"""Plex Media Server. Exact title, episode and position for every Plex client.

- **API polling** (default): Strivo reads /status/sessions every few seconds. Needs your server's X-Plex-Token
  (https://support.plex.tv/articles/204059436). Works without Plex Pass.
- **Webhooks** (optional, instant, needs Plex Pass): Plex Web > Settings > Webhooks > add
  http://<strivo>:<port>/api/webhook/plex
"""
from __future__ import annotations

from .base import IDLE, PAUSED, PLAYING, AdapterError, Device, NowPlaying, Prompt
from .mediaserver import MediaServerAdapter, client_device

_STATE = {"playing": PLAYING, "paused": PAUSED, "buffering": PLAYING, "stopped": IDLE}
_EVENT = {"media.play": PLAYING, "media.resume": PLAYING, "media.pause": PAUSED, "media.stop": IDLE,
          "media.scrobble": PLAYING}


def meta_to_np(m: dict, state: str) -> NowPlaying:
    typ = m.get("type")
    off, dur = m.get("viewOffset"), m.get("duration")
    np = NowPlaying(state=state, app="Plex", app_id=((m.get("Player") or {}).get("product") or "plex"),
                    media="Video" if typ in ("episode", "movie", "clip") else typ, title=m.get("title"),
                    position=int(off) / 1000 if off not in (None, "") else None,
                    duration=int(dur) / 1000 if dur not in (None, "") else None)
    if typ == "episode":
        np.series = m.get("grandparentTitle")
        try:
            np.season = int(m.get("parentIndex")) if m.get("parentIndex") is not None else None
            np.episode = int(m.get("index")) if m.get("index") is not None else None
        except (TypeError, ValueError):
            pass
    return np


class PlexAdapter(MediaServerAdapter):
    kind = "plex"
    label = "Plex"
    default_port = 32400
    token_hint = "your X-Plex-Token (support.plex.tv/articles/204059436)"

    def headers_for(self, token: str) -> dict:
        h = {"Accept": "application/json", "X-Plex-Product": "Strivo", "X-Plex-Client-Identifier": "strivo"}
        if token:
            h["X-Plex-Token"] = token
        return h

    def headers(self, device: Device) -> dict:
        return self.headers_for(self.token(device))

    async def discover(self, timeout: float = 3) -> list[Device]:
        import asyncio
        return [Device(kind=self.kind, name=r["name"], host=r["host"], port=r["port"], identifier=r["id"],
                       model="Plex Media Server") for r in await asyncio.to_thread(gdm_discover, timeout)]

    async def pair(self, device: Device, prompt: Prompt) -> Device:
        ident = await self.get_json(device, "/identity", token="")
        mc = ident.get("MediaContainer", ident)
        device.identifier = mc.get("machineIdentifier", device.identifier)
        device.model, device.os_version = "Plex Media Server", mc.get("version", "")
        token = self.token(device)
        try:  # a server that allows this network without auth needs no token
            await self.get_json(device, "/status/sessions", token=token)
        except AdapterError:
            token = (await prompt(f"Paste {self.token_hint}")).strip()
            if not token:
                raise AdapterError("A Plex token is needed.")
            await self.get_json(device, "/status/sessions", token=token)
        self.set_token(device, token)
        return device

    async def sessions(self, device: Device) -> list[tuple[Device, NowPlaying]]:
        data = await self.get_json(device, "/status/sessions")
        out = []
        for m in (data.get("MediaContainer") or {}).get("Metadata") or []:
            player = m.get("Player") or {}
            cdev = client_device(device, player.get("title") or player.get("product") or "",
                                 player.get("machineIdentifier") or "")
            out.append((cdev, meta_to_np(m, _STATE.get(player.get("state", "playing"), PLAYING))))
        return out

    def handle_webhook(self, payload: dict, devices: list[Device]):
        server = next((d for d in devices if d.kind == self.kind and d.identifier and
                       d.identifier == (payload.get("Server") or {}).get("uuid")), None)
        server = server or next((d for d in devices if d.kind == self.kind), None)
        ev = payload.get("event", "")
        if server is None or ev not in _EVENT:
            return []
        player = payload.get("Player") or {}
        cdev = client_device(server, player.get("title") or "", player.get("uuid") or "")
        if _EVENT[ev] == IDLE:
            return [(cdev, NowPlaying(state=IDLE))]
        return [(cdev, meta_to_np(payload.get("Metadata") or {}, _EVENT[ev]))]


def gdm_discover(timeout: float = 3) -> list[dict]:
    """Plex "G'Day Mate" discovery on UDP 32414."""
    import re
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    out = []
    try:
        s.sendto(b"M-SEARCH * HTTP/1.1\r\n\r\n", ("255.255.255.255", 32414))
        while True:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                break
            txt = data.decode(errors="ignore")
            g = lambda k: (re.search(rf"(?im)^{k}:\s*(.+?)\s*$", txt) or [None, ""])[1]  # noqa: E731
            r = dict(name=g("Name") or "Plex", host=addr[0], port=int(g("Port") or 32400), id=g("Resource-Identifier"))
            if not any(o["id"] == r["id"] for o in out):
                out.append(r)
    except OSError:
        pass
    finally:
        s.close()
    return out
