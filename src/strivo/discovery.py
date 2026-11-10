"""Small mDNS (Bonjour) helpers shared by adapters, plus the strivo.local name."""
from __future__ import annotations

import asyncio
import logging
import socket

log = logging.getLogger(__name__)


async def browse(service_types: list[str], timeout: float = 4) -> list[dict]:
    """Browse mDNS for the given service types; return [{type, name, host, port, props}]."""
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except ImportError:  # pragma: no cover
        return []
    found: dict[tuple, None] = {}

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            found[(service_type, name)] = None

    azc = AsyncZeroconf()
    browser = AsyncServiceBrowser(azc.zeroconf, service_types, handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
        out = []
        for (stype, name) in list(found):
            info = AsyncServiceInfo(stype, name)
            if not await info.async_request(azc.zeroconf, 2000):
                continue
            addrs = info.parsed_addresses()
            props = {(k.decode(errors="ignore") if isinstance(k, bytes) else k):
                     (v.decode(errors="ignore") if isinstance(v, bytes) else v) for k, v in (info.properties or {}).items()}
            out.append(dict(type=stype, name=name.replace("." + stype, ""), host=addrs[0] if addrs else "",
                            port=info.port, props=props))
        return out
    finally:
        await browser.async_cancel()
        await azc.async_close()


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class LocalName:
    """Advertise http://strivo.local:<port> on the home network (mDNS A record + _http._tcp service)."""

    def __init__(self, port: int, hostname: str = "strivo"):
        self.port, self.hostname, self.zc, self.info = port, hostname, None, None

    def start(self):
        try:
            from zeroconf import ServiceInfo, Zeroconf
            ip = local_ip()
            self.zc = Zeroconf()
            self.info = ServiceInfo("_http._tcp.local.", f"Strivo._http._tcp.local.",
                                    addresses=[socket.inet_aton(ip)], port=self.port,
                                    properties={"path": "/"}, server=f"{self.hostname}.local.")
            self.zc.register_service(self.info, allow_name_change=True)
            log.info("advertising http://%s.local:%s", self.hostname, self.port)
        except Exception as e:  # noqa: BLE001 - never block startup on mDNS
            log.warning("couldn't advertise %s.local: %s", self.hostname, e)

    def stop(self):
        if self.zc:
            try:
                self.zc.unregister_service(self.info)
                self.zc.close()
            except Exception:
                pass
