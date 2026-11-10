"""Collector: listens to every configured box and appends "now playing" changes to events.jsonl.

Only changes are written (position ticks alone aren't), so the log stays small: a few MB a year.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import Future

from . import adapters as adapters_mod
from .adapters.base import Device, NowPlaying
from .config import DataDir

log = logging.getLogger(__name__)


def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Runtime:
    """One asyncio loop on a background thread. The (threaded) web server hands it adapter work."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="strivo-async", daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self):
        if not self.thread.is_alive():
            self.thread.start()
        return self

    def submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, coro, timeout: float = 60):
        return self.submit(coro).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


class EventLog:
    def __init__(self, data: DataDir):
        self.data = data
        self.path = data.file("events.jsonl")
        self.lock = threading.Lock()

    def write(self, row: dict):
        line = json.dumps(row, default=str) + "\n"
        with self.lock, open(self.path, "a") as f:
            f.write(line)

    def read(self) -> list[dict]:
        out = []
        try:
            with open(self.path) as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
        return out


class Collector:
    def __init__(self, data: DataDir, adapters: dict | None = None, log_events: bool = True):
        self.data = data
        self.events = EventLog(data)
        self.adapters = adapters if adapters is not None else adapters_mod.all_adapters(data)
        self.log_events = log_events
        self.last: dict[str, dict] = {}       # device id -> last row written (without position)
        self.live: dict[str, dict] = {}       # device id -> health info for the UI
        self.listeners = []                   # callables(device, np), e.g. the probe
        self.tasks: dict[str, asyncio.Task] = {}
        self.stop_event: asyncio.Event | None = None
        self._names = data.read("device_names.json", {}) or {}

    # ---- devices
    def devices(self) -> list[Device]:
        return [Device.from_dict(d) for d in self.data.config().get("devices", [])]

    def name_of(self, dev_id: str) -> str:
        for d in self.devices():
            if d.id == dev_id:
                return d.name
        return self._names.get(dev_id, dev_id)

    def _remember_name(self, device: Device):
        if self._names.get(device.id) != device.name:
            self._names[device.id] = device.name
            self.data.write("device_names.json", self._names)

    # ---- events
    def emit(self, device: Device, np: NowPlaying | None):
        """Called by adapters (from the event loop) for every now-playing update."""
        live = self.live.setdefault(device.id, {})
        live.update(name=device.name, kind=device.kind, online=True, error="", seen=time.time())
        if np is None:
            return
        row = np.to_event()
        live["now"] = row
        for fn in self.listeners:
            try:
                fn(device, np)
            except Exception:  # noqa: BLE001
                log.exception("listener failed")
        key = {k: v for k, v in row.items() if k != "pos"}
        if key == self.last.get(device.id):
            return
        self.last[device.id] = key
        if self.log_events:
            self._remember_name(device)
            self.events.write(dict(ts=now_ts(), dev=device.id, src=device.kind, kind="play", **row))

    def _status_event(self, device: Device, kind: str, **kw):
        if self.log_events:
            self.events.write(dict(ts=now_ts(), dev=device.id, src=device.kind, kind=kind, **kw))

    # ---- run loop
    async def _watch(self, device: Device):
        adapter = self.adapters.get(device.kind)
        if adapter is None:
            self.live[device.id] = dict(name=device.name, kind=device.kind, online=False,
                                        error=f"no adapter for {device.kind}", seen=0)
            return
        interval = float(self.data.config().get("poll_seconds", 10))
        backoff = 15
        while not self.stop_event.is_set():
            try:
                self._status_event(device, "connected")
                await adapter.watch(device, self.emit, interval, self.stop_event)
                backoff = 15
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                msg = str(e)[:200] or type(e).__name__
                live = self.live.setdefault(device.id, dict(name=device.name, kind=device.kind))
                was_online = live.get("online", None)
                live.update(online=False, error=msg)
                if was_online is not False:
                    self._status_event(device, "disconnected", err=msg)
                self.last.pop(device.id, None)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 120)

    async def start(self):
        self.stop_event = asyncio.Event()
        for d in self.devices():
            if d.enabled and d.id not in self.tasks:
                self.tasks[d.id] = asyncio.create_task(self._watch(d), name=f"watch-{d.id}")

    async def stop(self):
        if self.stop_event:
            self.stop_event.set()
        for t in self.tasks.values():
            t.cancel()
        for t in self.tasks.values():
            try:
                await t
            except BaseException:
                pass
        self.tasks.clear()

    async def reload(self):
        """Config changed (device added/removed/renamed): restart the watchers."""
        await self.stop()
        await self.start()

    def ingest(self, kind: str, payload: dict) -> int:
        """Webhook entry point (called from the web server thread)."""
        adapter = self.adapters.get(kind)
        if adapter is None:
            return 0
        pairs = adapter.handle_webhook(payload, self.devices())
        for dev, np in pairs:
            self.emit(dev, np)
        return len(pairs)
