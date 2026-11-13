from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from prognoz.predict import release as release_mod
from prognoz.predict.release import archive_issue, promote_release
from web import data as web_data


@pytest.fixture(autouse=True)
def _synthetic_points(monkeypatch):
    """Фикстуры используют синтетические ключи (point-0…4) — боевой points_map к ним
    неприменим; проверку «чужих точек нет» отключаем, «ничего не пропало» остаётся."""
    monkeypatch.setattr(release_mod, "configured_points", lambda: set())


PIN = "a" * 64


def _frame(issue: date, horizon: int) -> pd.DataFrame:
    target = issue + timedelta(days=horizon)
    points = [f"point-{i}" for i in range(5)]
    return pd.DataFrame({
        "Точка": [f"Тестовая точка {i}" for i in range(5)],
        "Позиция": ["Тестовая позиция"] * 5,
        "Прогноз, шт": [7] * 5,
        "engine": ["stacked_guard"] * 5,
        "version": ["test-d234-v1"] * 5,
        "model_cutoff": ["2026-08-20"] * 5,
        "target_date": [target] * 5,
        "_issue_date": [issue] * 5,
        "_horizon_days": [horizon] * 5,
        "_feature_lag": [horizon + 1] * 5,
        "_bundle_lag": [3] * 5,
        "_bundle_manifest_sha256": [PIN] * 5,
        "_model_variant": ["stacked_consensus"] * 5,
        "_point_key": points,
    })


def _write_flat_issue(output_root, issue):
    log = output_root / "forecast_log"
    log.mkdir(parents=True)
    for horizon in (2, 3, 4):
        target = issue + timedelta(days=horizon)
        _frame(issue, horizon).to_parquet(
            log / f"{target.isoformat()}.parquet", index=False)


def test_archive_issue_is_complete_hashed_and_append_only(tmp_path):
    issue = date(2026, 8, 25)
    output, releases = tmp_path / "output", tmp_path / "releases"
    _write_flat_issue(output, issue)

    path = archive_issue(output, releases, issue)
    manifest = json.loads((path / "provenance.json").read_text(encoding="utf-8"))
    assert [item["target_date"] for item in manifest["files"]] == [
        "2026-08-27", "2026-08-28", "2026-08-29"]
    assert all(len(item["sha256"]) == 64 and item["bytes"] > 0
               for item in manifest["files"])
    model = manifest["provenance"]["model"]
    assert model["bundle_lag"] == 3
    assert model["bundle_manifest_sha256"] == PIN
    assert model["model_cutoffs"] == ["2026-08-20"]
    assert model["runtime_adapter"] == "latest_complete_day_to_frozen_lag3"

    hashes = [item["sha256"] for item in manifest["files"]]
    with pytest.raises(FileExistsError, match="already exists"):
        archive_issue(output, releases, issue)
    unchanged = json.loads((path / "provenance.json").read_text(encoding="utf-8"))
    assert [item["sha256"] for item in unchanged["files"]] == hashes


def test_archive_issue_rejects_wrong_horizon_metadata(tmp_path):
    issue = date(2026, 8, 25)
    output, releases = tmp_path / "output", tmp_path / "releases"
    _write_flat_issue(output, issue)
    path = output / "forecast_log" / "2026-08-29.parquet"
    bad = pd.read_parquet(path)
    bad["_feature_lag"] = 3
    bad.to_parquet(path, index=False)

    with pytest.raises(ValueError, match="must use lag=5"):
        archive_issue(output, releases, issue)
    assert not releases.exists()


def test_archive_rejects_fallback_stale_model_and_wrong_pin(tmp_path):
    issue = date(2026, 8, 25)
    output, releases = tmp_path / "output", tmp_path / "releases"
    _write_flat_issue(output, issue)
    path = output / "forecast_log" / "2026-08-28.parquet"
    bad = pd.read_parquet(path)
    bad["_model_variant"] = "hgb_fail_safe"
    bad.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="not a pure stacked consensus"):
        archive_issue(output, releases, issue)

    bad["_model_variant"] = "stacked_consensus"
    bad["model_cutoff"] = "2026-07-01"
    bad.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="older than 14 days"):
        archive_issue(output, releases, issue)

    bad["model_cutoff"] = "2026-08-20"
    bad.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="scheduler pin"):
        archive_issue(
            output, releases, issue, expected_manifest_sha256="b" * 64)


def test_complete_release_promotes_flat_and_web_prefers_archive(
        tmp_path, monkeypatch):
    issue = date(2026, 8, 25)
    stage = tmp_path / "stage"
    releases = tmp_path / "releases"
    flat = tmp_path / "flat"
    _write_flat_issue(stage, issue)
    for horizon in (2, 3, 4):
        target = issue + timedelta(days=horizon)
        (stage / f"Заявка_{target}.xlsx").write_bytes(b"test-xlsx")
    release = archive_issue(stage, releases, issue)
    promote_release(release, flat, xlsx_source_root=stage)

    monkeypatch.setattr(web_data, "RELEASE_ROOT", releases)
    monkeypatch.setattr(web_data, "FORECAST_LOG", flat / "forecast_log")
    for horizon in (2, 3, 4):
        target = (issue + timedelta(days=horizon)).isoformat()
        assert web_data.forecast_path(target).parent == release / "forecast_log"
        assert (flat / "forecast_log" / f"{target}.parquet").is_file()
        assert (flat / f"Заявка_{target}.xlsx").is_file()


def test_incomplete_stage_never_changes_published_flat(tmp_path):
    issue = date(2026, 8, 25)
    stage, releases, flat = (
        tmp_path / "stage", tmp_path / "releases", tmp_path / "flat")
    _write_flat_issue(stage, issue)
    missing = stage / "forecast_log" / "2026-08-29.parquet"
    missing.unlink()
    published = flat / "forecast_log" / "2026-08-27.parquet"
    published.parent.mkdir(parents=True)
    published.write_bytes(b"previous-complete-issue")
    before = published.read_bytes()

    with pytest.raises(FileNotFoundError, match=r"D\+4"):
        archive_issue(stage, releases, issue)

    assert published.read_bytes() == before
    assert not releases.exists()
