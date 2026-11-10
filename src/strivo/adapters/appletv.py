"""Apple TV, via pyatv (https://github.com/postlund/pyatv).

Tracking uses the MRP-over-AirPlay "now playing" push stream; Play on TV uses the Companion protocol.
Pairing asks for two 4-digit codes shown on the TV (one per protocol). Credentials live in <data>/pyatv.json.
"""
from __future__ import annotations

import asyncio
import logging

from ..apps import display_app
from .base import (OFF, PAUSED, PLAYING, IDLE, Adapter, AdapterError, Device, Emit, NowPlaying, Prompt, Status)

log = logging.getLogger(__name__)

try:
    import pyatv
    from pyatv.const import DeviceState, PowerState, Protocol
    from pyatv.storage.file_storage import FileStorage
except ImportError:  # pragma: no cover - pyatv is a hard dependency, but keep the core importable without it
    pyatv = None

_STATE = {"Playing": PLAYING, "Paused": PAUSED, "Idle": IDLE, "Loading": PLAYING, "Seeking": PLAYING,
          "Stopped": IDLE}


def _enum_name(v) -> str | None:
    return str(v).split(".")[-1] if v is not None else None


def playing_to_np(p, app=None) -> NowPlaying:
    """Convert a pyatv Playing (+ App) into Strivo's NowPlaying."""
    state = _STATE.get(_enum_name(p.device_state), IDLE)
    return NowPlaying(
        state=state,
        app=display_app(getattr(app, "name", None), getattr(app, "identifier", None)),
        app_id=getattr(app, "identifier", None),
        media=_enum_name(p.media_type), title=p.title, series=p.series_name,
        season=p.season_number, episode=p.episode_number, artist=p.artist, album=p.album, genre=p.genre,
        position=p.position, duration=p.total_time)


