# Testing Strivo (without owning every box)

Strivo talks to many kinds of devices, and no one owns them all. This page explains how to test each adapter with
software stand-ins, and records what has actually been checked so far. If you own a real device in the
"emulated only" column, a `strivo probe` report from it is the most useful thing you can contribute.

## Unit tests (no devices, no network)

```bash
pip install -e ".[dev]"
pytest
```

Every adapter has fake-device tests in `tests/`:

| Adapter | What the tests fake |
|---|---|
| `appletv` | pyatv's scan / connect / pair / push-update interfaces (`tests/test_appletv.py`) |
| `android` | the ADB connection, plus **real `dumpsys` output captured from the Android TV 12 and Google TV 14 emulators** (`tests/fixtures/`) |
| `roku` | a fake ECP HTTP server (`tests/fakes.py`, also runnable as `tools/fake_roku.py`) |
| `jellyfin`, `plex` | fake HTTP APIs and real-shaped webhook payloads |
| core / server | a fake TMDB transport; the whole web app over HTTP, including the setup wizard, pairing with a PIN, Play on TV and both webhooks |

CI runs these on Python 3.11, 3.12 and 3.13 for every push and pull request.

## What has been verified, and on what

Checked by hand during development (November 2026). "Emulated" means real software standing in for the device:
the real Android OS in Google's emulator, the real Jellyfin and Plex servers in Docker.

| Platform | Discover | Pair | What's playing | Play on TV | Verified on |
|---|---|---|---|---|---|
| Apple TV | ✅ | not re-run* | ✅ app, state, title, position, power | not run* | **Real devices**: two Apple TV 4K (gen 3), tvOS 27.2 and 27.3, read-only. Push updates checked live. |
| Google TV | n/a† | ✅ incl. the TV's "Allow debugging?" prompt | ✅ title, position, paused, screen off | ✅ app launch, deep link, "not installed" error | **Emulator**: Google TV 14 (API 34, arm64) with VLC 3.7.1 |
| Android TV | n/a† | ✅ same | ✅ same | ✅ | **Emulator**: Android TV 12 (API 31, arm64) with VLC 3.7.1 |
| Fire TV | n/a | n/a | parser only (Fire OS uses the Android 9–11 format seen on the Android 12 emulator) | n/a | **Not verified on Fire OS.** No Fire TV emulator exists. Needs a tester. |
| Jellyfin | ✅ (UDP 7359) | ✅ API key | ✅ series, season, episode, position, pause, stop, per client | n/a | **Docker**: Jellyfin Server 12.4.0 (`jellyfin/jellyfin:latest`), API polling **and** the official Webhook plugin v22 |
| Plex | ✅ (GDM) | ✅ without a token on an allowed network | ✅ series, season, episode, position, per client | n/a | **Docker**: Plex Media Server 1.43.5 (`plexinc/pms-docker:latest`), unclaimed. **Webhooks not verified** (need Plex Pass). |
| Roku | ✅ SSDP | ✅ | ✅ channel, state, position; title only when the channel shares it | ✅ launch, keypress | **Fake ECP server** only. Needs a tester with a real Roku. |

\* Strivo was not allowed to pair with or launch apps on the household's Apple TVs during development. Pairing and
launching use the same pyatv calls as the household app, which has done both daily since October 2026, and are
covered by fake-device tests.
† The emulator isn't on the LAN, so mDNS discovery (`_androidtvremote2._tcp`, `_amzn-wplay._tcp`) wasn't exercised;
boxes can always be added by address.

The Docker image was also run end to end (non-root, healthcheck green) and tracked the Plex server from inside a
container.

## Reproducing the emulator checks

### Google TV / Android TV (Android SDK emulator)

```bash
# Apple Silicon: arm64 images. Intel/AMD: use x86_64.
sdkmanager "emulator" "system-images;android-34;google-tv;arm64-v8a" "system-images;android-31;android-tv;arm64-v8a"
avdmanager create avd -n gtv -k "system-images;android-34;google-tv;arm64-v8a" -d tv_1080p
emulator -avd gtv -no-window -no-audio -port 5580 &
adb -s emulator-5580 wait-for-device

# A real media app that publishes a media session: VLC (official APK from get.videolan.org)
adb -s emulator-5580 install -g VLC-Android-3.7.1-arm64-v8a.apk
adb -s emulator-5580 push "Some Show - S01E02 - Name.mp4" /sdcard/Movies/
adb -s emulator-5580 shell am start -a android.intent.action.VIEW -t video/mp4 \
  -d "file:///sdcard/Movies/Some%20Show%20-%20S01E02%20-%20Name.mp4" -n org.videolan.vlc/.gui.video.VideoPlayerActivity

# Make the emulator behave like a TV on the network: adbd on TCP, reachable from the host
adb -s emulator-5580 tcpip 5555
adb -s emulator-5580 forward tcp:6555 tcp:5555
```

Then add a Google TV in Strivo at `127.0.0.1`, port `6555`. Strivo connects with its own key, so the emulator shows
the real "Allow debugging?" prompt: tick **Always allow** and press **Allow** (click in the emulator window, or
`adb shell input tap` on the button). `strivo now` and `strivo probe --minutes 1` should then report VLC with the show
and episode.

### Jellyfin (Docker)

```bash
docker run -d --name jf -p 8096:8096 -v ./jf-config:/config -v ./media:/media:ro jellyfin/jellyfin
```

Finish the startup wizard, add a TV library on `/media/shows`, create an API key (Dashboard › API Keys) and add the
server in Strivo. Any Jellyfin client (or the `/Sessions/Playing` API) playing an episode shows up within a poll.
For webhooks, install **Webhook** from the plugin catalog and add a Generic destination to
`http://<strivo>:8091/api/webhook/jellyfin` with the template from the setup wizard.

Free test media: `ffmpeg -f lavfi -i testsrc=duration=120 "Some Show - S01E01 - Pilot.mp4"`, or public-domain films
from the Internet Archive.

### Plex (Docker)

```bash
docker run -d --name pms -p 32400:32400 -e ALLOWED_NETWORKS=172.16.0.0/12 -v ./media:/data:ro plexinc/pms-docker
```

An unclaimed server needs no Plex account. Unauthenticated access only works from networks in `ALLOWED_NETWORKS`; on
Docker Desktop the easiest way is to run Strivo inside the server's network
(`docker run --network container:pms … ghcr.io/nickdnj/strivo`). A client reporting playback
(`GET /:/timeline?ratingKey=…&state=playing…` with `X-Plex-Client-Identifier`) appears in `/status/sessions`.

### Roku (fake ECP server)

```bash
python tools/fake_roku.py --port 8060
```

Add a Roku at `127.0.0.1` in Strivo. The fake cycles between Netflix (no title, like most channels), a channel that
reports a title, and the home screen, and prints every launch it receives.

### Apple TV

pyatv ships a fake Apple TV in its source tree (`scripts/fake_device.py --mrp --companion --demo`) that is useful for
local development. Strivo's own tests fake pyatv's interfaces instead, so they run anywhere.
