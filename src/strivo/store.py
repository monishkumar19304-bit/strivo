"""The watch history, kept forever in SQLite (`strivo.db` in the data folder).

Why SQLite: it ships with Python, needs no server, is one file, survives crashes and power cuts (WAL), and stays fast
for decades of history. Every "now playing" moment is one row; rows are never deleted or rewritten.

Earlier versions (and NiVo) kept the same rows in `events.jsonl`. On open, any lines of that file not imported yet are
copied in, then the file is left alone, so an old install upgrades by itself and nothing is lost. `strivo export`
writes the whole history back out as JSON Lines at any time: your data is never locked in.

Backups are a job for the computer Strivo runs on (Time Machine, a NAS, restic...). Back up the whole data folder;
when copying `strivo.db` by hand while Strivo runs, take `strivo.db-wal` with it, or use `sqlite3 strivo.db .backup`.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger(__name__)

DB_NAME = "strivo.db"
LEGACY = "events.jsonl"
SCHEMA = 1

_DDL = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id   INTEGER PRIMARY KEY,
    ts   TEXT NOT NULL,          -- ISO time from the box's clock reading
    dev  TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    row  TEXT NOT NULL           -- the full event as JSON
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_dev_ts ON events (dev, ts);
"""


class EventStore:
    """Append-only event history. Thread-safe; one connection guarded by a lock."""

    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder)
        self.path = self.folder / DB_NAME
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_DDL)
        with self.lock:
            if self._meta("schema") is None:
                self._set_meta("schema", str(SCHEMA))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.import_legacy()

    # ---- meta
    def _meta(self, k):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def _set_meta(self, k, v):
        self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    # ---- legacy events.jsonl
    def import_legacy(self) -> int:
        """Copy lines of events.jsonl not imported yet. Safe to call any time; returns rows added."""
        src = self.folder / LEGACY
        try:
            size = src.stat().st_size
        except FileNotFoundError:
            return 0
        with self.lock:
            done = int(self._meta("jsonl_offset") or 0)
            if size < done:          # the file was replaced by a shorter one: don't guess, leave it
                log.warning("%s is shorter than what was imported; not importing it again", src)
                return 0
            if size == done:
                return 0
            rows, offset = [], done
            with open(src, "rb") as f:
                f.seek(done)
                for line in f:
                    if not line.endswith(b"\n"):
                        break        # a line still being written: next time
                    offset += len(line)
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict) and e.get("ts"):
                        rows.append(e)
            self.db.execute("BEGIN")
            try:
                self.db.executemany("INSERT INTO events(ts, dev, kind, row) VALUES(?, ?, ?, ?)",
                                    [_cols(e) for e in rows])
                self._set_meta("jsonl_offset", offset)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        if rows:
            log.info("imported %d events from %s", len(rows), src.name)
        return len(rows)

    # ---- events
    def write(self, row: dict):
        with self.lock:
            self.db.execute("INSERT INTO events(ts, dev, kind, row) VALUES(?, ?, ?, ?)", _cols(row))

    def read(self) -> list[dict]:
        return [e for _, e in self.read_since(0)]

    def read_since(self, after_id: int) -> list[tuple[int, dict]]:
        """(id, event) for every row with id > after_id, oldest first."""
        with self.lock:
            rows = self.db.execute("SELECT id, row FROM events WHERE id > ? ORDER BY id", (after_id,)).fetchall()
        out = []
        for i, r in rows:
            try:
                out.append((i, json.loads(r)))
            except ValueError:
                continue
        return out

    def iter_plays_by_device(self, upto: int, batch: int = 5000):
        """(id, event) for every play event, per device in time order: what a full rebuild of sessions needs,
        streamed so the whole history never has to be in memory at once. Only rows with id <= upto."""
        with self.lock:
            cur = self.db.execute("SELECT id, row FROM events WHERE kind='play' AND id <= ? ORDER BY dev, ts, id", (upto,))
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                for i, r in rows:
                    try:
                        yield i, json.loads(r)
                    except ValueError:
                        continue

    def max_id(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def span(self) -> tuple[str | None, str | None]:
        with self.lock:
            return self.db.execute("SELECT MIN(ts), MAX(ts) FROM events").fetchone()

    def export_jsonl(self, out) -> int:
        """Write every event as one JSON line to the open text file `out`. Returns the number written."""
        n = 0
        for _, e in self.read_since(0):
            out.write(json.dumps(e, default=str) + "\n")
            n += 1
        return n

    def close(self):
        with self.lock:
            self.db.close()


def _cols(e: dict):
    return (str(e.get("ts")), str(e.get("dev") or ""), str(e.get("kind") or ""), json.dumps(e, default=str))
