"""Roku, via the External Control Protocol (ECP) on port 8060. EXPERIMENTAL (Tier 2).

ECP tells us the active channel and the player state/position for every channel, but a title only when the
channel shares one, which most don't. So Roku is "Play on TV" first; tracking counts only for channels that
report titles. `strivo probe` measures exactly that.

One-time setup on the Roku: Settings > System > Advanced system settings > Control by mobile apps >
Network access = "Permissive" (or "Enabled").
"""
from __future__ import annotations

import asyncio
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from ..apps import display_app
from .base import IDLE, OFF, PAUSED, PLAYING, Adapter, AdapterError, Device, NowPlaying, Prompt, Status

_STATE = {"play": PLAYING, "pause": PAUSED, "buffer": PLAYING, "open": PLAYING, "stop": IDLE, "close": IDLE,
          "none": IDLE, "startup": PLAYING}


def _ms(text: str | None) -> float | None:
    m = re.match(r"\s*(\d+)\s*ms", text or "")
    return int(m.group(1)) / 1000 if m else None


def parse_device_info(xml: str) -> dict:
    root = ET.fromstring(xml)
    g = lambda k: (root.findtext(k) or "").strip()  # noqa: E731
    return dict(name=g("user-device-name") or g("friendly-device-name") or g("default-device-name") or "Roku",
                model=" ".join(x for x in (g("vendor-name"), g("model-name")) if x) or "Roku",
                os_version=g("software-version"), serial=g("serial-number") or g("device-id"),
                power=g("power-mode"))


def parse_active_app(xml: str) -> tuple[str | None, str | None]:
    root = ET.fromstring(xml)
    app = root.find("app")
    if app is None or app.get("id") is None:  # the home screen has no id
        return None, None
    return (app.text or "").strip() or None, app.get("id")


def parse_media_player(xml: str) -> dict:
    root = ET.fromstring(xml)
    plugin = root.find("plugin")
    out = dict(state=_STATE.get(root.get("state", "none"), IDLE), position=_ms(root.findtext("position")),
               duration=_ms(root.findtext("duration")), live=(root.findtext("is_live") or "").strip() == "true",
               app=plugin.get("name") if plugin is not None else None,
               app_id=plugin.get("id") if plugin is not None else None, title=None, series=None, season=None,
               episode=None)
    # Some channels attach content metadata; read it when present.
    for tag in ("title", "content_title", "name"):
        v = root.findtext(tag) or (root.find("metadata").findtext(tag) if root.find("metadata") is not None else None)
        if v and v.strip():
            out["title"] = v.strip()
            break
    meta = root.find("metadata")
    if meta is not None:
        out["series"] = (meta.findtext("series") or meta.findtext("series_title") or "").strip() or None
        for k, dst in (("season", "season"), ("episode", "episode")):
            v = (meta.findtext(k) or "").strip()
            if v.isdigit():
                out[dst] = int(v)
    return out


class RokuAdapter(Adapter):
    kind = "roku"
    label = "Roku"
    experimental = True

    def __init__(self, data=None, timeout: float = 5):
        super().__init__(data)
        self.timeout = timeout

    def _url(self, device: Device, path: str) -> str:
        return f"http://{device.host}:{device.port or 8060}{path}"

    async def _get(self, device: Device, path: str) -> str:
        def go():
            with urllib.request.urlopen(self._url(device, path), timeout=self.timeout) as r:
                return r.read().decode("utf-8", "replace")
        return await asyncio.to_thread(go)

    async def _post(self, device: Device, path: str) -> int:
        def go():
            req = urllib.request.Request(self._url(device, path), data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status
        return await asyncio.to_thread(go)

    async def discover(self, timeout: float = 3) -> list[Device]:
        hosts = await asyncio.to_thread(ssdp_search, "roku:ecp", timeout)
        out = []
        for loc in hosts:
            u = urllib.parse.urlparse(loc)
            d = Device(kind=self.kind, name="Roku", host=u.hostname or "", port=u.port or 8060)
            try:  # only real ECP devices (other gadgets answer SSDP too)
                info = parse_device_info(await self._get(d, "/query/device-info"))
            except Exception:
                continue
            d.name, d.model, d.os_version, d.identifier = info["name"], info["model"], info["os_version"], info["serial"]
            d.id = ""
            d.__post_init__()
            out.append(d)
        return out

    async def pair(self, device: Device, prompt: Prompt) -> Device:
        try:
            info = parse_device_info(await self._get(device, "/query/device-info"))
        except Exception as e:
            raise AdapterError(f"Couldn't reach {device.name} on port 8060. On the Roku, set Settings > System > "
                               "Advanced system settings > Control by mobile apps to Permissive.") from e
        device.model, device.os_version, device.identifier = info["model"], info["os_version"], info["serial"]
        return device

    async def now_playing(self, device: Device) -> NowPlaying | None:
        info = parse_device_info(await self._get(device, "/query/device-info"))
        if info["power"] and info["power"] != "PowerOn":
            return NowPlaying(state=OFF)
        name, app_id = parse_active_app(await self._get(device, "/query/active-app"))
        mp = parse_media_player(await self._get(device, "/query/media-player"))
        if not app_id:
            return NowPlaying(state=IDLE)
        return NowPlaying(state=mp["state"], app=display_app(name, app_id), app_id=app_id, media="Video",
                          title=mp["title"], series=mp["series"], season=mp["season"], episode=mp["episode"],
                          position=mp["position"], duration=mp["duration"])

    async def status(self, device: Device) -> Status:
        try:
            info = parse_device_info(await self._get(device, "/query/device-info"))
            name, app_id = parse_active_app(await self._get(device, "/query/active-app"))
        except Exception:
            return Status(online=False)
        return Status(online=True, power="on" if info["power"] in ("PowerOn", "") else "off",
                      app=display_app(name, app_id) or "")

    async def launch(self, device: Device, app: str, deep_link: str | None = None) -> None:
        """app: a Roku channel id. deep_link: a contentId the channel understands (optional)."""
        if not re.fullmatch(r"[\w.-]+", app or ""):
            raise AdapterError("bad channel id")
        q = ""
        if deep_link:
            q = "?" + urllib.parse.urlencode({"contentId": deep_link, "mediaType": "episode"})
        try:
            await self._post(device, "/keypress/PowerOn")
        except Exception:
            pass  # not every Roku supports PowerOn; launching wakes most of them
        try:
            await self._post(device, f"/launch/{app}{q}")
        except Exception as e:
            raise AdapterError(f"{device.name} didn't open the channel") from e


def ssdp_search(st: str, timeout: float = 3) -> list[str]:
    """Return LOCATION headers for devices answering an SSDP M-SEARCH."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           f"ST: {st}\r\nMX: 2\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(timeout)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    out = []
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        while True:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m and m.group(1).decode() not in out:
                out.append(m.group(1).decode())
    except OSError:
        pass
    finally:
        s.close()
    return out
