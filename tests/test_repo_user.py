from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from flats import db, repo_user
from flats.types import Profile


@pytest.fixture
def conn(tmp_path):
    with closing(db.connect(tmp_path / "t.db")) as connection:
        db.init_db(connection)
        yield connection


def test_empty_db_has_default_profile(conn):
    profile = repo_user.get_profile(conn)
    assert profile == Profile(None, None, (), (), False, None)
    assert profile.pets_required is False


def test_profile_round_trip(conn):
    profile = Profile(
        50_000, 120_000, (1, 2), ("Центр", "Эгершельд"), True,
        "https://example.test/sample.jpg",
    )
    repo_user.save_profile(conn, profile)

    restored = repo_user.get_profile(conn)
    assert restored == profile
    assert restored.pets_required is True
    row = conn.execute("SELECT * FROM search_profile WHERE id = 1").fetchone()
    assert row["rooms_json"] == "[1, 2]"
    assert row["districts_json"] == '["Центр", "Эгершельд"]'
    assert row["pets_required"] == 1


def test_save_profile_replaces_existing_row(conn):
    repo_user.save_profile(conn, Profile(50_000, 120_000, (1,), ("Центр",), True, "photo"))
    replacement = Profile(None, 0, (), (), False, None)
    repo_user.save_profile(conn, replacement)

    assert repo_user.get_profile(conn) == replacement
    assert repo_user.get_profile(conn).pets_required is False
    rows = conn.execute("SELECT * FROM search_profile").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == 1
    assert rows[0]["pets_required"] == 0


def test_decisions_can_be_undone_in_reverse_order(conn):
    saved_id = repo_user.decide(conn, "farpost", "1", "save")
    skipped_id = repo_user.decide(conn, "farpost", "1", "skip")
    assert skipped_id > saved_id
    assert repo_user.current_decisions(conn) == {("farpost", "1"): "skip"}

    assert repo_user.undo_last(conn) == skipped_id
    assert repo_user.current_decisions(conn) == {("farpost", "1"): "save"}
    assert repo_user.undo_last(conn) == saved_id
    assert repo_user.current_decisions(conn) == {}
    assert repo_user.undo_last(conn) is None
    assert [tuple(row) for row in conn.execute(
        "SELECT id, undone FROM decisions ORDER BY id"
    )] == [(saved_id, 1), (skipped_id, 1)]


def test_empty_db_has_no_decisions_to_undo(conn):
    assert repo_user.current_decisions(conn) == {}
    assert repo_user.undo_last(conn) is None


def test_decide_records_utc_time_without_an_offer(conn):
    before = datetime.now(timezone.utc)
    decision_id = repo_user.decide(conn, "farpost", "1", "save")
    after = datetime.now(timezone.utc)

    assert isinstance(decision_id, int)
    row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    assert (row["source"], row["source_id"], row["action"], row["undone"]) == (
        "farpost", "1", "save", 0,
    )
    created_at = datetime.fromisoformat(row["created_at"])
    assert created_at.utcoffset() == timedelta(0)
    assert before <= created_at <= after
    assert conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0


@pytest.mark.parametrize("action", ["like", "SAVE", ""])
def test_invalid_action_does_not_change_history(conn, action):
    repo_user.decide(conn, "farpost", "1", "save")
    before = conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    with pytest.raises(ValueError):
        repo_user.decide(conn, "farpost", "1", action)
    assert conn.execute("SELECT * FROM decisions ORDER BY id").fetchall() == before


def test_current_decisions_and_undo_use_highest_active_id(conn):
    conn.executemany(
        """INSERT INTO decisions (id, source, source_id, action, created_at, undone)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (10, "farpost", "1", "save", "2099-01-01T00:00:00+00:00", 0),
            (20, "farpost", "1", "skip", "2000-01-01T00:00:00+00:00", 0),
            (30, "other", "1", "save", "2099-01-01T00:00:00+00:00", 0),
            (40, "farpost", "2", "skip", "1990-01-01T00:00:00+00:00", 0),
            (50, "farpost", "1", "save", "2099-01-01T00:00:00+00:00", 1),
            (60, "other", "2", "skip", "2099-01-01T00:00:00+00:00", 1),
        ],
    )
    conn.commit()
    assert repo_user.current_decisions(conn) == {
        ("farpost", "1"): "skip", ("other", "1"): "save", ("farpost", "2"): "skip",
    }
    assert repo_user.undo_last(conn) == 40
    assert repo_user.current_decisions(conn) == {
        ("farpost", "1"): "skip", ("other", "1"): "save",
    }


def test_writes_are_committed(conn, tmp_path):
    profile = Profile(50_000, None, (2,), ("Эгершельд",), False, None)
    repo_user.save_profile(conn, profile)
    with closing(db.connect(tmp_path / "t.db")) as reopened:
        assert repo_user.get_profile(reopened) == profile

    decision_id = repo_user.decide(conn, "farpost", "1", "save")
    with closing(db.connect(tmp_path / "t.db")) as reopened:
        assert repo_user.current_decisions(reopened) == {("farpost", "1"): "save"}

    assert repo_user.undo_last(conn) == decision_id
    with closing(db.connect(tmp_path / "t.db")) as reopened:
        assert repo_user.current_decisions(reopened) == {}
        assert reopened.execute(
            "SELECT undone FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()[0] == 1
