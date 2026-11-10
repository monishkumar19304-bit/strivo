# Roadmap

Strivo started as a personal project, an Apple TV watch log with a TiVo-style phone app, used every day in one household. This roadmap takes it from "works in one living room" to "works in yours".

The guiding rules:

1. **Fully automatic or not at all.** We only support setups that report what's playing by themselves.
2. **Your data stays home.** No Strivo cloud, ever.
3. **Normal people can install it.** If it needs a command line, we're not done.

---

## Phase 0 · Foundations 🔜

Turn the household version into a public codebase.

- [ ] Move all settings into one config file plus the setup wizard (no hard-coded devices, paths or keys)
- [ ] One data folder, so backups and moves are trivial
- [ ] Split the code into **core** (history, queue, picks) and **device adapters**
- [ ] Remove household-specific features
- [ ] Neutral visual theme (no look-alike trade dress)
- [ ] Basic tests and CI

## Phase 1 · Easy install

- [ ] Docker image (`ghcr.io/nickdnj/strivo`) for x86 and ARM
- [ ] First-run **setup wizard**: discover boxes, pair, TMDB key, country, services, TV names
- [ ] `strivo.local` name on the home network
- [ ] Health page: which boxes are online, last thing seen, recent errors

## Phase 2 · v0.1 launch

- [ ] Apple TV fully supported
- [ ] Screenshots and a short demo video
- [ ] Docs: install, compatibility, FAQ, architecture
- [ ] Announce in the Home Assistant community, r/selfhosted and r/AppleTV

## Phase 3 · More boxes

- [ ] **Google TV / Android TV / Fire TV adapter** (ADB over the network): the biggest group of homes that can be fully automatic
- [ ] **`strivo probe`**: a diagnostic that records what each app reports, for compatibility reports
- [ ] **Plex and Jellyfin** (webhooks and API)
- [ ] Community-built compatibility table

## Phase 4 · Everywhere (ideas)

- [ ] Home Assistant add-on
- [ ] Raspberry Pi image (flash, plug in, done)
- [ ] Roku adapter, if enough apps report titles
- [ ] Netflix viewing-history import
- [ ] Multiple viewers per household (each person's own queue)
- [ ] Optional one-tap "was it this show?" for app-only devices, as an extra
- [ ] Translations and more countries

---

Have an opinion on the order? **[Join the conversation in Discussions → Ideas](https://github.com/nickdnj/strivo/discussions/categories/ideas).**
