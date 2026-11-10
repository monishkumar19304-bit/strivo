# Contributing to Strivo

Thanks for being here. Strivo gets better with every living room it works in, and most ways to help **don't need any code**.

## Ways to help

| You are… | You can… |
|---|---|
| 🛋️ **Someone who watches TV** | Try Strivo, tell us what's confusing, [share ideas](https://github.com/nickdnj/strivo/discussions/categories/ideas) |
| 🧪 **Someone with a streaming box** | Run `strivo probe` and [share a compatibility report](docs/COMPATIBILITY.md#share-a-report). This is the #1 thing we need, especially for **Google TV, Android TV, Fire TV and Roku**. |
| 🏠 **A Home Assistant / self-hosting fan** | Help test installs on Pi, NAS and HA; improve the [install guide](docs/INSTALL.md) |
| 🎨 **A designer** | Couch-friendly UI, icons, the Posters view, accessibility |
| 🐍 **A developer** | Write a [device adapter](docs/ARCHITECTURE.md#device-adapters), fix bugs, improve picks |
| ✍️ **A writer** | Docs, FAQ, translations |

## Where to talk

- **[Discussions](https://github.com/nickdnj/strivo/discussions)** for questions, ideas, compatibility reports and show-and-tell.
- **[Issues](https://github.com/nickdnj/strivo/issues/new/choose)** for confirmed bugs and agreed-on features.

Not sure which? Start a Discussion. We'll turn it into an issue if needed.

## Before you write code

- For anything bigger than a small fix, **open or comment on an issue first**, so we agree on the approach before you spend time.
- Look for [`good first issue`](https://github.com/nickdnj/strivo/labels/good%20first%20issue) and [`help wanted`](https://github.com/nickdnj/strivo/labels/help%20wanted).

## Development setup

*(Filled in when the public code lands in Phase 0.)*

```bash
git clone https://github.com/nickdnj/strivo
cd strivo
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
strivo serve --data ./dev-data
```

## Pull requests

- Keep PRs focused: one change per PR.
- Add or update tests where it makes sense.
- Update docs if behavior changes.
- **Never commit API keys, tokens, pairing credentials, or your own watch history.** The data folder is in `.gitignore` for a reason.
- Fill in the PR template; it's short.

## Writing a device adapter

1. Read [ARCHITECTURE.md → Device adapters](docs/ARCHITECTURE.md#device-adapters).
2. Copy an existing adapter as a starting point.
3. Implement `discover`, `pair`, `now_playing` and `launch`.
4. Run `strivo probe` against your device and include the output in your PR.
5. Add your device to [COMPATIBILITY.md](docs/COMPATIBILITY.md).

**The bar for a tracking adapter:** it must report the title on its own for the common apps. "App name only" devices can get "Play on TV" support, but not tracking.

## Code of conduct

Be kind. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
