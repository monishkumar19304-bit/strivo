"""The `strivo` command: serve, setup, probe, discover, now."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

from . import __version__
from .config import DataDir, default_data_dir


def _data(args) -> DataDir:
    return DataDir(args.data or default_data_dir())


def cmd_serve(args):
    from .server import serve
    serve(args.data or default_data_dir(), args.host, args.port, advertise=not args.no_mdns)


async def _prompt_cli(message: str, need_input: bool = True) -> str:
    print("  " + message)
    if not need_input:
        return ""
    return await asyncio.to_thread(input, "  > ")


def cmd_setup(args):
    """A terminal version of the web wizard."""
    from .adapters import all_adapters
    from .collector import Collector
    from .core import Core
    from .devices import DeviceManager
    from .apps import streaming
    STREAMING = streaming()
    from .tmdb import TMDB
    data = _data(args)
    print(f"Strivo setup · data folder: {data.path}\n")

    async def run():
        col = Collector(data, all_adapters(data), log_events=False)
        dm = DeviceManager(data, col, None)
        print("Looking for boxes and media servers on your network (about 10 seconds)…")
        found = [f for f in await dm.discover(5) if not f["added"]]
        for i, f in enumerate(found, 1):
            print(f"  {i}. {f['name']} · {f['label']}{' (experimental)' if f['experimental'] else ''}"
                  f"{' · ' + f['model'] if f.get('model') else ''} · {f['host']}")
        if not found:
            print("  Nothing new found.")
        while True:
            ans = (await asyncio.to_thread(input, "\nAdd which? (number, 'm' to add by address, Enter to continue) ")).strip()
            if not ans:
                break
            if ans.lower() == "m":
                kind = (await asyncio.to_thread(input, f"  Type ({', '.join(col.adapters)}): ")).strip()
                host = (await asyncio.to_thread(input, "  Address: ")).strip()
                name = (await asyncio.to_thread(input, "  Name (e.g. Living Room): ")).strip() or kind
                plat = ""
                if kind == "android":
                    plat = "firetv" if (await asyncio.to_thread(input, "  Fire TV? (y/N) ")).lower().startswith("y") \
                        else "android"
                spec = dict(kind=kind, host=host, name=name, platform=plat)
            elif ans.isdigit() and 1 <= int(ans) <= len(found):
                spec = dict(found[int(ans) - 1])
                nm = (await asyncio.to_thread(input, f"  Name for {spec['name']} (Enter to keep): ")).strip()
                spec["name"] = nm or spec["name"]
            else:
                continue
            from .adapters.base import Device
            d = Device.from_dict(dict(spec, id=""))
            d.id = dm._unique_id(d.id)
            try:
                d = await col.adapters[d.kind].pair(d, _prompt_cli)
                dm.save_device(d)
                print(f"  ✓ {d.name} added.")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {e}")
        await col.stop()
        for a in col.adapters.values():
            await a.close()

    asyncio.run(run())
    core = Core(data)
    if not core.tmdb.configured:
        key = input("\nTMDB key for posters and picks (free at themoviedb.org > Settings > API; Enter to skip): ").strip()
        if key:
            if TMDB(key).check():
                data.set_secret("tmdb_key", key)
                print("  ✓ key saved")
            else:
                print("  ✗ TMDB didn't accept that key; add it later in Settings.")
    c = data.config()
    ctry = input(f"\nCountry code [{c.get('country', 'US')}]: ").strip().upper()
    print("\nServices you pay for (numbers, comma-separated; Enter to keep):")
    for i, s in enumerate(STREAMING, 1):
        print(f"  {i:2}. {s}{' ✓' if s in (c.get('services') or []) else ''}")
    pick = input("  > ").strip()
    body = {}
    if len(ctry) == 2 and ctry.isalpha():
        body["country"] = ctry
    if pick:
        body["my_services"] = [STREAMING[int(x) - 1] for x in pick.replace(" ", "").split(",")
                               if x.isdigit() and 1 <= int(x) <= len(STREAMING)]
    core.settings_save(body)
    data.update_config(lambda c: c.__setitem__("setup_done", True))
    print("\nDone. Start Strivo with:  strivo serve")


def cmd_probe(args):
    from .probe import from_events, listen
    data = _data(args)
    if args.events:
        from .collector import Collector
        p = from_events(args.events, Collector(data, {}, log_events=False).devices())
        print(json.dumps(p.as_json(), indent=1) if args.json else p.report())
        return
    from .adapters import all_adapters
    from .collector import Collector

    async def run():
        col = Collector(data, all_adapters(data), log_events=False)
        if not col.devices():
            print("No boxes set up yet. Run `strivo setup` first (or use the setup wizard in the web app).")
            return None
        print(f"Listening for {args.minutes:g} minute(s). Play something for a minute in each app you use…",
              file=sys.stderr)
        p = await listen(col, args.minutes, args.device)
        for a in col.adapters.values():
            await a.close()
        return p
    p = asyncio.run(run())
    if p:
        print(json.dumps(p.as_json(), indent=1) if args.json else p.report(args.minutes))


def cmd_discover(args):
    from .adapters import all_adapters
    from .collector import Collector
    from .devices import DeviceManager
    data = _data(args)

    async def run():
        col = Collector(data, all_adapters(data), log_events=False)
        res = await DeviceManager(data, col, None).discover(args.timeout)
        for a in col.adapters.values():
            await a.close()
        return res
    for d in asyncio.run(run()):
        print(json.dumps({k: d[k] for k in ("kind", "label", "name", "model", "os_version", "host", "port", "added")}))


def cmd_now(args):
    """Ask every configured box once what it's playing (handy when testing an adapter)."""
    from .adapters import all_adapters
    from .collector import Collector
    data = _data(args)

    async def run():
        col = Collector(data, all_adapters(data), log_events=False)
        out = []
        for d in col.devices():
            ad = col.adapters.get(d.kind)
            try:
                np = await asyncio.wait_for(ad.now_playing(d), 20)
                out.append(dict(device=d.name, kind=d.kind, **(np.to_event() if np else {})))
            except Exception as e:  # noqa: BLE001
                out.append(dict(device=d.name, kind=d.kind, error=f"{type(e).__name__}: {e}"))
        for a in col.adapters.values():
            await a.close()
        return out
    for row in asyncio.run(run()):
        print(json.dumps(row, default=str))


