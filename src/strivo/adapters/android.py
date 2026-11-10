"""Google TV, Android TV and Fire TV, via ADB over the home network (adb-shell, pure Python).

One-time setup on the TV: Settings > Developer options > turn on "Network debugging" (Android TV / Fire TV:
"ADB debugging"), then press **Allow** when the TV asks whether to trust Strivo.

What's playing comes from Android's media sessions (`dumpsys media_session`): every app that shows a "now
playing" card or responds to the remote's play/pause keys publishes one, with the title and usually a subtitle.
The foreground app comes from the activity manager. "Play on TV" starts the app's TV launcher activity, or an
Android VIEW intent for a deep link.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shlex

from ..apps import display_app, service_for
from .base import IDLE, OFF, PAUSED, PLAYING, Adapter, AdapterError, Device, NowPlaying, Prompt, Status

log = logging.getLogger(__name__)

SEP = "---STRIVO-SEP---"
# One round trip per poll: media sessions, the foreground activity, and whether the screen is awake.
POLL_CMD = (f"dumpsys media_session; echo {SEP}; "
            "dumpsys activity activities | grep -E 'ResumedActivity|mFocusedApp'; echo " + SEP + "; "
            "dumpsys power | grep -E 'mWakefulness=|Display Power: state='; echo " + SEP + "; cat /proc/uptime")
INFO_CMD = ("getprop ro.product.manufacturer; getprop ro.product.model; getprop ro.build.version.release; "
            "getprop ro.serialno; getprop ro.build.characteristics")

# android.media.session.PlaybackState constants
_PB = {0: IDLE, 1: IDLE, 2: PAUSED, 3: PLAYING, 4: PLAYING, 5: PLAYING, 6: PLAYING, 7: IDLE, 8: PLAYING,
       9: PLAYING, 10: PLAYING, 11: PLAYING}
_PB_RANK = {3: 0, 6: 1, 8: 1, 4: 1, 5: 1, 2: 2}


def _null(s):
    s = (s or "").strip()
    return None if s in ("", "null") else s


def parse_description(desc: str) -> tuple[str | None, str | None, str | None]:
    """MediaDescription.toString() is "title, subtitle, description". Titles can contain commas, so:
    when the trailing fields are "null" we can split from the right exactly; otherwise assume the
    description (synopsis) is the part most likely to contain commas."""
    parts = desc.split(", ")
    if len(parts) <= 3:
        parts += ["null"] * (3 - len(parts))
        return _null(parts[0]), _null(parts[1]), _null(parts[2])
    if parts[-1] == "null":
        t, s, d = desc.rsplit(", ", 2)
        return _null(t), _null(s), None
    return _null(parts[0]), _null(parts[1]), _null(", ".join(parts[2:]))


_HEADER = re.compile(r"^(?:(.+?) )?([\w.]+)/(.+?) \(userId=\d+\)$")
_JUNK_SUB = {"unknown album", "unknown artist", "<unknown>", "unknown"}


def parse_media_sessions(text: str, uptime_ms: int | None = None) -> list[dict]:
    """Parse `dumpsys media_session` into [{package, active, state, position, title, subtitle, description}].

    Handles both formats seen in the wild: `state=3` (Android 9-11) and `state=PLAYING(3)` (Android 12+).
    `position` is where playback was at `updated` (ms since boot); with uptime_ms we move it forward to now.
    """
    sessions, cur = [], None
    for line in text.splitlines():
        s = line.strip()
        m = _HEADER.match(s)
        if m and not s.startswith("Media button session") and line.startswith("    ") and not line.startswith("     "):
            cur = dict(package=m.group(2), tag=m.group(3), active=False, state=None, position=None, speed=0.0,
                       updated=None, title=None, subtitle=None, description=None, metadata_size=0)
            sessions.append(cur)
            continue
        if cur is None:
            continue
        if s.startswith("package="):
            cur["package"] = s.split("=", 1)[1].strip()
        elif s.startswith("active="):
            cur["active"] = s.split("=", 1)[1].strip() == "true"
        elif s.startswith("state=PlaybackState"):
            m = re.search(r"\{state=(?:\w+\()?(\d+)\)?, position=(-?\d+)", s)
            if m:
                cur["state"], cur["position"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"speed=(-?[\d.]+)", s)
            cur["speed"] = float(m.group(1)) if m else 0.0
            m = re.search(r"updated=(\d+)", s)
            cur["updated"] = int(m.group(1)) if m else None
        elif s.startswith("metadata:"):
            m = re.match(r"metadata:\s*size=(\d+), description=(.*)$", s)
            if m:
                cur["metadata_size"] = int(m.group(1))
                cur["title"], cur["subtitle"], cur["description"] = parse_description(m.group(2))
                if (cur["subtitle"] or "").lower() in _JUNK_SUB:
                    cur["subtitle"] = None
    for x in sessions:
        if uptime_ms and x["state"] == 3 and x["position"] is not None and x["updated"] and x["speed"]:
            x["position"] = int(x["position"] + max(0, uptime_ms - x["updated"]) * x["speed"])
    return [x for x in sessions if x["package"]]


def parse_foreground(text: str) -> str | None:
    for line in text.splitlines():
        m = re.search(r"(?:topResumedActivity|mResumedActivity|ResumedActivity|mFocusedApp)[:=].*?\s([\w.]+)/", line)
        if m:
            return m.group(1)
    return None


def parse_power(text: str) -> str:
    if re.search(r"mWakefulness=(Asleep|Dozing)", text) or "Display Power: state=OFF" in text:
        return "off"
    if "mWakefulness=Awake" in text or "Display Power: state=ON" in text:
        return "on"
    return "unknown"


def pick_session(sessions: list[dict], foreground: str | None) -> dict | None:
    """The session that represents what's on screen: playing beats paused; the foreground app breaks ties."""
    live = [s for s in sessions if s["state"] in _PB_RANK and (s["title"] or s["state"] in (3, 2))]
    if not live:
        return None
    live.sort(key=lambda s: (_PB_RANK[s["state"]], s["package"] != foreground, not s["active"]))
    return live[0]


