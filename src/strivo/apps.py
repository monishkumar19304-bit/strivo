"""The streaming-service catalog: one name per service, and how each platform identifies its app.

`launch` ids are what an adapter needs to open the app. They are best-known values; if one is wrong for your
box, `strivo probe` shows the id your box reported. Please send a fix (it's a one-line PR).
"""
from __future__ import annotations

# name -> per-platform app ids. "appletv" is a tvOS bundle id, "android" a Google TV / Android TV package,
# "firetv" a Fire TV package, "roku" a Roku channel id.
SERVICES: dict[str, dict] = {
    "Netflix":     dict(appletv="com.netflix.Netflix", android="com.netflix.ninja", firetv="com.netflix.ninja", roku="12"),
    "Hulu":        dict(appletv="com.hulu.plus", android="com.hulu.livingroomplus", firetv="com.hulu.plus", roku="2285"),
    "Prime Video": dict(appletv="com.amazon.aiv.AIVApp", android="com.amazon.amazonvideo.livingroom",
                        firetv="com.amazon.firebat", roku="13"),
    "HBO Max":     dict(appletv="com.wbd.stream", android="com.wbd.stream", firetv="com.hbo.hbonow", roku="61322",
                        aliases=["Max", "HBO Max"]),
    "Disney+":     dict(appletv="com.disney.disneyplus", android="com.disney.disneyplus", firetv="com.disney.disneyplus",
                        roku="291097"),
    "Peacock":     dict(appletv="com.peacocktv.peacock", android="com.peacocktv.peacockandroid",
                        firetv="com.peacock.peacockfiretv", roku="593099"),
    "Paramount+":  dict(appletv="com.cbsvideo.app", android="com.cbs.ott", firetv="com.cbs.ott", roku="31440"),
    "Apple TV+":   dict(appletv="com.apple.TVWatchList", android="com.apple.atve.androidtv.appletv",
                        firetv="com.apple.atve.amazon.appletv", roku="551012", aliases=["TV", "Apple TV"]),
    "YouTube":     dict(appletv="com.google.ios.youtube", android="com.google.android.youtube.tv",
                        firetv="com.amazon.firetv.youtube", roku="837"),
    "YouTube TV":  dict(appletv="com.google.ios.youtubeunplugged", android="com.google.android.youtube.tvunplugged",
                        firetv="com.amazon.firetv.youtube.tv", roku="195316"),
    "PBS":         dict(appletv="org.pbs.playeripad", android="com.pbs.video", firetv="com.pbs.video", roku="23353"),
    "Tubi":        dict(appletv="com.adrise.tubitv", android="com.tubitv", firetv="com.tubitv", roku="41468"),
    "Pluto TV":    dict(appletv="tv.pluto.ios", android="tv.pluto.android", firetv="tv.pluto.android", roku="74519"),
    "Plex":        dict(appletv="com.plexapp.plex", android="com.plexapp.android", firetv="com.plexapp.android", roku="13535"),
    "Jellyfin":    dict(appletv="org.jellyfin.swiftfin", android="org.jellyfin.androidtv", firetv="org.jellyfin.androidtv",
                        roku="592369"),
    "VLC":         dict(appletv="org.videolan.vlc-ios", android="org.videolan.vlc", firetv="org.videolan.vlc"),
    "Kodi":        dict(android="org.xbmc.kodi", firetv="org.xbmc.kodi"),
}

# Services that show up in "My services" and in picks (TMDB / JustWatch provider names map onto these).
STREAMING = ["Netflix", "Hulu", "Prime Video", "HBO Max", "Disney+", "Peacock", "Paramount+", "Apple TV+",
             "YouTube TV", "YouTube", "Tubi", "Pluto TV", "PBS"]

# Apps whose "title" is a live channel or a one-off video rather than a show.
LIVE_APPS = {"YouTube TV"}
VIDEO_APPS = {"YouTube"}

# TMDB watch-provider names -> service names above.
PROV_MAP = {
    "Netflix": "Netflix", "Netflix basic with Ads": "Netflix", "Netflix Standard with Ads": "Netflix",
    "Hulu": "Hulu", "Hulu Amazon Channel": "Hulu",
    "Paramount Plus": "Paramount+", "Paramount+": "Paramount+", "Paramount+ Amazon Channel": "Paramount+",
    "Paramount Plus Apple TV Channel": "Paramount+", "Paramount Plus Apple TV Channel ": "Paramount+",
    "Paramount+ Apple TV Channel ": "Paramount+", "Paramount Plus Essential": "Paramount+",
    "Paramount Plus Premium": "Paramount+",
    "Apple TV Plus": "Apple TV+", "Apple TV+": "Apple TV+", "Apple TV": "Apple TV+",
    "Apple TV Amazon Channel": "Apple TV+", "Apple TV Plus Amazon Channel": "Apple TV+",
    "Amazon Prime Video": "Prime Video", "Amazon Prime Video with Ads": "Prime Video", "Prime Video": "Prime Video",
    "Max": "HBO Max", "HBO Max": "HBO Max", "Max Amazon Channel": "HBO Max", "HBO Max Amazon Channel": "HBO Max",
    "Peacock": "Peacock", "Peacock Premium": "Peacock", "Peacock Premium Plus": "Peacock",
    "Peacock Premium Amazon Channel": "Peacock",
    "Disney Plus": "Disney+", "Disney+": "Disney+",
    "YouTube TV": "YouTube TV", "YouTube": "YouTube", "YouTube Premium": "YouTube",
    "Tubi TV": "Tubi", "Tubi": "Tubi", "Pluto TV": "Pluto TV", "PBS": "PBS", "The Roku Channel": "Roku Channel",
}

_PLATFORM_KEYS = ("appletv", "android", "firetv", "roku")


def _build_reverse():
    rev = {}
    for name, ids in SERVICES.items():
        rev[name.lower()] = name
        for a in ids.get("aliases", []):
            rev[a.lower()] = name
        for k in _PLATFORM_KEYS:
            if ids.get(k):
                rev[ids[k].lower()] = name
    return rev


_REVERSE = _build_reverse()


def service_for(app: str | None, app_id: str | None = None) -> str | None:
    """Map whatever a box reports (an app name, a bundle id, a package, a channel id) to a service name."""
    for v in (app_id, app):
        if v and v.lower() in _REVERSE:
            return _REVERSE[v.lower()]
    return None


def display_app(app: str | None, app_id: str | None = None) -> str | None:
    """The friendly app name used in history: the service name when known, else what the box said."""
    return service_for(app, app_id) or app or app_id


def launch_id(service: str, platform: str) -> str | None:
    s = SERVICES.get(service) or SERVICES.get(service_for(service) or "")
    return (s or {}).get(platform)
