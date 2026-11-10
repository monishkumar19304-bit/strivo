"""Shared plumbing for media-server adapters (Plex, Jellyfin).

A media server is one configured "device", but it can play on many screens at once. Each playback client
becomes its own source in history ("Bedroom TV · Jellyfin"), so a show watched in two rooms is two sessions.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request

from .base import IDLE, Adapter, AdapterError, Device, Emit, NowPlaying, Status


def client_device(server: Device, client_name: str, client_id: str = "") -> Device:
    slug = re.sub(r"[^a-z0-9]+", "-", (client_name or client_id or "player").lower()).strip("-")[:40] or "player"
    return Device(kind=server.kind, name=f"{client_name or 'Player'} · {server.name}", host=server.host,
                  port=server.port, identifier=client_id, model=server.model, os_version=server.os_version,
                  id=f"{server.id}--{slug}", extra={"server": server.id})


class MediaServerAdapter(Adapter):
    default_port = 0
    token_hint = ""

    def __init__(self, data=None, timeout: float = 8):
        super().__init__(data)
        self.timeout = timeout

    # ---- secrets: tokens live in secrets.json, keyed by device id
    def token(self, device: Device) -> str:
        if self.data is None:
            return device.extra.get("token", "")
        return self.data.secret(f"{self.kind}:{device.extra.get('server') or device.id}")

    def set_token(self, device: Device, token: str):
        if self.data is not None:
            self.data.set_secret(f"{self.kind}:{device.id}", token)
        else:
            device.extra["token"] = token

    def base_url(self, device: Device) -> str:
        host = device.host
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"http://{host}:{device.port or self.default_port}"

    def headers(self, device: Device) -> dict:
        return {"Accept": "application/json"}

    async def get_json(self, device: Device, path: str, token: str | None = None):
        url = self.base_url(device) + path
        hdrs = self.headers(device) if token is None else self.headers_for(token)

        def go():
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
                return json.loads(body) if body.strip() else {}
        try:
            return await asyncio.to_thread(go)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AdapterError(f"{device.name} refused the token ({e.code})") from e
            raise

    def headers_for(self, token: str) -> dict:
        raise NotImplementedError

    # ---- per-client sessions
    async def sessions(self, device: Device) -> list[tuple[Device, NowPlaying]]:
        raise NotImplementedError

    async def now_playing(self, device: Device) -> NowPlaying | None:
        s = await self.sessions(device)
        return s[0][1] if s else NowPlaying(state=IDLE)

    async def status(self, device: Device) -> Status:
        try:
            s = await self.sessions(device)
        except Exception:
            return Status(online=False)
        return Status(online=True, power="on", app=f"{len(s)} playing" if s else "")

    async def watch(self, device: Device, emit: Emit, interval: float = 10, stop: asyncio.Event | None = None):
        stop = stop or asyncio.Event()
        active: dict[str, Device] = {}
        while not stop.is_set():
            current = await asyncio.wait_for(self.sessions(device), max(15, interval * 2))
            emit(device, None)   # the server itself answered: mark it online
            seen = set()
            for cdev, np in current:
                seen.add(cdev.id)
                active[cdev.id] = cdev
                emit(cdev, np)
            for gone in [k for k in active if k not in seen]:
                emit(active.pop(gone), NowPlaying(state=IDLE))
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass
