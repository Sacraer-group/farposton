from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from prognoz.assortment import Entry, Policy, filter_frame, filter_pairs, load_policy, sql_exclusion_predicate


def _policy(*entries: Entry) -> Policy:
    return Policy("test", "approved", entries, "test-sha", Path("test.json"))


def _entry(sku: str, point: str | None = None, start: date | None = None) -> Entry:
    return Entry(sku, point, False, False, "none", "test", start, None)


def test_demo_policy_has_no_customer_rules():
    policy = load_policy()
    assert policy.active and policy.version == "demo-v1" and not policy.entries


def test_policy_hash_ignores_line_endings(tmp_path):
    payload = json.dumps({"version": "same", "status": "approved", "entries": []}, indent=2)
    lf, crlf = tmp_path / "lf.json", tmp_path / "crlf.json"
    lf.write_bytes((payload + "\n").encode())
    crlf.write_bytes((payload.replace("\n", "\r\n") + "\r\n").encode())
    assert load_policy(lf).sha256 == load_policy(crlf).sha256


def test_point_and_global_exclusions():
    policy = _policy(_entry("DEMO-SKU-01"), _entry("DEMO-SKU-02", "DEMO-POINT-01"))
    rows = pd.DataFrame([
        {"point_key": point, "sku_key": sku, "date": "2026-09-01"}
        for point in ("DEMO-POINT-01", "DEMO-POINT-02")
        for sku in ("DEMO-SKU-01", "DEMO-SKU-02", "DEMO-SKU-03")
    ])
    out = filter_frame(rows, "metric_scope", date_col="date", policy=policy)
    assert set(zip(out.point_key, out.sku_key)) == {
        ("DEMO-POINT-01", "DEMO-SKU-03"),
        ("DEMO-POINT-02", "DEMO-SKU-02"),
        ("DEMO-POINT-02", "DEMO-SKU-03"),
    }
    assert filter_pairs(set(zip(rows.point_key, rows.sku_key)), "factory_order", policy=policy) == set(zip(out.point_key, out.sku_key))


def test_effective_date_and_sql_predicate():
    policy = _policy(_entry("DEMO-SKU-01", "DEMO-POINT-01", date(2026, 9, 10)))
    rows = pd.DataFrame({"point_key": ["DEMO-POINT-01"] * 2,
                         "sku_key": ["DEMO-SKU-01"] * 2,
                         "date": ["2026-09-09", "2026-09-10"]})
    assert filter_frame(rows, "factory_order", date_col="date", policy=policy).date.tolist() == ["2026-09-09"]
    predicate = sql_exclusion_predicate("factory_order", point_expr="point_key", sku_expr="sku_key", policy=policy)
    assert "DEMO-POINT-01" in predicate and "DEMO-SKU-01" in predicate
