"""The one interface every platform implements. See docs/ARCHITECTURE.md#device-adapters."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

# prompt(message, need_input=True) -> the user's answer (a PIN). With need_input=False it only shows the
# message ("Press Allow on the TV") and returns at once. The web wizard and the CLI each supply one.
Prompt = Callable[..., Awaitable[str]]
# emit(device, now_playing_or_None, event_kind) -> None; used by push-style adapters.
Emit = Callable[["Device", Optional["NowPlaying"]], None]

PLAYING, PAUSED, IDLE, OFF = "Playing", "Paused", "Idle", "Off"


@dataclass
class Device:
    kind: str                      # adapter kind, e.g. "appletv"
    name: str                      # what the household calls it ("Living Room")
    host: str = ""                 # address on the home network (never leaves the house)
    port: int | None = None
    identifier: str = ""           # stable id the platform uses (Apple TV id, serial, server id)
    model: str = ""                # e.g. "Apple TV 4K (gen 3)"; goes into probe reports
    os_version: str = ""
    id: str = ""                   # Strivo's own id for this device (slug), used in history
    enabled: bool = True
    platform: str = ""             # for android: "android" (Google TV / Android TV) or "firetv"
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            base = re.sub(r"[^a-z0-9]+", "-", (self.name or self.kind).lower()).strip("-") or self.kind
            self.id = base
        if self.port is not None:
            self.port = int(self.port)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class NowPlaying:
    """Whatever the box reports. Missing fields stay None; the core does the rest."""
    state: str = IDLE              # Playing / Paused / Idle / Off
    app: str | None = None         # app name as reported ("Hulu")
    app_id: str | None = None      # bundle id / package / channel id
    media: str | None = None       # Video / Music / TV / Unknown
    title: str | None = None
    series: str | None = None
    season: int | None = None
    episode: int | None = None
    artist: str | None = None      # many apps put the show name or channel here
    album: str | None = None
    genre: str | None = None
    position: float | None = None  # seconds
    duration: float | None = None  # seconds

    def to_event(self) -> dict:
        return dict(app=self.app, app_id=self.app_id, state=self.state, media=self.media, title=self.title,
                    series=self.series, season=self.season, episode=self.episode, artist=self.artist,
                    album=self.album, genre=self.genre, pos=self.position, total=self.duration)


@dataclass
class Status:
    online: bool = False
    power: str = "unknown"         # on / off / unknown
    app: str = ""


class AdapterError(Exception):
    """A message that is safe to show the user."""


class Adapter:
    """Base class. Subclasses set `kind`/`label` and implement the four calls.

    Adapters are polled by default: the collector calls `now_playing()` every few seconds. An adapter whose
    platform pushes updates (Apple TV) overrides `watch()` instead. Server adapters (Plex, Jellyfin) can also
    receive webhooks through `handle_webhook()`.
    """

    kind = "base"
    label = "Base"
    tracking = True               # False for "Play on TV only" platforms
    experimental = False

    def __init__(self, data=None):
        self.data = data          # strivo.config.DataDir (None in some tests)

    async def discover(self, timeout: float = 5) -> list[Device]:
        return []

    async def pair(self, device: Device, prompt: Prompt) -> Device:
        """Make the device usable (credentials, keys). Returns the device, possibly with more details filled in."""
        return device

    async def now_playing(self, device: Device) -> NowPlaying | None:
        raise NotImplementedError

    async def launch(self, device: Device, app: str, deep_link: str | None = None) -> None:
        raise AdapterError(f"{self.label} can't open apps yet")

    async def status(self, device: Device) -> Status:
        try:
            np = await asyncio.wait_for(self.now_playing(device), 10)
        except Exception:
            return Status(online=False)
        st = Status(online=True, power="off" if np and np.state == OFF else "on")
        if np and np.app:
            st.app = np.app
        return st

    async def watch(self, device: Device, emit: Emit, interval: float = 10, stop: asyncio.Event | None = None):
        """Default: poll now_playing(). Raises on connection failure so the collector can back off."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            np = await asyncio.wait_for(self.now_playing(device), max(15, interval * 2))
            emit(device, np)
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        pass

    def handle_webhook(self, payload: dict, devices: list[Device]) -> list[tuple[Device, NowPlaying | None]]:
        return []


def new_id() -> str:
    return uuid.uuid4().hex[:8]
