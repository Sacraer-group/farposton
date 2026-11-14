import sqlite3
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from flats import config
from flats.db import connect, init_db
from flats.types import Amount


def test_init_db_creates_schema(tmp_path):
    with closing(connect(tmp_path / "t.db")) as conn:
        init_db(conn)
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert tables == {
        "visitors", "visitor_profiles", "visitor_decisions", "trip_schedules", "trip_request_usage",
        "houses", "offer_houses", "house_sources", "house_facts", "house_contacts", "house_events",
            "import_runs", "offers", "photo_features", "search_profile", "decisions"
        }
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'offers_group'"
        ).fetchone() is not None


def test_init_db_is_idempotent(tmp_path):
    with closing(connect(tmp_path / "t.db")) as conn:
        init_db(conn)
        init_db(conn)


def test_connect_uses_config_path(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "t.db"
    monkeypatch.setenv("FLATS_DB", str(path))
    with closing(connect()) as conn:
        assert path.is_file()
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.parametrize("value", [None, ""])
def test_metrapi_key_requires_nonempty_env(monkeypatch, value):
    monkeypatch.delenv("METRAPI_KEY", raising=False)
    if value is not None:
        monkeypatch.setenv("METRAPI_KEY", value)
    with pytest.raises(RuntimeError, match="^METRAPI_KEY is not set$"):
        config.metrapi_key()


def test_metrapi_key_returns_env(monkeypatch):
    monkeypatch.setenv("METRAPI_KEY", "test-key")
    assert config.metrapi_key() == "test-key"


@pytest.mark.parametrize(
    ("getter", "env_name", "default"),
    [
        (config.db_path, "FLATS_DB", "data/flats.db"),
        (config.photo_dir, "FLATS_PHOTO_DIR", "data/photos"),
    ],
)
def test_config_paths(monkeypatch, tmp_path, getter, env_name, default):
    monkeypatch.delenv(env_name, raising=False)
    assert getter() == Path(default)
    path = tmp_path / "custom"
    monkeypatch.setenv(env_name, str(path))
    assert getter() == path


def test_unknown_amount_is_frozen():
    amount = Amount(rub=None, kind="unknown", value=None, raw=None)
    assert amount.rub is None
    with pytest.raises(FrozenInstanceError):
        amount.rub = 0
