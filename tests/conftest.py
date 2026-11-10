import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from fakes import FakeTMDB  # noqa: E402
from strivo.config import DataDir  # noqa: E402


@pytest.fixture
def data(tmp_path):
    return DataDir(tmp_path / "data")


@pytest.fixture
def tmdb():
    return FakeTMDB()


def write_events(data, rows):
    with open(data.file("events.jsonl"), "a") as f:
        for r in rows:
            base = dict(kind="play", src="appletv", app=None, state="Playing", title=None, series=None, season=None,
                        episode=None, artist=None, album=None, genre=None, pos=None, total=None)
            base.update(r)
            f.write(json.dumps(base) + "\n")
