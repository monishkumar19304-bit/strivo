# Roadmap

Strivo started as a personal project, an Apple TV watch log with a TiVo-style phone app, used every day in one household. This roadmap takes it from "works in one living room" to "works in yours".

The guiding rules:

1. **Fully automatic or not at all.** We only support setups that report what's playing by themselves.
2. **Your data stays home.** No Strivo cloud, ever.
3. **Normal people can install it.** If it needs a command line, we're not done.

---

## Phase 0 · Foundations ✅

Turn the household version into a public codebase.

- [x] Move all settings into one config file plus the setup wizard (no hard-coded devices, paths or keys)
- [x] One data folder, so backups and moves are trivial
- [x] Split the code into **core** (history, queue, picks) and **device adapters**
- [x] Remove household-specific features
- [x] Neutral visual theme (no look-alike trade dress)
- [x] Basic tests and CI

## Phase 1 · Easy install 🔜

- [x] Docker image (`ghcr.io/nickdnj/strivo`) for x86 and ARM *(built by CI; `:latest` published with v0.1)*
- [x] First-run **setup wizard**: discover boxes, pair, TMDB key, country, services, TV names
- [x] `strivo.local` name on the home network *(mDNS; needs testing on more routers)*
- [x] Health page: which boxes are online, last thing seen, recent errors *(More › Devices)*

## Phase 2 · v0.1 launch

- [ ] Apple TV fully supported
- [ ] Screenshots and a short demo video
- [ ] Docs: install, compatibility, FAQ, architecture
- [ ] Announce in the Home Assistant community, r/selfhosted and r/AppleTV

## Phase 3 · More boxes

- [x] **Google TV / Android TV / Fire TV adapter** (ADB over the network): the biggest group of homes that can be fully automatic *(verified on Google's emulator; needs real-box testers, especially Fire TV)*
- [x] **`strivo probe`**: a diagnostic that records what each app reports, for compatibility reports
- [x] **Plex and Jellyfin** (webhooks and API) *(verified on real servers in Docker; Plex webhooks untested)*
- [ ] Community-built compatibility table

## Phase 3½ · Self-healing compatibility

Apps will change what they report; Strivo should fix itself. Design: [SELF-HEALING.md](SELF-HEALING.md).

- [x] Fragile parts as data: app ids, title rules, deep links and expectations in a versioned `compat.json`
- [x] Health states per box and per app (healthy, degraded, offline, unpaired), with last-good versions
- [x] `strivo doctor` and a Health section in Settings
- [x] Daily check of GitHub releases for rules fixes (validated, rollback) and new versions (announce only)
- [x] Verify recovery from real playback, notify only on real changes
- [x] Opt-in, sanitized, prefilled GitHub report (no token, nothing posted by Strivo)
- [ ] Signed rules (maintainer key) on top of the checksum
- [ ] Probe reports that propose a rules change
- [ ] Expectations for every platform, filled from probe reports
- [ ] Tier 2: optional local-model diagnosis
- [ ] Tier 3: optional bring-your-own-key cloud diagnosis and report drafting
- [ ] Pilot with beta testers

## Phase 3¾ · Sports

Live games across every service, in one place. Design: [SPORTS.md](SPORTS.md).

- [ ] **Sports tab:** live now, later today, this week; filter by sport, league and team; "on my services"
- [ ] Where to watch each game, mapped from channels to your services (rules data)
- [ ] Play on TV opens the right app for the game
- [ ] Learn your teams automatically from what you watch; games in history
- [ ] No spoilers by default; optional "game starting" notice for your teams
- [ ] Local channels and blackouts by ZIP code

## Phase 4 · Everywhere (ideas)

- [ ] Home Assistant add-on
- [ ] Raspberry Pi image (flash, plug in, done)
- [ ] Roku adapter, if enough apps report titles *(experimental adapter exists; needs measurements from real Roku owners)*
- [ ] Netflix viewing-history import
- [ ] Multiple viewers per household (each person's own queue)
- [ ] Optional one-tap "was it this show?" for app-only devices, as an extra
- [ ] Translations and more countries

---

Open work is tracked as [issues](https://github.com/nickdnj/strivo/issues); look for
[`good first issue`](https://github.com/nickdnj/strivo/labels/good%20first%20issue) and
[`help wanted`](https://github.com/nickdnj/strivo/labels/help%20wanted).

Have an opinion on the order? **[Join the conversation in Discussions → Ideas](https://github.com/nickdnj/strivo/discussions/categories/ideas).**
