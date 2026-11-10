# Configuration

Almost everything is set in the app: the setup wizard, **More › Devices** and **More › Settings**. This page covers
the few things that aren't (yet), and the environment variables.

## Environment variables

| Variable | Default | What |
|---|---|---|
| `STRIVO_DATA` | `/data` in Docker, else `~/.local/share/strivo` | The data folder |
| `STRIVO_PORT` | `8091` | Web app port |
| `STRIVO_HOST` | `0.0.0.0` | Address to listen on |
| `STRIVO_TMDB_KEY` | *(none)* | TMDB key, if you'd rather not paste it into the app |
| `TZ` | system | Your time zone (Docker) |

## `config.json`

Lives in the data folder. Edit it with Strivo stopped. Keys you might want:

```jsonc
{
  "poll_seconds": 10,            // how often polled boxes (Android, Roku, Plex, Jellyfin) are asked
  "notify_url": "",              // also in Settings: where wishlist alerts are POSTed
  "rules": {
    "recurring": ["Jeopardy!"],  // always treat these as daily/game shows (kept out of Watching)
    "movies": ["Some Title"],    // always treat these as movies
    "movie_min_sec": 4500,       // untagged titles at least this long count as movies
    "late_night": ["…"],         // title words that mean late night / talk
    "news_channels": ["…"],      // channels whose shows count as news (kept 2 days)
    "news_titles": ["…"]
  },
  // Optional: describe your household's taste for movie picks
  "taste": {
    "genres": {"Mystery": 1.0, "Adventure": 0.8, "Comedy": 0.5},   // boost these TMDB genres
    "story_genres": ["Adventure", "Mystery", "Science Fiction"],    // if set, Action picks need one of these too
    "searches": [["a whodunit", "", ["whodunit", "detective"]],     // [reason shown, "Genre,Genre", [TMDB keywords]]
                 ["a space adventure", "Adventure,Science Fiction", []]]
  },
  "tv_taste": [["a mystery series", "9648", []]]                    // same idea for series (TMDB TV genre ids)
}
```

Everything else in the file is managed by the app.
