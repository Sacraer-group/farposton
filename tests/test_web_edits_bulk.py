from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from web import edits


TARGET_DATE = "2099-01-02"
RELEASE_ID = "sha256:" + "a" * 64


@pytest.fixture()
def isolated_edits(monkeypatch, tmp_path):
    monkeypatch.setattr(edits, "DB_PATH", tmp_path / "web_edits.sqlite")
    return edits.DB_PATH


def test_bulk_save_is_atomic_and_returns_revisions(isolated_edits):
    result = edits.add_edits_bulk(
        TARGET_DATE,
        RELEASE_ID,
        [("point-a", "sku-1", 12.0, 0), ("point-a", "sku-2", 7.0, 0)],
        "127.0.0.1",
        "manager-a",
        "batch-success-1",
    )

    state = edits.get_effective_state(TARGET_DATE, RELEASE_ID)
    assert result["count"] == 2
    assert result["replayed"] is False
    assert state[("point-a", "sku-1")][0] == 12.0
    assert state[("point-a", "sku-2")][0] == 7.0
    assert state[("point-a", "sku-1")][1] != state[("point-a", "sku-2")][1]
    history = edits.get_history(TARGET_DATE)
    assert {row["actor"] for row in history} == {"manager-a"}
    assert {row["batch_id"] for row in history} == {"batch-success-1"}
    assert {row["release_id"] for row in history} == {RELEASE_ID}
    assert {row["edited_at"] for row in history} == {result["edited_at"]}
    assert result["edited_at"].endswith("+00:00")


def test_null_reset_remains_in_history_but_not_effective(isolated_edits):
    edits.add_edits_bulk(
        TARGET_DATE, RELEASE_ID, [("point-a", "sku-1", 12.0, 0)], None,
        "manager-a", "batch-set-1")
    revision = edits.get_effective_state(
        TARGET_DATE, RELEASE_ID)[("point-a", "sku-1")][1]

    edits.add_edits_bulk(
        TARGET_DATE, RELEASE_ID,
        [("point-a", "sku-1", None, revision)], None,
        "manager-a", "batch-reset-1")

    assert ("point-a", "sku-1") not in edits.get_effective(
        TARGET_DATE, RELEASE_ID)
    qty, reset_revision = edits.get_effective_state(
        TARGET_DATE, RELEASE_ID)[("point-a", "sku-1")]
    assert qty is None
    assert reset_revision > revision
    assert len(edits.get_history(TARGET_DATE)) == 2


def test_retry_is_idempotent_and_mismatched_reuse_is_rejected(isolated_edits):
    entries = [("point-a", "sku-1", 12.0, 0), ("point-a", "sku-2", 7.0, 0)]
    first = edits.add_edits_bulk(
        TARGET_DATE, RELEASE_ID, entries, None, "manager-a", "batch-retry-1")
    replay = edits.add_edits_bulk(
        TARGET_DATE, RELEASE_ID, list(reversed(entries)), None,
        "manager-a", "batch-retry-1")

    assert replay == {**first, "replayed": True}
    assert len(edits.get_history(TARGET_DATE)) == 2
    with pytest.raises(edits.BatchReplayMismatch):
        edits.add_edits_bulk(
            TARGET_DATE, RELEASE_ID, [("point-a", "sku-1", 13.0, 0)], None,
            "manager-a", "batch-retry-1")
    assert len(edits.get_history(TARGET_DATE)) == 2


def test_stale_revision_rejects_whole_batch(isolated_edits):
    edits.add_edits_bulk(
        TARGET_DATE, RELEASE_ID, [("point-a", "sku-1", 12.0, 0)], None,
        "manager-a", "batch-current-1")

    with pytest.raises(edits.EditConflictError):
        edits.add_edits_bulk(
            TARGET_DATE,
            RELEASE_ID,
            [("point-a", "sku-1", 13.0, 0), ("point-a", "sku-2", 7.0, 0)],
            None,
            "manager-a",
            "batch-stale-1",
        )

    assert edits.get_effective(TARGET_DATE, RELEASE_ID) == {
        ("point-a", "sku-1"): 12.0}
    assert len(edits.get_history(TARGET_DATE)) == 1


def test_database_error_rolls_back_every_row_and_batch_marker(isolated_edits):
    con = edits._connect()
    try:
        con.executescript(
            """
            CREATE TRIGGER fail_bad_sku BEFORE INSERT ON edits
            WHEN NEW.sku_key = 'bad'
            BEGIN
                SELECT RAISE(ABORT, 'injected failure');
            END;
            """)
        con.commit()
    finally:
        con.close()

    with pytest.raises(sqlite3.IntegrityError):
        edits.add_edits_bulk(
            TARGET_DATE,
            RELEASE_ID,
            [("point-a", "good", 1.0, 0), ("point-a", "bad", 2.0, 0)],
            None,
            "manager-a",
            "batch-rollback-1",
        )

    assert edits.get_history(TARGET_DATE) == []
    con = edits._connect()
    try:
        assert con.execute("SELECT count(*) FROM edit_batches").fetchone()[0] == 0
    finally:
        con.close()


def test_legacy_schema_migrates_safely_under_concurrent_first_connections(
        isolated_edits):
    con = sqlite3.connect(str(isolated_edits))
    try:
        con.executescript(
            """
            CREATE TABLE edits (
                id INTEGER PRIMARY KEY,
                target_date TEXT NOT NULL,
                point_key TEXT NOT NULL,
                sku_key TEXT NOT NULL,
                qty_user REAL,
                edited_at TEXT NOT NULL,
                ip TEXT
            );
            """)
        con.commit()
    finally:
        con.close()

    def connect_and_close(_index):
        connection = edits._connect()
        connection.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(connect_and_close, range(12)))

    con = edits._connect()
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(edits)")}
        batch_columns = {
            row[1] for row in con.execute("PRAGMA table_info(edit_batches)")}
        assert {"actor", "batch_id", "release_id"}.issubset(columns)
        assert "release_id" in batch_columns
    finally:
        con.close()


def test_edits_do_not_cross_to_a_reissued_forecast(isolated_edits):
    edits.add_edits_bulk(
        TARGET_DATE, RELEASE_ID, [("point-a", "sku-1", 12.0, 0)], None,
        "manager-a", "batch-release-a")
    next_release = "sha256:" + "b" * 64

    assert edits.get_effective(TARGET_DATE, RELEASE_ID) == {
        ("point-a", "sku-1"): 12.0}
    assert edits.get_effective(TARGET_DATE, next_release) == {}


def test_pre_upgrade_edit_is_bound_once_to_first_observed_release(isolated_edits):
    edited_at = edits.add_edit(TARGET_DATE, "point-a", "sku-1", 11.0, None)
    next_release = "sha256:" + "b" * 64

    assert edited_at.endswith("+00:00")
    assert edits.get_effective(TARGET_DATE, RELEASE_ID) == {
        ("point-a", "sku-1"): 11.0}
    assert edits.get_effective(TARGET_DATE, next_release) == {}
    assert edits.get_history(TARGET_DATE)[0]["release_id"] == RELEASE_ID