class AppleTVAdapter(Adapter):
    kind = "appletv"
    label = "Apple TV"

    def __init__(self, data=None, storage_path: str | None = None):
        super().__init__(data)
        self.storage_path = storage_path   # default: <data>/pyatv.json
        self._storage = None
        self._conns: dict[str, object] = {}

    async def _store(self):
        if self._storage is None:
            loop = asyncio.get_running_loop()
            path = self.storage_path or (str(self.data.file("pyatv.json")) if self.data else None)
            self._storage = FileStorage(path, loop) if path else FileStorage.default_storage(loop)
            await self._storage.load()
        return self._storage

    async def _scan(self, device: Device | None = None, timeout: float = 5):
        loop = asyncio.get_running_loop()
        storage = await self._store()
        if device is None:
            return await pyatv.scan(loop, timeout=timeout, storage=storage)
        res = []
        if device.host:  # unicast first: works across most home networks and is fast
            res = await pyatv.scan(loop, hosts=[device.host], timeout=timeout, storage=storage)
        if not res and device.identifier:
            res = await pyatv.scan(loop, identifier=device.identifier, timeout=timeout, storage=storage)
        return res

    @staticmethod
    def _to_device(conf) -> Device:
        di = conf.device_info
        return Device(kind="appletv", name=conf.name, host=str(conf.address), identifier=conf.identifier or "",
                      model=getattr(di, "model_str", "") or "", os_version=str(getattr(di, "version", "") or ""))

    async def discover(self, timeout: float = 5) -> list[Device]:
        out = []
        for conf in await self._scan(None, timeout):
            di = conf.device_info
            if di and (_enum_name(di.operating_system) not in ("TvOS", "Unknown", None)
                       or "homepod" in (getattr(di, "model_str", "") or "").lower()):
                continue  # Macs, AirPort, HomePods: nothing to track
            if not any(s.protocol in (Protocol.MRP, Protocol.AirPlay, Protocol.Companion) for s in conf.services):
                continue
            out.append(self._to_device(conf))
        return out

    async def pair(self, device: Device, prompt: Prompt) -> Device:
        res = await self._scan(device, 8)
        if not res:
            raise AdapterError(f"Couldn't find {device.name}. Is it on and on the same network?")
        conf = res[0]
        storage = await self._store()
        loop = asyncio.get_running_loop()
        # tvOS 15+: now playing comes over AirPlay (MRP tunnel), app launching over Companion.
        for proto, why in ((Protocol.AirPlay, "now playing"), (Protocol.Companion, "Play on TV")):
            svc = conf.get_service(proto)
            if svc is None or svc.credentials:
                continue
            if svc.pairing.name == "NotSupported":
                continue
            handler = await pyatv.pair(conf, proto, loop, storage=storage, name="Strivo")
            try:
                await handler.begin()
                if handler.device_provides_pin:
                    pin = await prompt(f"Type the 4-digit code shown on {device.name} (for {why})")
                    handler.pin(int(pin))
                await handler.finish()
                if not handler.has_paired:
                    raise AdapterError("Pairing didn't finish. Try again and type the code quickly.")
            finally:
                await handler.close()
        await storage.save()
        d = self._to_device(conf)
        d.name, d.id = device.name, device.id
        return d

    async def _connect(self, device: Device):
        atv = self._conns.get(device.id)
        if atv is not None:
            return atv
        res = await self._scan(device, 8)
        if not res:
            raise AdapterError("not found")
        atv = await pyatv.connect(res[0], asyncio.get_running_loop(), storage=await self._store())
        self._conns[device.id] = atv
        return atv

    def _drop(self, device: Device):
        atv = self._conns.pop(device.id, None)
        if atv is not None:
            try:
                atv.close()
            except Exception:
                pass

    async def now_playing(self, device: Device) -> NowPlaying | None:
        try:
            atv = await self._connect(device)
            p = await atv.metadata.playing()
            try:
                app = atv.metadata.app
            except Exception:
                app = None
            np = playing_to_np(p, app)
            try:
                if atv.power.power_state == PowerState.Off:
                    np.state = OFF
            except Exception:
                pass
            return np
        except Exception:
            self._drop(device)
            raise

    async def watch(self, device: Device, emit: Emit, interval: float = 10, stop: asyncio.Event | None = None):
        """Push-based: pyatv calls us on every change, so nothing is missed between polls."""
        stop = stop or asyncio.Event()
        atv = await self._connect(device)
        lost = asyncio.Event()

        class Listener(pyatv.interface.PushListener, pyatv.interface.PowerListener,
                       pyatv.interface.DeviceListener):
            def playstatus_update(self, updater, playstatus):
                try:
                    app = atv.metadata.app
                except Exception:
                    app = None
                emit(device, playing_to_np(playstatus, app))

            def playstatus_error(self, updater, exception):
                log.debug("push error on %s: %s", device.name, exception)

            def powerstate_update(self, old_state, new_state):
                if new_state == PowerState.Off:
                    emit(device, NowPlaying(state=OFF))

            def connection_lost(self, exception):
                lost.set()

            def connection_closed(self):
                lost.set()

        listener = Listener()
        atv.listener = listener
        atv.push_updater.listener = listener
        atv.push_updater.start()
        try:
            atv.power.listener = listener
        except Exception:
            pass
        try:
            while not stop.is_set() and not lost.is_set():
                waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(lost.wait())]
                done, pending = await asyncio.wait(waiters, timeout=30, return_when=asyncio.FIRST_COMPLETED)
                for w in pending:
                    w.cancel()
                if not done:
                    atv.metadata.app  # raises if the connection died quietly
            if lost.is_set():
                raise AdapterError("connection lost")
        finally:
            self._drop(device)

    async def launch(self, device: Device, app: str, deep_link: str | None = None) -> None:
        """app: a tvOS bundle id. deep_link: a URL the app understands (e.g. a YouTube video URL)."""
        target = deep_link or app
        last = None
        for _ in range(3):  # a sleeping Apple TV can take a few seconds to answer
            try:
                atv = await self._connect(device)
                await atv.apps.launch_app(target)
                return
            except Exception as e:  # noqa: BLE001
                last = e
                self._drop(device)
                await asyncio.sleep(3)
        raise AdapterError(f"{device.name} didn't open the app ({type(last).__name__})")

    async def status(self, device: Device) -> Status:
        try:
            atv = await asyncio.wait_for(self._connect(device), 10)
        except Exception:
            return Status(online=False)
        st = Status(online=True)
        try:
            st.power = "on" if atv.power.power_state == PowerState.On else "off"
        except Exception:
            st.power = "unknown"
        try:
            a = atv.metadata.app
            st.app = display_app(a.name, a.identifier) or "" if a else ""
        except Exception:
            pass
        return st

    async def close(self):
        for k in list(self._conns):
            atv = self._conns.pop(k)
            try:
                atv.close()
            except Exception:
                pass
