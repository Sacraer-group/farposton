from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from prognoz.predict import stacked_guard_core as core


SCRIPT = Path(__file__).parents[1] / "scripts" / "train-stacked-guard.py"


def _module():
    spec = importlib.util.spec_from_file_location("train_stacked_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _oof_frame() -> pd.DataFrame:
    day = pd.Timestamp("2026-01-10")
    row = {name: 1.0 for name in core.META_NUMERIC}
    row.update({
        "point_key": "point",
        "sku_key": "sku",
        "grp": "group",
        "day": day,
        "demand": 8.0,
        "price": 200.0,
        "hgb_qty": 7.0,
        "q67_qty": 8.0,
        "q70_qty": 8.0,
        "hgb_train_cutoff": day - pd.Timedelta(days=3),
        "q67_train_cutoff": day - pd.Timedelta(days=3),
        "q70_train_cutoff": day - pd.Timedelta(days=3),
        "hgb_feature_as_of": day - pd.Timedelta(days=3),
        "q67_feature_as_of": day - pd.Timedelta(days=3),
        "q70_feature_as_of": day - pd.Timedelta(days=3),
        "q67_block_id": "block",
        "q70_block_id": "block",
        "hgb_source_sha256": "a" * 64,
    })
    return pd.DataFrame([row])


def test_oof_requires_hgb_provenance(monkeypatch):
    trainer = _module()
    frame = _oof_frame().drop(columns=["hgb_train_cutoff"])
    monkeypatch.setattr(trainer.pd, "read_parquet", lambda _: frame)
    with pytest.raises(RuntimeError, match="misses columns"):
        trainer.load_oof(Path("unused.parquet"), date(2026, 1, 31))


def test_oof_rejects_future_hgb_cutoff(monkeypatch):
    trainer = _module()
    frame = _oof_frame()
    frame["hgb_train_cutoff"] = frame["day"] - pd.Timedelta(days=2)
    monkeypatch.setattr(trainer.pd, "read_parquet", lambda _: frame)
    with pytest.raises(RuntimeError, match="temporal contract"):
        trainer.load_oof(Path("unused.parquet"), date(2026, 1, 31))


def test_oof_accepts_exact_hgb_contract(monkeypatch):
    trainer = _module()
    frame = _oof_frame()
    monkeypatch.setattr(trainer.pd, "read_parquet", lambda _: frame)
    loaded, source_sha = trainer.load_oof(
        Path("unused.parquet"), date(2026, 1, 31))
    assert source_sha == "a" * 64
    assert loaded.loc[0, "residual_label"] == 1.0
