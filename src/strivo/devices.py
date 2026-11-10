"""Device management shared by the setup wizard, Settings, Play on TV and the CLI."""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from .adapters.base import AdapterError, Device
from . import rules as _rules
from .apps import launch_id, services, trailer

log = logging.getLogger(__name__)

TV_KINDS = ("appletv", "android", "roku")      # boxes that can open apps ("Play on TV")


def platform_of(d: Device) -> str:
    if d.kind == "android":
        return "firetv" if d.platform == "firetv" else "android"
    return d.kind


class Pairing:
    """One pairing conversation between the wizard and a box (PIN codes, "press Allow")."""

    def __init__(self, device: Device):
        self.id = uuid.uuid4().hex[:10]
        self.device = device
        self.state = "running"       # running / need_input / done / error
        self.message = "Connecting…"
        self.error = ""
        self.started = time.time()
        self._answer: asyncio.Future | None = None

    async def prompt(self, message: str, need_input: bool = True) -> str:
        self.message = message
        if not need_input:
            return ""
        self._answer = asyncio.get_running_loop().create_future()
        self.state = "need_input"
        try:
            return await asyncio.wait_for(self._answer, 300)
        finally:
            self.state = "running" if self.state == "need_input" else self.state

    def answer(self, value: str):
        if self._answer and not self._answer.done():
            self._answer.get_loop().call_soon_threadsafe(self._answer.set_result, value)

    def view(self) -> dict:
        return dict(id=self.id, state=self.state, message=self.message, error=self.error,
                    device=dict(id=self.device.id, name=self.device.name, kind=self.device.kind))


class DeviceManager:
    def __init__(self, data, collector, runtime):
        self.data, self.collector, self.runtime = data, collector, runtime
        self.pairings: dict[str, Pairing] = {}

    @property
    def adapters(self):
        return self.collector.adapters

    def devices(self) -> list[Device]:
        return self.collector.devices()

    def get(self, dev_id: str) -> Device | None:
        return next((d for d in self.devices() if d.id == dev_id), None)

    # ---- discovery
    async def discover(self, timeout: float = 5) -> list[dict]:
        kinds = list(self.adapters)

        async def one(k):
            try:
                return await asyncio.wait_for(self.adapters[k].discover(timeout), timeout + 6)
            except Exception as e:  # noqa: BLE001
                log.info("discovery for %s failed: %s", k, e)
                return []
        results = await asyncio.gather(*(one(k) for k in kinds))
        have = {(d.kind, d.identifier or d.host) for d in self.devices()}
        out = []
        for k, found in zip(kinds, results):
            for d in found:
                v = d.to_dict()
                v["added"] = (d.kind, d.identifier or d.host) in have
                v["label"] = self.adapters[k].label
                v["experimental"] = self.adapters[k].experimental
                out.append(v)
        return out

    # ---- pairing
    def start_pairing(self, spec: dict) -> Pairing:
        kind = spec.get("kind")
        if kind not in self.adapters:
            raise AdapterError("unknown device type")
        d = Device.from_dict(dict(spec, id=""))
        d.id = self._unique_id(d.id)
        p = Pairing(d)
        self.pairings[p.id] = p
        self.runtime.submit(self._pair(p))
        return p

    def _unique_id(self, base: str) -> str:
        taken = {x.id for x in self.devices()}
        if base not in taken:
            return base
        i = 2
        while f"{base}-{i}" in taken:
            i += 1
        return f"{base}-{i}"

    async def _pair(self, p: Pairing):
        try:
            dev = await self.adapters[p.device.kind].pair(p.device, p.prompt)
            self.save_device(dev)
            p.state, p.message = "done", f"{dev.name} is ready."
            await self.collector.reload()
        except Exception as e:  # noqa: BLE001
            p.state = "error"
            p.error = str(e) if isinstance(e, AdapterError) else f"{type(e).__name__}: {e}"[:300]
            log.info("pairing %s failed: %s", p.device.name, p.error)

    def save_device(self, dev: Device):
        def fn(c):
            devs = [x for x in c.get("devices", []) if x.get("id") != dev.id]
            devs.append(dev.to_dict())
            c["devices"] = devs
        self.data.update_config(fn)

    def update_device(self, dev_id: str, name: str | None = None, enabled: bool | None = None,
                      remove: bool = False) -> bool:
        found = False

        def fn(c):
            nonlocal found
            out = []
            for x in c.get("devices", []):
                if x.get("id") == dev_id:
                    found = True
                    if remove:
                        continue
                    if name:
                        x["name"] = str(name)[:60]
                    if enabled is not None:
                        x["enabled"] = bool(enabled)
                out.append(x)
            c["devices"] = out
            if remove and c.get("default_tv") == dev_id:
                c["default_tv"] = ""
        self.data.update_config(fn)
        if found:
            self.runtime.submit(self.collector.reload())
        return found

    # ---- Play on TV
    async def tv_list(self) -> list[dict]:
        tvs = [d for d in self.devices() if d.enabled and d.kind in TV_KINDS and d.kind in self.adapters]

        async def one(d):
            try:
                st = await asyncio.wait_for(self.adapters[d.kind].status(d), 12)
            except Exception:
                from .adapters.base import Status
                st = Status(online=False)
            return dict(id=d.id, name=d.name, kind=d.kind, online=st.online, power=st.power, app=st.app)
        return list(await asyncio.gather(*(one(d) for d in tvs)))

    async def tv_play(self, dev_id: str, provider: str = "", trailer_id: str = "") -> str:
        d = self.get(dev_id)
        if d is None or d.kind not in TV_KINDS:
            raise AdapterError("unknown TV")
        plat = platform_of(d)
        ad = self.adapters[d.kind]
        if trailer_id:
            if not re.fullmatch(r"[\w-]{6,20}", trailer_id):
                raise AdapterError("bad trailer id")
            t = trailer(plat, trailer_id)
            if not t:
                raise AdapterError("trailers aren't supported on this kind of box")
            app, link = t
            await ad.launch(d, app, link)
            return "trailer"
        provider = _rules.current().play_as(provider)
        if provider not in services():
            raise AdapterError("unknown service")
        app = launch_id(provider, plat)
        if not app:
            raise AdapterError(f"{provider} isn't available on this kind of box")
        await ad.launch(d, app)
        return provider