def cmd_doctor(args):
    """Is tracking healthy? Checks each box now, shows per-app health, rules version and fixes."""
    from .adapters import all_adapters
    from .server import App
    data = _data(args)
    app = App(data.path, adapters=all_adapters(data), start_background=False)
    try:
        for d in app.collector.devices():   # a quick live check instead of the collector's long-running watch
            if not d.enabled:
                continue
            ad = app.collector.adapters.get(d.kind)
            lv = app.collector.live.setdefault(d.id, dict(name=d.name, kind=d.kind))
            try:
                st = app.runtime.call(ad.status(d), 30)
                lv.update(online=st.online, error="" if st.online else "no answer")
            except Exception as e:  # noqa: BLE001
                lv.update(online=False, error=str(e)[:200])
        if args.rollback:
            print("Rules rolled back to", app.rules.rollback())
        if args.check:
            app.updates.check(force=True)
        if args.apply:
            app.updates.apply_pending()
        if args.report:
            r = app.report_url(args.report)
            print("This report contains only:", json.dumps(r["fields"]))
            print("Review it and submit it yourself (Strivo never posts anything):\n" + r["url"])
            return
        doc = app.doctor()
        if args.json:
            print(json.dumps(doc, indent=1, default=str))
            return
        icon = {"healthy": "✅", "recovered": "✅", "degraded": "⚠️ ", "verifying": "⏳", "offline": "⛔", "unpaired": "🔑",
                "paused": "⏸ ", "app-only": "⚪", "unknown": "❔"}
        print("Boxes")
        for b in doc["boxes"]:
            print(f"  {icon.get(b['state'], '?')} {b['name']} ({b['platform']}): {b['state']}"
                  + (f" · {b['error']}" if b["error"] and b["state"] != "healthy" else ""))
        print("Apps")
        for a in doc["apps"] or []:
            print(f"  {icon.get(a['state'], '?')} {a['app']} on {a['device_name']}: {a['state']}"
                  + (f" (expects {a['expected']})" if a.get("expected") else ""))
        if not doc["apps"]:
            print("  (nothing watched yet; health appears after things play)")
        r, u = doc["rules"], doc["updates"]
        print(f"Rules {r['version']} ({r['source']}; bundled {r['bundled']}) · updates: {doc['settings']['rules_updates']}")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_check"])) if u.get("last_check") else "never"
        print(f"Last check for fixes: {when}" + (f" · {u.get('status')}" if u.get("status") else "")
              + (f" · {u['error']}" if u.get("error") else ""))
        if u.get("code_update"):
            print(f"Strivo {u['latest_version']} is available: {u.get('release_url', '')}")
        if u.get("rules_available"):
            print(f"Rules {u['rules_available']} are ready: run `strivo doctor --apply`")
        for p in doc["problems"]:
            print(f"\n{icon.get(p['state'], '?')} {p['what']}: {p['summary']}")
            if p["cause"]:
                print("   likely: " + p["cause"])
            for s in p["steps"]:
                print("   - " + s)
            print(f"   report it: strivo doctor --report '{p['key']}'")
    finally:
        app.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="strivo", description="Strivo: your viewing history, automatically.")
    ap.add_argument("--version", action="version", version=f"strivo {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--data", default=os.environ.get("STRIVO_DATA"), help="data folder (default: %(default)s or "
                                                                              "~/.local/share/strivo)")
        p.set_defaults(fn=fn)
        return p

    p = add("serve", cmd_serve, "run Strivo (collector + web app)")
    p.add_argument("--host", default=os.environ.get("STRIVO_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.environ.get("STRIVO_PORT", "8091")))
    p.add_argument("--no-mdns", action="store_true", help="don't advertise strivo.local")
    add("setup", cmd_setup, "set up boxes, TMDB key and services in the terminal")
    p = add("probe", cmd_probe, "report what each box and app shares (no personal info)")
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--device", help="only this device id")
    p.add_argument("--events", help="analyze an existing events.jsonl instead of listening")
    p.add_argument("--json", action="store_true")
    p = add("discover", cmd_discover, "list boxes and servers found on the network")
    p.add_argument("--timeout", type=float, default=5)
    add("now", cmd_now, "print what each configured box is playing right now")
    p = add("doctor", cmd_doctor, "is tracking healthy? check boxes, apps, rules and fixes")
    p.add_argument("--check", action="store_true", help="check GitHub for fixes now")
    p.add_argument("--apply", action="store_true", help="install a rules update that's waiting (rules_updates=ask)")
    p.add_argument("--rollback", action="store_true", help="go back to the previous rules")
    p.add_argument("--report", metavar="KEY", help="print a prefilled, sanitized GitHub issue link for a problem")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("pyatv", "zeroconf", "adb_shell", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    args.fn(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
