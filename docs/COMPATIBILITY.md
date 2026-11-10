# Does it work with my TV?

Strivo has one rule: **tracking must be fully automatic.** A setup is supported only if it tells Strivo *what's playing* (the show, and ideally the episode) all by itself.

So what matters is the **box (or TV operating system) that runs your streaming apps**, not the brand on the front of the TV.

- A **Samsung or LG** TV with an Apple TV, Google TV or Fire TV plugged in? ✅ Fully supported. Strivo watches the box.
- A **Sony, TCL, Hisense or Philips** TV with Google TV built in? ✅ Supported directly.
- An **Insignia or Toshiba** Fire TV Edition TV? ✅ Supported directly.

---

## Supported setups

### Tier 1: fully automatic

| Setup | Examples | Knows the title? | How Strivo talks to it | One-time setup |
|---|---|---|---|---|
| 🍎 **Apple TV** | Apple TV 4K, Apple TV HD | ✅ Most apps (see the app table) | Local network, via [pyatv](https://github.com/postlund/pyatv) | Type a 4-digit code shown on the TV |
| 🤖 **Google TV / Android TV** | Google TV Streamer, Chromecast with Google TV, Nvidia Shield, onn 4K; Sony, TCL, Hisense, Philips TVs with Google TV | ✅ Apps that publish a media session *(being verified per app)* | Local network, via Android's debugging bridge (ADB) | Turn on developer options and network debugging, then press **Allow** on the TV |
| 🔥 **Fire TV** | Fire TV Stick, Fire TV Cube; Insignia, Toshiba Fire TV Edition TVs | ❔ Expected to match Google TV *(no Fire TV tested yet)* | Same as Google TV (Fire TV is Android underneath) | Same as Google TV (Settings › My Fire TV › Developer options › ADB debugging) |
| 🎬 **Plex / Jellyfin / Emby** | Any home media server, played on any TV | ✅ Always: show, season, episode and exact position | The server's API (polled every few seconds), plus optional webhooks for instant updates (Plex webhooks need Plex Pass; Jellyfin has a free Webhook plugin) | Paste the server address and a token (Jellyfin: an API key; Plex: an X-Plex-Token, or none on a trusted network) |

### Where each setup stands today

| Setup | Status | Checked on |
|---|---|---|
| 🍎 Apple TV | **Working** | Real Apple TV 4K boxes, daily since October 2026 (the household version), and read-only against the new adapter |
| 🤖 Google TV / Android TV | **Adapter built, needs real-box testers** | Google's emulator (Google TV 14, Android TV 12) with a real media app (VLC) |
| 🔥 Fire TV | **Adapter built, untested on Fire OS** | Same adapter as Google TV; no Fire TV emulator exists |
| 🎬 Jellyfin | **Adapter built** | Real Jellyfin server in Docker: API polling and the Webhook plugin |
| 🎬 Plex | **Adapter built** (webhooks untested) | Real Plex Media Server in Docker, API polling; webhooks need Plex Pass |
| 🟣 Roku | **Experimental** | A fake Roku (ECP) server only |

How each was checked, and how to repeat it: **[TESTING.md](TESTING.md)**.

### Tier 2: being measured

| Setup | Why it's not Tier 1 yet |
|---|---|
| 🟣 **Roku** (Roku players, Roku TVs from TCL, Hisense and others) | Easy to talk to, and "Play on TV" works, but only some apps report what's playing. If community testing shows enough apps do, Roku moves up. |

### Not supported for tracking

| Setup | Why |
|---|---|
| 📺 **Samsung Tizen built-in apps** | The TV reports which app is open, but never the show. |
| 📺 **LG webOS built-in apps** | Same: app only, never the show. |
| 📺 **Vizio SmartCast built-in apps** | Same limitation. |

"Play on TV" may come to these later, but tracking can't be automatic, so the answer is: **plug in a supported box** (a streaming stick costs about $30).

---

## Apps

Even on a supported box, each streaming app decides what it shares. This table is **filled in from community reports**. Today only Apple TV has been tested, in one household.

Legend: ✅ reports what's playing · 🟡 sometimes · ⚪ app only · ❔ not tested yet

Apps report titles differently. Some send "Show | S43 E19", others only the episode name. Strivo matches whatever arrives to the right show and episode through TMDB.

| App | Apple TV | Google TV / Android TV | Fire TV | Roku |
|---|---|---|---|---|
| VLC | ❔ | ✅ file title (emulator) | ❔ | n/a |
| Hulu | ✅ show + episode | ❔ | ❔ | ❔ |
| Max | ✅ episode name | ❔ | ❔ | ❔ |
| Peacock | ✅ | ❔ | ❔ | ❔ |
| Paramount+ | ✅ | ❔ | ❔ | ❔ |
| Disney+ | ✅ | ❔ | ❔ | ❔ |
| Apple TV app | ✅ | ❔ | ❔ | ❔ |
| YouTube TV | ✅ | ❔ | ❔ | ❔ |
| YouTube | ✅ video title | ❔ | ❔ | ❔ |
| Prime Video | ✅ *(few samples)* | ❔ | ❔ | ❔ |
| PBS | 🟡 | ❔ | ❔ | ❔ |
| **Netflix** | ⚪ | ❔ | ❔ | ❔ |

> **Netflix** doesn't share what's playing on Apple TV. A **Netflix viewing-history import** (Netflix lets you download your history) is planned to fill the gap.

*Apple TV results are from one household's daily use; your app versions may differ. Please report what you see.
The VLC result is from Google's TV emulator, where the big streaming apps can't be installed; it proves the
pipeline, not any particular streaming app.*

On Google TV / Android TV / Fire TV, Strivo reads Android's media sessions: the same "now playing" information the
TV shows on its own Now Playing card. Apps that show one will almost certainly work; apps that don't (some
Netflix builds) will show as **app only**. `strivo probe` tells you which is which on your box.

---

## Share a report

This table only gets filled in with your help, and it's one of the most valuable things you can contribute.

1. Install Strivo ([INSTALL.md](INSTALL.md); before v0.1, `pip install git+https://github.com/nickdnj/strivo` works) and add your box with `strivo setup` or the setup wizard.
2. Run:
   ```bash
   strivo probe --minutes 10
   ```
3. While it runs, play something for a minute in each app you use.
4. It prints a short report: your box model, and for each app whether it reported the show, the episode, or nothing. **It contains no personal information**: no account names and no network addresses.
5. Post it in **[Discussions → Compatibility reports](https://github.com/nickdnj/strivo/discussions/categories/compatibility-reports)**, or open a [Device report issue](https://github.com/nickdnj/strivo/issues/new?template=device_report.yml).

Maintainers fold reports into the tables above.
