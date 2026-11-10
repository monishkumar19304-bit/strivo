"""Jellyfin (and Emby, which shares the API). Exact title, episode and position for every client.

Two ways in, use either or both:
- **API polling** (default): Strivo asks the server's /Sessions endpoint every few seconds. Needs an API key
  (Dashboard > API Keys > +).
- **Webhooks** (optional, instant): install the free Webhook plugin, add a "Generic" destination pointing to
  http://<strivo>:<port>/api/webhook/jellyfin with the template shown in the setup wizard.
"""
from __future__ import annotations

from .base import IDLE, PAUSED, PLAYING, AdapterError, Device, NowPlaying, Prompt
from .mediaserver import MediaServerAdapter, client_device

TICKS = 10_000_000  # Jellyfin positions are in 100 ns ticks

# Paste into the Webhook plugin's "Generic" destination (Template field).
WEBHOOK_TEMPLATE = """{"NotificationType":"{{NotificationType}}","ServerId":"{{ServerId}}","DeviceId":"{{DeviceId}}",\
"DeviceName":"{{DeviceName}}","ClientName":"{{ClientName}}","ItemType":"{{ItemType}}","Name":"{{Name}}",\
"SeriesName":"{{SeriesName}}","SeasonNumber":"{{SeasonNumber}}","EpisodeNumber":"{{EpisodeNumber}}",\
"PlaybackPositionTicks":"{{PlaybackPositionTicks}}","RunTimeTicks":"{{RunTimeTicks}}","IsPaused":"{{IsPaused}}",\
"Provider_tmdb":"{{Provider_tmdb}}"}"""


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def item_to_np(item: dict, play_state: dict | None, client: str) -> NowPlaying:
    play_state = play_state or {}
    typ = item.get("Type")
    paused = play_state.get("IsPaused") in (True, "True", "true")
    pos = play_state.get("PositionTicks")
    rt = item.get("RunTimeTicks")
    np = NowPlaying(state=PAUSED if paused else PLAYING, app="Jellyfin", app_id=client or "jellyfin",
                    media="Video" if typ in ("Episode", "Movie", "Video", "MusicVideo", "TvChannel") else typ,
                    title=item.get("Name"), position=(int(pos) / TICKS) if pos not in (None, "") else None,
                    duration=(int(rt) / TICKS) if rt not in (None, "") else None)
    if typ == "Episode":
        np.series = item.get("SeriesName") or None
        np.season = _int(item.get("ParentIndexNumber", item.get("SeasonNumber")))
        np.episode = _int(item.get("IndexNumber", item.get("EpisodeNumber")))
    elif typ == "TvChannel":
        np.artist = item.get("Name")
    return np


class JellyfinAdapter(MediaServerAdapter):
    kind = "jellyfin"
    label = "Jellyfin / Emby"
    default_port = 8096
    token_hint = "an API key (Dashboard > API Keys)"

    def headers_for(self, token: str) -> dict:
        return {"Accept": "application/json",
                "Authorization": f'MediaBrowser Client="Strivo", Device="Strivo", DeviceId="strivo", '
                                 f'Version="0.1", Token="{token}"'}

    def headers(self, device: Device) -> dict:
        return self.headers_for(self.token(device))

    async def discover(self, timeout: float = 3) -> list[Device]:
        import asyncio
        found = await asyncio.to_thread(udp_discover, timeout)
        return [Device(kind=self.kind, name=f.get("Name") or "Jellyfin", host=f["host"], port=f["port"],
                       identifier=f.get("Id", ""), model="Jellyfin server") for f in found]

    async def pair(self, device: Device, prompt: Prompt) -> Device:
        token = self.token(device) or (await prompt(f"Paste {self.token_hint} from {device.name}")).strip()
        if not token:
            raise AdapterError("An API key is needed.")
        info = await self.get_json(device, "/System/Info", token=token)
        device.identifier = info.get("Id", device.identifier)
        device.model = f"{info.get('ProductName') or 'Jellyfin Server'}"
        device.os_version = info.get("Version", "")
        self.set_token(device, token)
        return device

    async def sessions(self, device: Device) -> list[tuple[Device, NowPlaying]]:
        data = await self.get_json(device, "/Sessions?ActiveWithinSeconds=120")
        out = []
        for s in data if isinstance(data, list) else []:
            item = s.get("NowPlayingItem")
            if not item:
                continue
            client = s.get("Client") or ""
            cdev = client_device(device, s.get("DeviceName") or client, s.get("DeviceId") or "")
            out.append((cdev, item_to_np(item, s.get("PlayState"), client)))
        return out

    def handle_webhook(self, payload: dict, devices: list[Device]):
        """Jellyfin Webhook plugin, Generic destination with WEBHOOK_TEMPLATE."""
        server = next((d for d in devices if d.kind == self.kind and d.identifier and
                       d.identifier == payload.get("ServerId")), None)
        server = server or next((d for d in devices if d.kind == self.kind), None)
        if server is None:
            return []
        kind = payload.get("NotificationType", "")
        cdev = client_device(server, payload.get("DeviceName") or payload.get("ClientName") or "",
                             payload.get("DeviceId") or "")
        if kind == "PlaybackStop":
            return [(cdev, NowPlaying(state=IDLE))]
        if kind not in ("PlaybackStart", "PlaybackProgress"):
            return []
        item = dict(Type=payload.get("ItemType"), Name=payload.get("Name"), SeriesName=payload.get("SeriesName"),
                    SeasonNumber=payload.get("SeasonNumber"), EpisodeNumber=payload.get("EpisodeNumber"),
                    RunTimeTicks=payload.get("RunTimeTicks") or None)
        ps = dict(PositionTicks=payload.get("PlaybackPositionTicks") or None, IsPaused=payload.get("IsPaused"))
        return [(cdev, item_to_np(item, ps, payload.get("ClientName") or ""))]


def udp_discover(timeout: float = 3) -> list[dict]:
    """Jellyfin answers "who is JellyfinServer?" on UDP 7359 with its name, id and address."""
    import json
    import socket
    import urllib.parse
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    out = []
    try:
        s.sendto(b"who is JellyfinServer?", ("255.255.255.255", 7359))
        while True:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                break
            try:
                j = json.loads(data)
            except ValueError:
                continue
            u = urllib.parse.urlparse(j.get("Address") or f"http://{addr[0]}:8096")
            j["host"], j["port"] = u.hostname or addr[0], u.port or 8096
            if not any(o.get("Id") == j.get("Id") for o in out):
                out.append(j)
    except OSError:
        pass
    finally:
        s.close()
    return out
