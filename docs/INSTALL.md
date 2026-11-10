# Installing Strivo

> **Status:** early development. The code runs from source and the Docker image builds; the published image (`:latest`) arrives with v0.1. Apple TV is proven in daily use; the other platforms are built and checked on emulators and servers in Docker ([TESTING.md](TESTING.md)) and need real-device testers. Follow [the roadmap](ROADMAP.md) or watch the repo.

Strivo is a small program that runs **on a computer in your home that's always on**. It quietly watches your streaming boxes, keeps your history and queue, and serves the app you use on your phone.

It does **not** run on the TV or the streaming box itself, and there is no Strivo cloud. Your viewing history never leaves your house.

---

## The 3-minute version

1. **Check your TV setup.** You need at least one supported box: Apple TV, Google TV / Android TV, Fire TV, or a Plex/Jellyfin server. See [Does it work with my TV?](COMPATIBILITY.md).
2. **Pick a home for Strivo.** It needs an always-on computer (below). A $60–100 Raspberry Pi is the easiest.
3. **Install it** with one of the paths below.
4. **Open `http://strivo.local:8091`** on your phone (or `http://<the computer's IP>:8091`). The setup wizard finds your boxes, pairs them, and asks which streaming services you have.
5. **Add it to your home screen.** It works like an app.

That's it. From then on, everything you watch is logged by itself.

---

## What you need

| | |
|---|---|
| **A supported streaming box** | Apple TV, Google TV / Android TV, Fire TV, or a Plex/Jellyfin server. [Full list](COMPATIBILITY.md). |
| **An always-on computer** on the same home network | See [Choosing a home for Strivo](#choosing-a-home-for-strivo). |
| **A free TMDB account** | For posters, descriptions and recommendations. The wizard walks you through getting a key (about 2 minutes). |
| **A phone** | Any modern iPhone or Android phone browser. No app store needed. |

---

## Choosing a home for Strivo

Strivo is light. Measured on a real household running it every day:

- **Memory:** about 175 MB
- **CPU:** under 1% (it mostly waits)
- **Disk:** about 60 MB for the program, plus a few MB of data per year

So almost anything works. What matters is that it's **always on** and on the **same network** as your TVs.

### Recommended specs

| | Minimum | Recommended |
|---|---|---|
| **Processor** | Any 64-bit ARM or x86 | Raspberry Pi 4/5 or any Intel/AMD from the last 10 years |
| **Memory** | 1 GB | 2 GB or more |
| **Storage** | 8 GB | 32 GB SSD or a good-quality microSD card |
| **Network** | Wi-Fi | **Wired Ethernet** to your router (more reliable discovery) |
| **Power** | Always on | Always on, set to restart after a power cut |

### Options, easiest first

#### 1. A Raspberry Pi "Strivo box" (recommended for most people)
About **$60–100** all in: a Raspberry Pi 4 or 5 (2 GB is plenty), the official power supply, a case, and a 32 GB microSD card. It uses about as much power as a night light (about 3–5 W, a few dollars a year).

- Flash the **Strivo image** onto the card with the free [Raspberry Pi Imager](https://www.raspberrypi.com/software/) *(planned)*.
- Plug the Pi into your router and power.
- Open `http://strivo.local:8091` on your phone.

No keyboard, monitor, or command line needed.

#### 2. A computer you already leave on
A Mac mini, an old laptop, or a Windows PC that stays on.

- **Mac / Windows / Linux:** install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then run Strivo with one command (below).
- A native Mac menu-bar app is a possible later addition.
- Make sure it doesn't go to sleep. Sleeping means nothing gets logged.

#### 3. A NAS (Synology, QNAP, Unraid, TrueNAS)
If you have a NAS, it's a great home. Install the Strivo container from the NAS's Docker / Container Manager app. It needs **host networking** to find your boxes.

#### 4. Home Assistant
If you run Home Assistant, Strivo will be available as an **add-on** *(planned, after the Docker image)*. Home Assistant already uses the same open-source libraries to talk to Apple TV and Android TV.

#### 5. A small mini PC
An Intel N100-class mini PC (about $120–180) if you want something sturdier than a Pi, or plan to run other home services on it too.

### What doesn't work
- **On the TV or the streaming box itself.** Strivo needs a separate always-on computer.
- **A guest Wi-Fi network** or a separate network segment (VLAN) from your TVs. Strivo finds boxes using local discovery (Bonjour/mDNS), which usually doesn't cross those boundaries.
- **A laptop that sleeps or leaves the house.** Strivo can't log what it can't see.

---

## Install paths

### Docker (Mac, Windows, Linux, NAS)
```bash
docker run -d --name strivo --network host --restart unless-stopped \
  -e TZ=America/New_York -v strivo-data:/data ghcr.io/nickdnj/strivo:latest
```
*(`:latest` is published with v0.1. Until then, `:edge` is built from `main`, or build it yourself: `docker build -t strivo .`)*

- `--network host` is required so Strivo can discover and talk to your boxes. (Docker Desktop on Mac/Windows has
  limited host networking; a Raspberry Pi, Linux box or NAS is the smoothest home.)
- Set `TZ` to your time zone so "last night" means last night.
- The app listens on port **8091** (change with `-e STRIVO_PORT=...`).

### Docker Compose
```yaml
services:
  strivo:
    image: ghcr.io/nickdnj/strivo:latest
    network_mode: host
    restart: unless-stopped
    environment:
      - TZ=America/New_York
    volumes:
      - strivo-data:/data
volumes:
  strivo-data:
```
(The same file is in the repo as [`docker-compose.yml`](../docker-compose.yml).)

### From source (any computer with Python 3.11+)
```bash
pip install git+https://github.com/nickdnj/strivo     # or: git clone … && pip install .
strivo serve                                         # data goes to ~/.local/share/strivo
```
`strivo setup` does the setup in the terminal instead of the web wizard. `strivo --help` lists the rest.

### Raspberry Pi image *(planned)*
Download the image from Releases and flash it with Raspberry Pi Imager.

### Home Assistant add-on *(planned)*
Add the Strivo add-on repository in **Settings → Add-ons → Add-on Store → Repositories**.

---

## First-run setup wizard

When you first open Strivo it walks you through:

1. **Find your boxes.** It scans the network and lists every Apple TV, Google TV / Android TV and Fire TV it finds.
2. **Pair them.**
   - **Apple TV:** type the 4-digit code shown on the TV.
   - **Google TV / Android TV / Fire TV:** turn on network debugging once (the wizard shows exactly where), then press **Allow** on the TV.
   - **Plex / Jellyfin:** paste your server address and token.
3. **Add your TMDB key** (the wizard links you to the free sign-up).
4. **Pick your country and streaming services**, so recommendations only show what you can actually watch.
5. **Name your TVs** ("Living Room", "Bedroom") and pick a default.

You can add, rename, pause or remove boxes later under **More › Devices**, which also shows whether each box is
connected and the last thing it reported.

---

## Setting up each kind of box

### Apple TV
Nothing to change on the Apple TV. Strivo asks for two 4-digit codes shown on the TV: one lets it see what's
playing, the other lets "Play on TV" open apps.

### Google TV / Android TV
1. **Settings › System › About** (or Device Preferences › About), scroll to **Build**, and press it 7 times.
2. **Settings › System › Developer options**: turn on **USB debugging** (and **Network debugging** / **ADB over
   network** if your TV has it).
3. Add the TV in Strivo. The TV asks *"Allow debugging?"*: tick **Always allow from this computer** and press **Allow**.

Strivo uses ADB on port 5555. TVs that only offer *Wireless debugging* with a pairing code (some newer Google TV
builds) aren't supported yet; see the issue tracker.

### Fire TV
1. **Settings › My Fire TV › About**: press your device name 7 times to unlock Developer options.
2. **Settings › My Fire TV › Developer options**: turn on **ADB debugging**.
3. Add it in Strivo and press **Allow** when the Fire TV asks.

### Roku (experimental)
**Settings › System › Advanced system settings › Control by mobile apps › Network access**: choose **Permissive**.
Roku tells Strivo which channel is open, but only some channels say what's playing; `strivo probe` shows which.

### Jellyfin / Emby
1. **Dashboard › API Keys › +**, name it "Strivo", and paste the key into Strivo.
2. That's enough: Strivo asks the server what's playing every few seconds, on every client.
3. *Optional, for instant updates:* install **Webhook** from **Dashboard › Plugins › Catalog**, restart, then add a
   **Generic** destination: URL `http://<strivo>:8091/api/webhook/jellyfin`, notification types *Playback Start*,
   *Playback Progress* and *Playback Stop*, and paste the template shown in the setup wizard (also in
   `strivo/adapters/jellyfin.py`).

### Plex
1. Add the server in Strivo. If your Plex server allows your home network without signing in
   (**Settings › Network › List of IP addresses and networks that are allowed without auth**), no token is needed.
   Otherwise paste your **X-Plex-Token** ([how to find it](https://support.plex.tv/articles/204059436)).
2. *Optional, Plex Pass only:* **Settings › Webhooks › Add** `http://<strivo>:8091/api/webhook/plex`.

---

## Using it away from home (optional)

Strivo only listens on your home network by default. To use it on the go, the simplest safe option is [Tailscale](https://tailscale.com) (free for personal use) on both the Strivo computer and your phone. **Don't** open a port on your router to the internet.

---

## Backups

Everything Strivo knows lives in one data folder (`/data` in Docker). Back it up and you can move Strivo to a new computer in minutes.

Strivo keeps your history forever but doesn't back itself up: that's the job of the computer it runs on (Time Machine,
a NAS, restic, your host's snapshots). Include the whole data folder. If you copy `strivo.db` by hand while Strivo is
running, copy `strivo.db-wal` with it, or take a clean copy first:

```bash
sqlite3 /data/strivo.db ".backup /backups/strivo-$(date +%F).db"
```

To get your history out in a plain format any time (to keep, or to move):

```bash
strivo export strivo-history.jsonl        # Docker: docker exec strivo strivo export /data/strivo-history.jsonl
```

Putting that export (or an `events.jsonl` from an older install) into a new data folder imports it on first start.

---

## Getting help

- **Setup questions:** [GitHub Discussions → Q&A](https://github.com/nickdnj/strivo/discussions)
- **Something broken:** [open a bug report](https://github.com/nickdnj/strivo/issues/new/choose)
- **Your device isn't listed:** run `strivo probe` and [share a compatibility report](COMPATIBILITY.md#share-a-report)
- **A box shows "Not reachable":** check it's on and on the same network, then look at **More › Devices** for the
  error. For Android boxes, debugging switches off after some system updates; turn it back on.
