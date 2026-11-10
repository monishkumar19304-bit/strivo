"""The history lives in SQLite forever: upgrade from events.jsonl, no duplicates, no loss, export round-trips."""
import io
import json
import os
import sqlite3
import threading

from conftest import read_events, write_events
from strivo.cli import main as cli_main
from strivo.collector import EventLog
from strivo.store import EventStore


def ev(i, **kw):
    return dict(dict(ts=f"2025-01-01T20:{i // 60:02d}:{i % 60:02d}", dev="den", title=f"Show {i}"), **kw)


def test_upgrade_imports_jsonl_once_and_keeps_the_file(data):
    write_events(data, [ev(i) for i in range(5)])
    before = open(data.file("events.jsonl")).read()
    assert len(read_events(data)) == 5
    EventLog.close_all()
    assert len(read_events(data)) == 5                      # reopening doesn't import again
    assert open(data.file("events.jsonl")).read() == before  # the old file is left exactly as it was
    assert oct(os.stat(data.file("strivo.db")).st_mode)[-3:] == "600"


def test_lines_appended_later_are_picked_up_but_half_lines_wait(data):
    write_events(data, [ev(0)])
    assert len(read_events(data)) == 1
    with open(data.file("events.jsonl"), "a") as f:
        f.write(json.dumps(dict(kind="play", **ev(1))) + "\n")
        f.write('{"kind": "play", "ts": "2025-01-01T21:00:00", "title": "half')     # still being written
    assert [e["title"] for e in read_events(data)] == ["Show 0", "Show 1"]
    with open(data.file("events.jsonl"), "a") as f:
        f.write(' done"}\n')
    assert [e["title"] for e in read_events(data)][-1] == "half done"


def test_new_events_go_to_the_db_not_the_jsonl(data):
    log = EventLog(data)
    log.write(dict(kind="play", **ev(1)))
    assert not os.path.exists(data.file("events.jsonl"))
    assert read_events(data)[0]["title"] == "Show 1"
    con = sqlite3.connect(data.file("strivo.db"))
    assert con.execute("SELECT dev, kind FROM events").fetchall() == [("den", "play")]


def test_concurrent_writers_lose_nothing(data):
    log = EventLog(data)

    def w(k):
        for i in range(200):
            log.write(dict(kind="play", **ev(i, dev=f"d{k}")))
    ts = [threading.Thread(target=w, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(read_events(data)) == 800


def test_core_reads_only_new_rows(data, tmdb):
    from strivo.core import Core
    core = Core(data, transport=tmdb)
    core.log.write(dict(kind="play", state="Playing", app="Hulu", **ev(1)))
    assert len(core.sessions()[0]) == 1
    sb = core._sb
    core.log.write(dict(kind="play", state="Playing", app="Hulu", **ev(200)))
    assert [x["n"]["series"] for x in core.sessions()[0]] == ["Show 1", "Show 200"]
    assert core._sb is sb and core._ev_last == 2               # incremental: same builder, only the new row read
    core.log.write(dict(kind="play", state="Playing", app="Hulu", **ev(100)))   # older than what's there
    assert [x["n"]["series"] for x in core.sessions()[0]] == ["Show 1", "Show 100", "Show 200"]
    assert core._sb is not sb                                  # out of order: rebuilt from the database


def test_shorter_jsonl_is_not_reimported(data):
    write_events(data, [ev(i) for i in range(3)])
    assert len(read_events(data)) == 3
    open(data.file("events.jsonl"), "w").write("")      # someone emptied it: nothing is lost or duplicated
    assert len(read_events(data)) == 3


def test_export_round_trip(data, capsys):
    write_events(data, [ev(i) for i in range(3)])
    out = data.path / "out.jsonl"
    cli_main(["export", "--data", str(data.path), str(out)])
    rows = [json.loads(x) for x in open(out)]
    assert [r["title"] for r in rows] == ["Show 0", "Show 1", "Show 2"]
    # and an export can seed a fresh install
    fresh = data.path / "fresh"
    fresh.mkdir()
    (fresh / "events.jsonl").write_text(out.read_text())
    st = EventStore(fresh)
    assert st.count() == 3 and st.span() == ("2025-01-01T20:00:00", "2025-01-01T20:00:02")
    buf = io.StringIO()
    assert st.export_jsonl(buf) == 3
    st.close()
