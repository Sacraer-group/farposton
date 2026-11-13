from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest

from prognoz.assortment import provenance as assortment_provenance
from prognoz.predict import corrections_observations as corrections


def _release(tmp_path, *, issue="2026-08-25", qty=10):
    release = tmp_path / "releases" / issue
    log = release / "forecast_log"
    log.mkdir(parents=True)
    path = log / "2026-08-27.parquet"
    frame = pd.DataFrame({
        "_point_key": ["point-a"], "_sku_key": ["sku-a"],
        "Точка": ["Точка А"], "Позиция": ["Позиция А"],
        "Прогноз, шт": [qty], "target_date": [date(2026, 8, 27)],
        "_issue_date": [date.fromisoformat(issue)],
        "_horizon_days": [(date(2026, 8, 27) - date.fromisoformat(issue)).days],
        "engine": ["stacked_guard"], "version": ["test-v1"],
    })
    frame.to_parquet(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    policy = assortment_provenance()
    manifest = {
        "issue_id": issue,
        "files": [{"target_date": "2026-08-27",
                   "path": "forecast_log/2026-08-27.parquet",
                   "rows": 1, "bytes": path.stat().st_size,
                   "sha256": digest}],
        "provenance": {
            "point_keys": ["point-a"],
            "assortment": policy,
        },
    }
    (release / "provenance.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return tmp_path / "releases", f"sha256:{digest}"


def _outcome(*, settled=True, gap=False,
             matured="2026-08-30T10:00:00+10:00"):
    return pd.DataFrame([{
        "point_key": "point-a", "sku_key": "sku-a",
        "trade_day": date(2026, 8, 27), "sold_units": 11.0,
        "lost_units": 2.0, "supply_gap": gap, "wo_settled": settled,
        "avg_price": 300.0, "matured_at": matured,
    }])


def _cutoffs(release_id):
    return pd.DataFrame([{
        "release_id": release_id, "target_date": "2026-08-27",
        "decision_cutoff_at": "2026-08-25T18:00:00+10:00",
        "cutoff_policy_version": "pilot-deadline-v1",
    }])


def _edits(release_id):
    return pd.DataFrame([
        {"id": 1, "target_date": "2026-08-27", "release_id": release_id,
         "point_key": "point-a", "sku_key": "sku-a", "qty_user": 12.0,
         "edited_at": "2026-08-25T17:00:00+10:00", "actor": "secret-user",
         "ip": "10.0.0.7"},
        {"id": 2, "target_date": "2026-08-27", "release_id": release_id,
         "point_key": "point-a", "sku_key": "sku-a", "qty_user": 14.0,
         "edited_at": "2026-08-25T17:59:00+10:00", "actor": "secret-user",
         "ip": "10.0.0.7"},
        {"id": 3, "target_date": "2026-08-27", "release_id": release_id,
         "point_key": "point-a", "sku_key": "sku-a", "qty_user": 99.0,
         "edited_at": "2026-08-25T18:01:00+10:00", "actor": "secret-user",
         "ip": "10.0.0.7"},
    ])


def _build(tmp_path, edits=None, outcome=None, cutoffs=None):
    root, release_id = _release(tmp_path)
    return corrections.build_observations(
        edits if edits is not None else _edits(release_id),
        corrections.build_release_index(root),
        outcome if outcome is not None else _outcome(),
        cutoffs if cutoffs is not None else _cutoffs(release_id),
        outcome_snapshot_sha256="a" * 64,
    ), release_id


def test_builds_hash_bound_causal_observation_without_identity(tmp_path):
    got, release_id = _build(tmp_path)

    assert len(got) == 1
    row = got.iloc[0]
    assert row["edit_id"] == 2
    assert row["base_qty"] == 10
    assert row["human_qty"] == 14
    assert row["delta_qty"] == 4
    assert row["demand"] == 13
    assert bool(row["eligible"]) is True
    assert row["release_id"] == release_id
    assert len(row["release_manifest_sha256"]) == 64
    assert row["matured_at"].endswith("+00:00")
    assert row["edit_timestamp_basis"] == "offset_aware"
    assert "actor" not in got.columns
    assert "ip" not in got.columns


def test_naive_legacy_timestamp_is_never_causal(tmp_path):
    root, release_id = _release(tmp_path)
    edits = _edits(release_id).head(1).copy()
    edits["edited_at"] = "2026-08-25T17:00:00"
    got = corrections.build_observations(
        edits, corrections.build_release_index(root), _outcome(),
        _cutoffs(release_id), outcome_snapshot_sha256="a" * 64)

    assert not bool(got.iloc[0]["eligible"])
    assert got.iloc[0]["rejection_reason"] == "edit_timestamp_timezone_unknown"


def test_missing_maturity_time_stays_research_only(tmp_path):
    got, _release_id = _build(tmp_path, outcome=_outcome(matured=None))

    assert not bool(got.iloc[0]["eligible"])
    assert "outcome_maturity_time_unknown" in got.iloc[0]["rejection_reason"]


def test_outcome_cannot_be_mature_at_decision_time(tmp_path):
    got, _release_id = _build(
        tmp_path, outcome=_outcome(matured="2026-08-25T18:00:00+10:00"))

    assert not bool(got.iloc[0]["eligible"])
    assert "outcome_maturity_before_or_at_decision" in got.iloc[0][
        "rejection_reason"]


def test_nullable_quality_flags_are_ineligible_not_truthy(tmp_path):
    outcome = _outcome()
    outcome["wo_settled"] = outcome["wo_settled"].astype("boolean")
    outcome["supply_gap"] = outcome["supply_gap"].astype("boolean")
    outcome.loc[0, "wo_settled"] = pd.NA
    outcome.loc[0, "supply_gap"] = pd.NA
    got, _release_id = _build(tmp_path, outcome=outcome)

    reasons = set(got.iloc[0]["rejection_reason"].split(";"))
    assert "outcome_not_mature" in reasons
    assert "outcome_quality_unknown" in reasons
    assert not bool(got.iloc[0]["eligible"])


def test_release_policy_mismatch_is_ineligible(tmp_path):
    root, release_id = _release(tmp_path)
    index = corrections.build_release_index(root)
    index[release_id] = replace(
        index[release_id], assortment_policy_sha256="b" * 64)
    got = corrections.build_observations(
        _edits(release_id), index, _outcome(), _cutoffs(release_id),
        outcome_snapshot_sha256="a" * 64)

    assert not bool(got.iloc[0]["eligible"])
    assert "assortment_policy_mismatch" in got.iloc[0]["rejection_reason"]


def test_release_manifest_change_after_index_is_rejected(tmp_path):
    root, release_id = _release(tmp_path)
    index = corrections.build_release_index(root)
    manifest_path = index[release_id].manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance"]["point_keys"] = ["tampered-point"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Provenance выпуска изменился"):
        corrections.build_observations(
            _edits(release_id), index, _outcome(), _cutoffs(release_id),
            outcome_snapshot_sha256="a" * 64)


def test_exact_release_hash_selects_correct_same_target_issue(tmp_path):
    root, first_id = _release(tmp_path, issue="2026-08-24", qty=5)
    _root, second_id = _release(tmp_path, issue="2026-08-25", qty=20)
    edits = _edits(second_id)
    got = corrections.build_observations(
        edits, corrections.build_release_index(root), _outcome(),
        _cutoffs(second_id), outcome_snapshot_sha256="a" * 64)

    assert first_id != second_id
    assert got.iloc[0]["release_id"] == second_id
    assert got.iloc[0]["base_qty"] == 20


def test_unresolved_release_is_retained_but_ineligible(tmp_path):
    root, _release_id = _release(tmp_path)
    edits = pd.DataFrame([{
        "id": 1, "target_date": "2026-08-27", "release_id": "sha256:missing",
        "point_key": "point-a", "sku_key": "sku-a", "qty_user": 99.0,
        "edited_at": "2026-08-25T17:00:00+10:00",
    }])
    got = corrections.build_observations(
        edits, corrections.build_release_index(root), _outcome(),
        pd.DataFrame(columns=corrections.CUTOFF_COLUMNS),
        outcome_snapshot_sha256="a" * 64)

    assert got.iloc[0]["rejection_reason"] == "release_unresolved"


def test_readiness_counts_distinct_weeks_and_exact_points():
    dates = [date(2026, 1, 5) + timedelta(weeks=i) for i in range(12)]
    observations = pd.DataFrame([
        {"eligible": True, "target_date": day.isoformat(), "point_key": point,
         "rejection_reason": ""}
        for day in dates for point in ("point-a", "point-b")
    ])
    ready = corrections.readiness_report(
        observations, expected_point_keys={"point-a", "point-b"},
        expected_point_count=2, min_active_weeks=12, min_rows=24,
        min_rows_per_point=12)
    assert ready["status"] == "ready_for_walk_forward_challenger"
    assert ready["distinct_active_weeks"] == 12

    sparse = observations.iloc[[0, -1]].copy()
    not_ready = corrections.readiness_report(
        sparse, expected_point_keys={"point-a", "point-b"},
        expected_point_count=2, min_active_weeks=12, min_rows=2,
        min_rows_per_point=1)
    assert not_ready["distinct_active_weeks"] == 2
    assert not_ready["status"] == "collect_more_observations"

    wrong_points = observations.replace({"point-b": "test-point"})
    wrong = corrections.readiness_report(
        wrong_points, expected_point_keys={"point-a", "point-b"},
        expected_point_count=2, min_active_weeks=12, min_rows=24,
        min_rows_per_point=12)
    assert wrong["checks"]["exact_expected_points"] is False


def test_readiness_requires_time_coverage_on_every_point():
    dates = [date(2026, 1, 5) + timedelta(weeks=i) for i in range(12)]
    observations = pd.DataFrame(
        [{"eligible": True, "target_date": day.isoformat(),
          "point_key": "point-a", "rejection_reason": ""} for day in dates]
        + [{"eligible": True, "target_date": dates[-1].isoformat(),
            "point_key": "point-b", "rejection_reason": ""} for _ in range(12)])

    report = corrections.readiness_report(
        observations, expected_point_keys={"point-a", "point-b"},
        expected_point_count=2, min_active_weeks=12, min_rows=24,
        min_rows_per_point=12)

    assert report["distinct_active_weeks"] == 12
    assert report["active_weeks_per_point"]["point-b"] == 1
    assert report["checks"]["minimum_distinct_active_weeks_per_point"] is False
    assert report["status"] == "collect_more_observations"


def test_readiness_fails_closed_for_four_point_configuration():
    dates = [date(2026, 1, 5) + timedelta(weeks=i) for i in range(12)]
    points = {f"point-{index}" for index in range(4)}
    observations = pd.DataFrame([
        {"eligible": True, "target_date": day.isoformat(),
         "point_key": point, "rejection_reason": ""}
        for day in dates for point in points
    ])

    report = corrections.readiness_report(
        observations, expected_point_keys=points, min_active_weeks=12,
        min_rows=48, min_rows_per_point=12)

    assert report["checks"]["expected_point_config_cardinality"] is False
    assert report["status"] == "collect_more_observations"


def test_expected_points_config_is_versioned_and_exactly_six(tmp_path):
    path = tmp_path / "points.json"
    path.write_text(json.dumps({
        "policy_version": "six-markets-v1",
        "point_keys": [f"point-{index}" for index in range(6)],
    }), encoding="utf-8")

    config = corrections.read_expected_points(path)

    assert len(config["point_keys"]) == 6            # 18.09: + Точка 6
    assert config["policy_version"] == "six-markets-v1"
    assert len(config["sha256"]) == 64

    path.write_text(json.dumps({
        "policy_version": "five-markets-is-not-enough",
        "point_keys": [f"point-{index}" for index in range(5)],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Ожидалось точек: 6"):
        corrections.read_expected_points(path)


def test_read_edits_is_read_only_and_excludes_actor_ip(tmp_path):
    path = tmp_path / "edits.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE edits (id INTEGER, target_date TEXT, release_id TEXT, "
            "point_key TEXT, sku_key TEXT, qty_user REAL, edited_at TEXT, "
            "actor TEXT, ip TEXT)")
        connection.execute(
            "INSERT INTO edits VALUES (1, '2026-08-27', 'sha256:x', 'p', 's', "
            "4, '2026-08-25T17:00:00+10:00', 'user', '127.0.0.1')")
    before = path.stat().st_mtime_ns

    got = corrections.read_edits_read_only(path)

    assert list(got.columns) == list(corrections.SAFE_EDIT_COLUMNS)
    assert path.stat().st_mtime_ns == before


def test_publish_run_is_one_hashed_directory(tmp_path):
    observations = pd.DataFrame([{"eligible": False}])
    published = corrections.publish_run(
        tmp_path, observations, {"status": "collect_more_observations"},
        {"test": True})

    assert published.is_dir()
    assert not any(path.name.startswith(".") for path in tmp_path.iterdir())
    manifest = json.loads(
        (published / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = published / item["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
