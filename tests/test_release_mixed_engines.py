# -*- coding: utf-8 -*-
"""Выпуск со смешанными движками по горизонтам (17.09): отказ ML-сторожа на одном
горизонте больше не переводит весь выпуск на формулу."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from prognoz.predict import release as release_mod
from prognoz.predict.release import (archive_issue, configured_points,
                                     previous_release_points)
from tests.test_release import _frame


@pytest.fixture(autouse=True)
def _synthetic_points(monkeypatch):
    monkeypatch.setattr(release_mod, "configured_points", lambda: set())


def _write(output_root, issue: date, formula_horizons=()):
    log = output_root / "forecast_log"
    log.mkdir(parents=True, exist_ok=True)
    for horizon in (2, 3, 4):
        frame = _frame(issue, horizon)
        if horizon in formula_horizons:
            frame["engine"] = "baseline"
            frame["_model_variant"] = "baseline"
        target = issue + timedelta(days=horizon)
        frame.to_parquet(log / f"{target.isoformat()}.parquet", index=False)


def test_auto_release_keeps_ml_where_it_passed_and_marks_formula_horizons(tmp_path):
    issue = date(2026, 8, 25)
    output, releases = tmp_path / "output", tmp_path / "releases"
    _write(output, issue, formula_horizons=(3, 4))
    path = archive_issue(output, releases, issue, engine="auto")
    prov = json.loads((path / "provenance.json").read_text(encoding="utf-8"))["provenance"]
    assert prov["engine"] == "mixed"
    assert prov["engines"] == {"2026-08-27": "stacked_guard",
                               "2026-08-28": "baseline", "2026-08-29": "baseline"}
    assert prov["model"]["bundle_lag"] == 3 and prov["model"]["fallback"] is True
    assert prov["model"]["fallback_targets"] == ["2026-08-28", "2026-08-29"]
    assert [t["engine"] for t in prov["targets"]] == ["stacked_guard", "baseline", "baseline"]


def test_strict_engine_still_rejects_a_mixed_issue(tmp_path):
    issue = date(2026, 8, 25)
    output, releases = tmp_path / "output", tmp_path / "releases"
    _write(output, issue, formula_horizons=(3,))
    with pytest.raises(ValueError, match="requested stacked_guard"):
        archive_issue(output, releases, issue, engine="stacked_guard")


def test_auto_release_of_pure_ml_or_pure_formula_keeps_old_labels(tmp_path):
    issue = date(2026, 8, 25)
    _write(tmp_path / "ml", issue)
    prov = json.loads((archive_issue(tmp_path / "ml", tmp_path / "r1", issue, engine="auto")
                       / "provenance.json").read_text(encoding="utf-8"))["provenance"]
    assert prov["engine"] == "stacked_guard" and "fallback" not in prov["model"]
    _write(tmp_path / "f", issue, formula_horizons=(2, 3, 4))
    prov = json.loads((archive_issue(tmp_path / "f", tmp_path / "r2", issue, engine="auto")
                       / "provenance.json").read_text(encoding="utf-8"))["provenance"]
    assert prov["engine"] == "baseline" and prov["model"]["fallback"] is True


def test_configured_points_come_from_points_map(monkeypatch, tmp_path):
    (tmp_path / "points_map.csv").write_text(
        "\n".join(["point_key,canon_name,role,merged_into",
                   "a,А,market,",
                   "b,Б,market_hub,",
                   "c,В (подразделение),merged,a",
                   "d,Г,market_future,", ""]),
        encoding="utf-8")
    monkeypatch.setenv("PROGNOZ_CONFIG_DIR", str(tmp_path))
    assert configured_points() == {"a", "b"}


def test_new_point_in_config_without_data_does_not_block_release(tmp_path, monkeypatch):
    """18.09: точка появляется в конфиге раньше, чем в данных (справочник приезжает
    со снимком базы) — выпуск обязан выйти, разрыв уходит в points_pending."""
    monkeypatch.setattr(release_mod, "configured_points",
                        lambda: {f"point-{i}" for i in range(5)} | {"point-new"})
    issue = date(2026, 8, 25)
    _write(tmp_path / "output", issue)
    path = archive_issue(tmp_path / "output", tmp_path / "releases", issue, engine="auto")
    prov = json.loads((path / "provenance.json").read_text(encoding="utf-8"))["provenance"]
    assert prov["points_pending"] == ["point-new"]
    assert len(prov["point_keys"]) == 5


def test_release_refuses_to_lose_a_point_of_the_previous_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(release_mod, "configured_points", lambda: set())
    issue = date(2026, 8, 25)
    output, releases = tmp_path / "output", tmp_path / "releases"
    _write(output, issue)
    archive_issue(output, releases, issue, engine="auto")
    assert previous_release_points(releases) == {f"point-{i}" for i in range(5)}

    later = date(2026, 8, 26)
    out2 = tmp_path / "output2"
    log = out2 / "forecast_log"
    log.mkdir(parents=True)
    for horizon in (2, 3, 4):
        frame = _frame(later, horizon)
        frame = frame[frame["_point_key"] != "point-4"]          # точка пропала из выпуска
        (frame).to_parquet(log / f"{(later + timedelta(days=horizon)).isoformat()}.parquet",
                           index=False)
    # пока точка числится живой в конфиге — её пропажа это отказ
    monkeypatch.setattr(release_mod, "configured_points",
                        lambda: {f"point-{i}" for i in range(5)})
    with pytest.raises(ValueError, match="lost points"):
        archive_issue(out2, releases, later, engine="auto")

    # а если точку закрыли и убрали из конфига — выпуск без неё законен
    monkeypatch.setattr(release_mod, "configured_points",
                        lambda: {f"point-{i}" for i in range(4)})
    path = archive_issue(out2, releases, later, engine="auto")
    prov = json.loads((path / "provenance.json").read_text(encoding="utf-8"))["provenance"]
    assert "point-4" not in prov["point_keys"] and prov["points_pending"] == []


def test_point_outside_points_map_warns_but_publishes(tmp_path, monkeypatch, capsys):
    """21.09: конфиг может отстать от справочника базы (dim_point не пересобирает ни
    один ночной цикл) — лишняя точка не повод оставить сеть без заявки."""
    monkeypatch.setattr(release_mod, "configured_points", lambda: {"point-0", "point-1"})
    issue = date(2026, 8, 25)
    _write(tmp_path / "output", issue)
    path = archive_issue(tmp_path / "output", tmp_path / "releases", issue, engine="auto")
    prov = json.loads((path / "provenance.json").read_text(encoding="utf-8"))["provenance"]
    assert prov["points_unknown"] == ["point-2", "point-3", "point-4"]
    assert "ТРЕВОГА" in capsys.readouterr().err

    # совсем чужой конфиг (ни одной общей точки) — по-прежнему отказ
    monkeypatch.setattr(release_mod, "configured_points", lambda: {"чужая-точка"})
    with pytest.raises(ValueError, match="no points from points_map"):
        archive_issue(tmp_path / "output", tmp_path / "releases2", issue, engine="auto")
