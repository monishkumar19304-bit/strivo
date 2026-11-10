# Sports (design)

> **Status:** first version built (Sports tab, filters, where to watch, follow teams, Play on TV). Learned teams, games in history and local channels by ZIP are next. See the [roadmap](ROADMAP.md) (Phase 3¾).

Live sports have scattered across more services than shows and movies have. One week of NFL games airs on Fox, CBS,
NBC, ESPN, NFL Network, Prime Video, Peacock and Netflix. Nobody can remember where tonight's game is. Strivo already
knows which services you pay for and can open apps on your TV, so it can answer the one question sports fans ask:
**"What's on right now, and where can I watch it?"**

## What you see: a **Sports** tab

- **Live now** first, then **Later today**, then **This week**. Each game is one card: teams, logos, score (or start time),
  and **where to watch it**, with your own services first.
- **Filters** at the top:
  - **Sport / league:** NFL, college football, NBA, WNBA, MLB, NHL, golf (PGA, LPGA, LIV), soccer (Premier League, MLS, …), and more.
  - **Team:** pick one or more teams and see only their games.
  - **On my services:** hide anything you can't watch with what you have.
- **My teams** row at the top: your teams' next and current games, always visible.
- **Tap a game → Play on TV:** opens the right app on the TV you choose (Peacock, Prime Video, ESPN, YouTube TV, Fox One, …),
  straight to the game where the app allows it.
- **Golf and racing** are events rather than games: one card per tournament or race, with the round or session that's on now.

## Fully automatic, here too

- **Your teams are learned, not typed.** When Strivo sees you watching a game (most live-TV apps report the matchup as
  the title), it counts it toward that team. After a few games the team shows up in **My teams** on its own. You can
  still add or remove teams by hand.
- **Watch history includes games.** A game you watched shows up in history as the matchup and final score, not
  "YouTube TV · 3 hr".
- **No spoilers by default.** Scores for games you haven't watched are hidden until you tap. Live scores can be turned on
  per sport.

## Where the data comes from

| What | Source | Notes |
|---|---|---|
| Schedules, live scores, teams, logos | ESPN's public scoreboard feed (`site.api.espn.com/.../scoreboard`) | Free, no key. Already lists each game's channels and streaming services (for example *Prime Video*, *Peacock*, *ESPN+*, *Fox*, team apps), including whether they're national or local. Not an official, documented API, so it sits behind a provider interface. |
| Fallback | TheSportsDB (free tier, community-run) | Used if the ESPN feed changes or goes away. |
| "Which of my services carries that channel" | **`networks` section of the rules file** (`compat.json`) | Maps channels to services, for example *Fox → YouTube TV, Hulu + Live TV, Fubo, Fox One*; *ABC → Hulu + Live TV, YouTube TV, ESPN app*. It's data, so it's fixed by a rules update, like everything else that breaks ([SELF-HEALING.md](SELF-HEALING.md)). |
| Local channels and blackouts | Your country and ZIP code (optional) | Regional sports networks and MLB/NBA/NHL blackouts depend on where you live. Without a ZIP code, local games are marked *"local channel"* rather than guessed. |

**Privacy:** the schedule is fetched the same way for everyone: by league and day, never by team or by user. Your
teams, your history and your ZIP code never leave the house. Team logos load on your phone straight from ESPN's image
server, the same as on any sports site.

## How it fits the code

- `sports/espn.py`: the schedule provider. One method, `day(league_path, yyyymmdd)`, returns plain game dicts; everything
  that depends on the feed's shape is in this file, so a second provider (TheSportsDB) can slot in beside it.
- `sports/__init__.py` (`Sports`): fetches each enabled league for yesterday through the next 6 days in parallel, caches
  each day (45 s while a game is live or about to start, 10 min for the rest of today, 3 h for later days), keeps the
  last good copy if the feed fails, and adds "where to watch" from the rules file and the household's services.
- Rules (`compat.json` → `sports`): `leagues` (id, feed path, name, sport, on by default), `networks` (channel →
  services), `aliases` (how the feed spells channels, e.g. *NFL Net*, *Golf Chnl*) and `play_as` (*Hulu + Live TV*
  opens the Hulu app). Validated like the rest of the rules file.
- API: `GET /api/sports` (games, leagues, followed teams, errors), `POST /api/sports/prefs` (`leagues`, `follow`, `unfollow`).
- Followed teams and enabled leagues are in `config.json` (`sports_teams`, `sports_leagues`).
- History: per-app title rules recognise matchups (`"Giants at Cowboys"`, `"NFL Football: Giants vs. Cowboys"`) and
  link the session to the game.
- Web: a new **Sports** tab in `web/index.html`, using the same card and Play on TV components as the rest of the app.

## First version ✅

1. NFL, college football, NBA, MLB, NHL and PGA golf on by default; WNBA, college basketball, LPGA, Premier League and
   MLS one tap away (⚙︎ in the Sports tab).
2. Live now / later today / this week / final, with league, team and "on my services" filters, and a My teams row.
3. Channel → service table for the US national channels and the major streamers.
4. Scores hidden by default (one switch, or "Show score" per game).
5. Play on TV opens the right app. ESPN, Sling TV and Fubo app ids are known for some boxes; Fox One, DirecTV Stream
   and NFL+ still need theirs (a rules update, once someone captures them with `strivo probe`).

Later: learned teams, games in history, spoiler control, local channels by ZIP, more leagues and countries,
a "game starting" notification for your teams (opt-in).

## Open questions

- **What do live-TV apps report on each box?** We need probe captures of YouTube TV, Hulu + Live TV, Fubo, the ESPN app,
  Peacock and Prime Video during a live game, on Apple TV and Google TV. A good first job for a sports-watching tester.
- **Deep links into a specific game** vary a lot by app; start with opening the app and improve per app.
