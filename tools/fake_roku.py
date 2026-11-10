#!/usr/bin/env python3
"""A fake Roku (ECP on HTTP) for developing and testing the Roku adapter without a Roku.

    python tools/fake_roku.py --port 8060

Then add it in Strivo as a Roku at 127.0.0.1 (port 8060). Every 20 s it cycles through: Netflix playing with no
title (what most channels do), a channel that does report a title, and the home screen. POSTs (launch, keypress)
are printed so you can see "Play on TV" arrive.
"""
import argparse
import itertools
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from fakes import fake_roku_server  # noqa: E402

SCENES = [
    dict(app="Netflix", app_id="12", player_state="play", pos_ms=60000, dur_ms=2700000, title=None),
    dict(app="The Roku Channel", app_id="151908", player_state="play", pos_ms=5000, dur_ms=1500000,
         title="Sample Episode Title"),
    dict(app=None, app_id=None, player_state="none", pos_ms=0, dur_ms=0, title=None),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8060)
    ap.add_argument("--every", type=float, default=20)
    args = ap.parse_args()
    state = dict(SCENES[0])
    srv = fake_roku_server(state)
    # rebind to the requested port
    srv.close()
    from http.server import ThreadingHTTPServer
    srv.httpd = ThreadingHTTPServer(("0.0.0.0", args.port), srv.httpd.RequestHandlerClass)
    threading.Thread(target=srv.httpd.serve_forever, daemon=True).start()
    print(f"fake Roku ECP on http://127.0.0.1:{args.port}", flush=True)
    seen = 0
    for scene in itertools.cycle(SCENES):
        state.update(scene)
        print("now:", scene["app"] or "home screen", flush=True)
        end = time.time() + args.every
        while time.time() < end:
            posts = state.get("posts", [])
            for p in posts[seen:]:
                print("POST", p, flush=True)
            seen = len(posts)
            time.sleep(0.5)


if __name__ == "__main__":
    main()