def poll_to_np(output: str) -> NowPlaying:
    parts = output.split(SEP)
    uptime_ms = None
    if len(parts) > 3:
        try:
            uptime_ms = int(float(parts[3].split()[0]) * 1000)
        except (ValueError, IndexError):
            pass
    sessions = parse_media_sessions(parts[0], uptime_ms)
    fg = parse_foreground(parts[1]) if len(parts) > 1 else None
    power = parse_power(parts[2]) if len(parts) > 2 else "unknown"
    if power == "off":
        return NowPlaying(state=OFF)
    s = pick_session(sessions, fg)
    if s is None:
        pkg = fg
        return NowPlaying(state=IDLE, app=display_app(None, pkg), app_id=pkg)
    pos = s["position"] / 1000 if s["position"] is not None and s["position"] >= 0 else None
    title, sub = s["title"], s["subtitle"]
    return NowPlaying(state=_PB.get(s["state"], IDLE), app=display_app(None, s["package"]), app_id=s["package"],
                      media="Video", title=title, artist=sub, album=s["description"], position=pos)


class AndroidAdapter(Adapter):
    kind = "android"
    label = "Google TV / Android TV / Fire TV"

    def __init__(self, data=None, connect_timeout: float = 10):
        super().__init__(data)
        self.connect_timeout = connect_timeout
        self._conns: dict[str, object] = {}
        self._signer = None

    # ---- identity
    def _keys(self):
        if self._signer is None:
            from adb_shell.auth.keygen import keygen
            from adb_shell.auth.sign_pythonrsa import PythonRSASigner
            path = str(self.data.file("adbkey")) if self.data else None
            if path is None:
                raise AdapterError("no data folder for the ADB key")
            import os
            if not os.path.exists(path):
                keygen(path)
                os.chmod(path, 0o600)
            with open(path) as f:
                priv = f.read()
            with open(path + ".pub") as f:
                pub = f.read()
            self._signer = PythonRSASigner(pub, priv)
        return [self._signer]

    async def _connect(self, device: Device, auth_timeout: float = 10):
        conn = self._conns.get(device.id)
        if conn is not None and conn.available:
            return conn
        from adb_shell.adb_device_async import AdbDeviceTcpAsync
        conn = AdbDeviceTcpAsync(device.host, device.port or 5555, default_transport_timeout_s=self.connect_timeout)
        try:
            await conn.connect(rsa_keys=self._keys(), auth_timeout_s=auth_timeout,
                               transport_timeout_s=self.connect_timeout)
        except Exception as e:
            try:
                await conn.close()
            except Exception:
                pass
            raise AdapterError(f"couldn't connect to {device.name} over ADB ({type(e).__name__}: {e})") from e
        self._conns[device.id] = conn
        return conn

    async def _drop(self, device: Device):
        conn = self._conns.pop(device.id, None)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    async def shell(self, device: Device, cmd: str, timeout: float = 15) -> str:
        conn = await self._connect(device)
        try:
            return await conn.shell(cmd, read_timeout_s=timeout, timeout_s=timeout) or ""
        except Exception:
            await self._drop(device)
            raise

    # ---- the interface
    async def discover(self, timeout: float = 4) -> list[Device]:
        from ..discovery import browse
        out, seen = [], set()
        for r in await browse(["_androidtvremote2._tcp.local.", "_amzn-wplay._tcp.local."], timeout):
            if not r["host"] or r["host"] in seen:
                continue
            seen.add(r["host"])
            fire = r["type"].startswith("_amzn")
            name = r["props"].get("n") or r["props"].get("fn") or r["name"]
            out.append(Device(kind=self.kind, name=name, host=r["host"], port=5555,
                              platform="firetv" if fire else "android",
                              model="Fire TV" if fire else "Google TV / Android TV"))
        return out

    async def pair(self, device: Device, prompt: Prompt) -> Device:
        await prompt(f"On {device.name}: if a box asks to allow USB/network debugging, tick "
                     "\"Always allow from this computer\" and press Allow.", need_input=False)
        await self._drop(device)
        await self._connect(device, auth_timeout=90)
        info = (await self.shell(device, INFO_CMD)).splitlines() + [""] * 5
        maker, model, release, serial, chars = (x.strip() for x in info[:5])
        device.model = f"{maker} {model}".strip()
        device.platform = "firetv" if maker.lower() == "amazon" else "android"
        device.os_version = (("Fire OS (Android " if device.platform == "firetv" else "Android ") + release
                             + (")" if device.platform == "firetv" else "")) if release else ""
        device.identifier = serial or device.identifier
        return device

    async def now_playing(self, device: Device) -> NowPlaying | None:
        out = await self.shell(device, POLL_CMD)
        return poll_to_np(out)

    async def status(self, device: Device) -> Status:
        try:
            np = await asyncio.wait_for(self.now_playing(device), 12)
        except Exception:
            return Status(online=False)
        return Status(online=True, power="off" if np.state == OFF else "on", app=np.app or "")

    async def launch(self, device: Device, app: str, deep_link: str | None = None) -> None:
        """app: an Android package name (or a service name). deep_link: a URL to open with ACTION_VIEW."""
        if not re.fullmatch(r"[\w.]+", app or ""):
            raise AdapterError("bad package name")
        if deep_link:
            if not re.match(r"^[a-z][a-z0-9+.-]*://\S+$", deep_link, re.I):
                raise AdapterError("bad link")
            cmd = f"am start -a android.intent.action.VIEW -d {shlex.quote(deep_link)} -p {app}"
            out = await self.shell(device, cmd)
            if "Error" not in out:
                return
        # Wake the screen first (harmless if already awake), then start the TV launcher activity.
        await self.shell(device, "input keyevent KEYCODE_WAKEUP")
        for cat in ("android.intent.category.LEANBACK_LAUNCHER", "android.intent.category.LAUNCHER"):
            out = await self.shell(device, f"monkey -p {app} -c {cat} 1")
            if "Events injected: 1" in out:
                return
        raise AdapterError(f"{display_app(None, app)} isn't installed on {device.name}")

    async def close(self):
        for k in list(self._conns):
            conn = self._conns.pop(k)
            try:
                await conn.close()
            except Exception:
                pass


def platform_key(device: Device) -> str:
    return "firetv" if device.platform == "firetv" else "android"


__all__ = ["AndroidAdapter", "parse_media_sessions", "parse_description", "poll_to_np", "platform_key",
           "service_for"]
