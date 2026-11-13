from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from web import analytics, routes_analytics


def _request(role: str, point_key: str | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/analytics/shelf",
        "headers": [],
        "query_string": b"",
        "session": {
            "auth_at": 1,
            "username": f"test-{role}",
            "role": role,
            "point_key": point_key,
        },
    })


def test_analytics_manager_scope_cannot_be_overridden(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    manager = _request("manager", "point-a")

    assert routes_analytics._scope_point(manager, None) == "Точка А"
    assert routes_analytics._scope_point(manager, "Точка А") == "Точка А"
    with pytest.raises(HTTPException) as exc:
        routes_analytics._scope_point(manager, "Точка Б")
    assert exc.value.status_code == 403


def test_analytics_admin_can_choose_network_or_point():
    admin = _request("admin")
    assert routes_analytics._scope_point(admin, None) is None
    assert routes_analytics._scope_point(admin, "Точка Б") == "Точка Б"


def test_excluded_days_are_admin_only():
    manager = _request("manager", "point-a")
    body = routes_analytics.ExcludedPayload(
        date="2026-01-01", reason="тест", action="add")
    with pytest.raises(HTTPException) as exc:
        routes_analytics.api_excluded_post(manager, body)
    assert exc.value.status_code == 403


def _shelf_frame() -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for offset in range(40):
        day = start + timedelta(days=offset)
        for point in ("Точка А", "Точка Б"):
            rows.append({
                "point_key": f"{point}-key",
                "sku_key": "sku-position",
                "canon_name": point,
                "sku_name": "Позиция",
                "trade_day": day,
                "lost_units": 2.0 if point == "Точка А" else 100.0,
                "lost_rub_margin": 20.0 if point == "Точка А" else 1000.0,
                "wo_units": 1.0,
                "wo_rub_cost": 5.0,
                "wo_settled": offset < 38,
                "ordered_human": 3.0,
                "sold_units": 2.0,
                "t_first": 10.0,
                "t_last": 18.0,
                "close_h": 22.0,
                "outcome": "кончилось рано",
            })
    return pd.DataFrame(rows)


def test_shelf_uses_only_owned_point_and_settled_window(monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    source.with_suffix(".stamp").write_text(
        "method-v1|fingerprint|2026-02-09|2026-02-09", encoding="utf-8")
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(analytics.data, "read_parquet_stable", lambda _path: _shelf_frame())

    got = analytics.shelf_top(20, "Точка А", 28)

    assert got["point"] == "Точка А"
    assert got["source_to"] == "2026-02-09"
    assert got["to"] == "2026-02-07"
    assert got["preliminary_from"] == "2026-02-08"
    assert len(got["daily"]) == 28
    assert got["totals"]["lost_units"] == 56.0
    assert {r["point"] for r in got["deficit"]} == {"Точка А"}
    assert got["deficit_network"] == [{
        "sku_key": "sku-position", "sku": "Позиция", "units": 56.0, "rub": 560,
    }]
    assert got["deficit_details"][0]["hours_left"] == 4.0
    assert got["deficit_details"][0]["tail_rate"] == 0.5


def test_shelf_drilldown_reaches_point_day_and_hour_without_false_zeroes(
        monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    frame = _shelf_frame()
    same_name_other_card = frame.copy()
    same_name_other_card["sku_key"] = "sku-other-card"
    same_name_other_card["lost_units"] = 999.0
    monkeypatch.setattr(
        analytics.data, "read_parquet_stable",
        lambda _path: pd.concat([frame, same_name_other_card], ignore_index=True))

    def fake_operations(sku_key, point, frm, to, selected_day):
        assert (sku_key, point, str(selected_day)) == (
            "sku-position", "Точка А", "2026-02-07")
        return (
            {("Точка А", "2026-02-07"): {
                "ordered_document": 3.0, "collected": None}},
            {("Точка А", "2026-02-07"): 2.0},
            [{"hour": 18, "label": "18:00", "qty": 1.0},
             {"hour": 20, "label": "20:00", "qty": 2.0}],
        )

    monkeypatch.setattr(analytics, "_shelf_operations", fake_operations)
    got = analytics.shelf_drilldown(
        "Позиция", "Точка А", "2026-02-07", 28,
        sku_key="sku-position", as_of="2026-02-07")

    assert {row["point"] for row in got["points"]} == {"Точка А"}
    assert len(got["days"]) == 28
    assert got["selected"]["ordered_document"] == 3.0
    assert got["selected"]["collected"] is None
    assert got["selected"]["supplied"] == 2.0
    assert got["selected"]["lost_units"] == 2.0
    assert got["selected"]["first_sale"] == "10:00"
    assert [row["hour"] for row in got["hourly"]] == [18, 19, 20, 21, 22]
    assert got["hourly"][1]["qty"] == 0.0
    assert got["source_to"] == "2026-02-09"
    assert got["to"] == "2026-02-07"
    assert got["preliminary_from"] == "2026-02-08"
    assert got["missing_days"] == 0
    assert "не подтверждение приёмки" in " ".join(got["notes"])


def test_shelf_drilldown_uses_earliest_first_sale(monkeypatch, tmp_path):
    analytics.shelf_drilldown.cache_clear()
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    frame = _shelf_frame()
    row = frame[(frame["canon_name"] == "Точка А")
                & (frame["trade_day"] == date(2026, 2, 7))].iloc[0].copy()
    frame.loc[row.name, "t_first"] = 14.0
    row["t_first"] = 9.0
    frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(analytics.data, "read_parquet_stable", lambda _path: frame)
    monkeypatch.setattr(
        analytics, "_shelf_operations", lambda *_a: ({}, {}, []))

    got = analytics.shelf_drilldown(
        "Позиция", "Точка А", "2026-02-07", 28,
        sku_key="sku-position", as_of="2026-02-07")

    assert got["selected"]["first_sale"] == "09:00"


def test_admin_shelf_drilldown_keeps_network_as_of_for_lagging_point(
        monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    frame = _shelf_frame()
    frame = frame[~((frame["canon_name"] == "Точка А")
                    & (frame["trade_day"] == date(2026, 2, 7)))]
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(analytics.data, "read_parquet_stable", lambda _path: frame)
    monkeypatch.setattr(
        analytics, "_shelf_operations", lambda *_a: ({}, {}, []))

    got = analytics.shelf_drilldown(
        "Позиция", "Точка А", None, 28,
        sku_key="sku-position", as_of="2026-02-07",
        allow_network_as_of=True)

    assert got["as_of"] == "2026-02-07"
    assert got["from"] == "2026-01-11"
    assert got["to"] == "2026-02-07"
    assert got["missing_days"] == 1
    assert got["missing_from"] == "2026-02-07"
    assert got["missing_to"] == "2026-02-07"


def test_manager_shelf_drilldown_does_not_use_other_points_maturity(
        monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    frame = _shelf_frame()
    frame = frame[~((frame["canon_name"] == "Точка А")
                    & (frame["trade_day"] == date(2026, 2, 7)))]
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(analytics.data, "read_parquet_stable", lambda _path: frame)

    with pytest.raises(ValueError, match="позже последнего зрелого дня"):
        analytics.shelf_drilldown(
            "Позиция", "Точка А", None, 28,
            sku_key="sku-position", as_of="2026-02-07")


def test_shelf_drilldown_rejects_as_of_after_last_settled_day(monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(
        analytics.data, "read_parquet_stable", lambda _path: _shelf_frame())

    with pytest.raises(ValueError, match="позже последнего зрелого дня"):
        analytics.shelf_drilldown(
            "Позиция", None, None, 28,
            sku_key="sku-position", as_of="2026-02-08")


def test_shelf_drilldown_quality_explains_hidden_preliminary_days(monkeypatch):
    monkeypatch.setattr(analytics, "quality_overview", lambda *_a: {
        "reconciliation": {"from": None, "to": None},
        "writeoff_amount_coverage_pct": None,
        "sources": {},
    })

    got = analytics.section_quality(
        "shelf", "2026-01-11", "2026-02-07",
        extra={"preliminary_from": "2026-02-08", "source_to": "2026-02-09"})

    reason = next(
        item for item in got["reasons"]
        if item["code"] == "shelf_unsettled_days_hidden"
    )
    assert reason["from"] == "2026-02-08"
    assert reason["to"] == "2026-02-09"
    assert "Более свежие дни скрыты" in reason["text"]


def test_sellout_keeps_same_sku_separate_by_point(monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(analytics.data, "read_parquet_stable", lambda _path: _shelf_frame())

    got = analytics.sellout_speed(None, 28)

    assert len(got["all"]) == 2
    assert {r["point"] for r in got["all"]} == {"Точка А", "Точка Б"}
    assert {r["sku_key"] for r in got["all"]} == {"sku-position"}
    assert got["preliminary_from"] == "2026-02-08"
    assert len(got["slow"]) == 2
    assert {r["sku_key"] for r in got["slow"]} == {"sku-position"}
    assert {r["avg_sold_per_day"] for r in got["slow"]} == {2.0}
    assert {r["receipt_sale_rate_u_h"] for r in got["all"]} == {0.22}
    assert got["summary"]["receipt_rate"]["rate_u_h"] == 0.22


def test_receipt_rate_is_weighted_by_observed_product_hours():
    frame = pd.DataFrame([
        {"canon_name": "Точка А", "sku_key": "sku-a", "sku_name": "А",
         "trade_day": date(2026, 2, 1), "sold_units": 10,
         "t_first": 10.1, "t_last": 19.8},
        {"canon_name": "Точка Б", "sku_key": "sku-b", "sku_name": "Б",
         "trade_day": date(2026, 2, 1), "sold_units": 10,
         "t_first": 10.1, "t_last": 10.8},
        {"canon_name": "Точка В", "sku_key": "sku-c", "sku_name": "В",
         "trade_day": date(2026, 2, 1), "sold_units": 5,
         "t_first": None, "t_last": None},
    ])

    by_key, summary = analytics._receipt_rate_stats(
        frame, ["canon_name", "sku_key", "sku_name"])

    assert by_key[("Точка А", "sku-a", "А")]["receipt_sale_rate_u_h"] == 1.0
    assert by_key[("Точка Б", "sku-b", "Б")]["receipt_sale_rate_u_h"] == 10.0
    assert ("Точка В", "sku-c", "В") not in by_key
    assert summary["rate_u_h"] == 1.82  # 20 продаж / 11 товаро-часов, не среднее 5,5
    assert summary["observed_pair_days"] == 2
    assert summary["excluded_pair_days"] == 1
    assert summary["formula_version"] == "receipt-window-v1"


def test_shelf_rolling_average_uses_calendar_days_without_false_zeroes(
        monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    frame = _shelf_frame()
    frame = frame[
        (frame["canon_name"] == "Точка А")
        & frame["trade_day"].isin([date(2026, 1, 31), date(2026, 2, 7)])
    ].copy()
    frame["wo_settled"] = True
    frame.loc[frame["trade_day"] == date(2026, 1, 31),
              ["lost_units", "lost_rub_margin", "wo_units", "wo_rub_cost"]] = [
                  10.0, 100.0, 1.0, 5.0]
    frame.loc[frame["trade_day"] == date(2026, 2, 7),
              ["lost_units", "lost_rub_margin", "wo_units", "wo_rub_cost"]] = [
                  20.0, 200.0, 2.0, 10.0]
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(analytics.data, "read_parquet_stable", lambda _path: frame)

    got = analytics.shelf_top(20, "Точка А", 10)

    assert len(got["daily"]) == 2
    last = got["daily"][-1]
    assert last["lost_units_ma7"] == 20.0
    assert last["lost_rub_margin_ma7"] == 200
    assert last["wo_units_ma7"] == 2.0
    assert last["wo_rub_cost_ma7"] == 10
    assert last["ma7_observed_days"] == 1


def test_sellout_keeps_same_name_cards_separate(monkeypatch, tmp_path):
    source = tmp_path / "shelf_rolling.parquet"
    source.write_bytes(b"stable-placeholder")
    frame = _shelf_frame()
    second = frame[frame["canon_name"] == "Точка А"].copy()
    second["sku_key"] = "sku-second-card"
    monkeypatch.setattr(analytics, "SHELF_ROLLING", source)
    monkeypatch.setattr(analytics, "SHELF_DAILY", tmp_path / "missing.parquet")
    monkeypatch.setattr(
        analytics.data, "read_parquet_stable",
        lambda _path: pd.concat([frame, second], ignore_index=True))

    got = analytics.sellout_speed("Точка А", 28)

    assert {row["sku_key"] for row in got["all"]} == {
        "sku-position", "sku-second-card"}
    assert {row["sku_key"] for row in got["slow"]} == {
        "sku-position", "sku-second-card"}


def test_writeoff_quality_names_preliminary_and_inventory_period(monkeypatch):
    monkeypatch.setattr(analytics, "quality_overview", lambda *_a: {
        "reconciliation": {"from": "2025-01-01", "to": "2026-12-31"},
        "writeoff_amount_coverage_pct": 10.0,
        "sources": {},
    })
    got = analytics.section_quality(
        "writeoffs", "2026-07-01", "2026-07-22",
        extra={"writeoff_amount_coverage_pct": 10.0})
    codes = {r["code"] for r in got["reasons"]}

    assert "writeoff_amount_incomplete" in codes
    assert "writeoffs_preliminary" in codes
    assert "inventory_adjustments" in codes
    inventory = next(r for r in got["reasons"] if r["code"] == "inventory_adjustments")
    assert "22.07.2026" in inventory["text"]


def test_sales_source_warnings_cover_yoy_and_writeoff_markdown(monkeypatch):
    monkeypatch.setattr(analytics, "quality_overview", lambda *_a: {
        "reconciliation": {"from": "2026-01-01", "to": "2026-12-31"},
        "writeoff_amount_coverage_pct": 10.0,
        "sources": {},
    })

    yoy_codes = {r["code"] for r in analytics.section_quality(
        "yoy", "2026-07-01", "2026-07-31",
        extra={"comparison_from": "2025-07-01", "comparison_to": "2025-07-31"}
    )["reasons"]}
    writeoff_codes = {r["code"] for r in analytics.section_quality(
        "writeoffs", "2026-07-01", "2026-07-31")["reasons"]}
    sales_codes = {r["code"] for r in analytics.section_quality(
        "sales", "2026-07-01", "2026-07-31")["reasons"]}

    assert "sales_document_preliminary" in yoy_codes
    assert "sales_document_preliminary" in sales_codes
    assert "sales_document_comparison_preliminary" in yoy_codes
    assert "u40_sales_not_reconciled" in writeoff_codes


def test_sales_export_repeats_period_quality_warning(monkeypatch):
    monkeypatch.setattr(analytics, "sales_series", lambda *_a, **_k: {
        "from": "2026-07-01", "to": "2026-07-02",
        "rows": [{"date": "2026-07-01", "sold": 5.0, "u40": 1.0,
                  "u40_share": 20.0}],
    })
    monkeypatch.setattr(analytics, "section_quality", lambda *_a, **_k: {
        "reasons": [{"code": "sales_document_preliminary",
                     "from": "2026-07-01", "to": "2026-07-02",
                     "text": "Тестовое ограничение периода."}],
    })

    path, _name = analytics.build_export_xlsx("sales", point="Точка А")
    try:
        raw = pd.read_excel(path, sheet_name="Продажи", header=None)
        text = "\n".join(raw.fillna("").astype(str).iloc[:, 0].tolist())
        assert "СТАТУС: данные требуют оговорок" in text
        assert "sales_document_preliminary [2026-07-01 — 2026-07-02]" in text
        assert "Тестовое ограничение периода" in text
    finally:
        path.unlink(missing_ok=True)


def test_status_route_scopes_overview_to_manager_point(monkeypatch):
    manager = _request("manager", "point-a")
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_overview(point=None):
        captured["point"] = point
        return {"status": "ok", "reasons": []}

    monkeypatch.setattr(analytics, "quality_overview", fake_overview)
    response = routes_analytics.api_analytics_status(manager)

    assert captured == {"point": "Точка А"}
    assert json.loads(response.body)["can_manage_excluded"] is False


def test_shelf_route_applies_manager_scope(monkeypatch):
    manager = _request("manager", "point-a")
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_shelf(n, point, days):
        captured.update(n=n, point=point, days=days)
        return {
            "deficit": [], "deficit_network": [], "deficit_details": [],
            "writeoff": [], "daily": [], "totals": {},
            "from": None, "to": None, "source": None, "point": point,
            "provenance": {},
        }

    monkeypatch.setattr(analytics, "shelf_top", fake_shelf)
    monkeypatch.setattr(analytics, "section_quality", lambda *_a, **_k: {"reasons": []})
    monkeypatch.setattr(routes_analytics, "_points_safe", lambda _request: ["Точка А"])

    response = routes_analytics.api_shelf(manager, n=20, point=None, days=28)
    payload = json.loads(response.body)

    assert captured == {"n": 20, "point": "Точка А", "days": 28}
    assert payload["points"] == ["Точка А"]
    assert payload["point"] == "Точка А"


def test_shelf_route_returns_separate_unit_and_money_charts(monkeypatch):
    admin = _request("admin")

    def fake_shelf(_n, point, _days):
        return {
            "deficit": [], "deficit_network": [], "deficit_details": [],
            "writeoff": [],
            "daily": [{
                "date": "2026-02-07", "lost_units": 2.0,
                "lost_units_ma7": 1.5, "lost_rub_margin": 200,
                "lost_rub_margin_ma7": 150, "wo_units": 1.0,
                "wo_units_ma7": 0.5, "wo_rub_cost": 80,
                "wo_rub_cost_ma7": 60, "ma7_observed_days": 4,
            }],
            "totals": {}, "from": "2026-02-01", "to": "2026-02-07",
            "source": "shelf.parquet", "point": point, "provenance": {},
        }

    monkeypatch.setattr(analytics, "shelf_top", fake_shelf)
    monkeypatch.setattr(analytics, "section_quality", lambda *_a, **_k: {"reasons": []})
    monkeypatch.setattr(routes_analytics, "_points_safe", lambda _request: [])

    payload = json.loads(routes_analytics.api_shelf(
        admin, n=20, point=None, days=28).body)

    assert "Оценка упущенных продаж" in payload["svg_lost"]
    assert "Списания" in payload["svg_writeoff"]
    assert "Оценка упущенной маржи" in payload["svg_lost_margin"]
    assert "Оценка себестоимости списаний" in payload["svg_writeoff_cost"]
    assert "₽" in payload["svg_lost_margin"]
    assert "₽" in payload["svg_writeoff_cost"]


def test_shelf_drilldown_route_applies_scope_and_returns_hourly_chart(monkeypatch):
    manager = _request("manager", "point-a")
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_drilldown(sku, point, day, days, *, sku_key, as_of,
                       allow_network_as_of):
        captured.update(sku=sku, sku_key=sku_key, point=point, day=day,
                        as_of=as_of, days=days,
                        allow_network_as_of=allow_network_as_of)
        return {
            "sku": sku, "sku_key": sku_key, "point": point,
            "as_of": as_of, "from": "2026-02-01", "to": "2026-02-07",
            "points": [], "days": [], "selected": {"date": "2026-02-07"},
            "hourly": [{"hour": 18, "label": "18:00", "qty": 2.0}],
            "notes": [], "provenance": {},
        }

    monkeypatch.setattr(analytics, "shelf_drilldown", fake_drilldown)
    monkeypatch.setattr(analytics, "section_quality", lambda *_a, **_k: {"reasons": []})
    response = routes_analytics.api_shelf_drilldown(
        manager, sku="Позиция", sku_key="sku-position", point=None,
        day="2026-02-07", as_of="2026-02-07", days=28)
    payload = json.loads(response.body)

    assert captured == {"sku": "Позиция", "sku_key": "sku-position",
                        "point": "Точка А", "day": "2026-02-07",
                        "as_of": "2026-02-07", "days": 28,
                        "allow_network_as_of": False}
    assert "Продажи по чекам" in payload["svg_hourly"]


def test_analytics_page_contains_quality_and_loss_charts():
    html = (analytics.ROOT / "web" / "static" / "analytics.html").read_text(
        encoding="utf-8")
    assert "quality-global" in html
    assert "svg_lost" in html
    assert "svg_writeoff" in html
    assert "svg_lost_margin" in html
    assert "svg_writeoff_cost" in html
    assert "Скачать отчёт" in html
    assert "Раннее прекращение продаж" in html
    assert "Раннее прекращение продаж — позиции для проверки (оценка)" in html
    assert "не подтверждённое время пустой полки" in html
    assert "r.sku, r.sku_key, r.point, '', d.to" in html
    assert "Среднее время последней продажи в отмеченные алгоритмом дни —" not in html
    assert "Расшифровка крупнейших оценок" in html
    assert "shelf_drilldown" in html
    assert "Сеть → позиция → точка → день" in html
    assert "Поступило по документам" in html
    assert "полка пустеет" not in html.lower()
    assert "Наблюдаемый темп продаж по чекам, шт/ч" in html
    assert "Дней расчёта темпа / наблюдений" in html
    assert "положительной продажи этой позиции в чеке" in html
    assert "не скорость опустения полки" in html
    assert "Фактический период этого графика" in html
    assert "кассовые чеки + профиль продаж и модельная оценка дефицита" in html
    assert "акты списания 1С + цена позиции" in html
    assert "Стоимость потерь, ₽ (оценка)" in html
    assert "Не бухгалтерская сверка" in html


def test_shelf_chart_moving_average_labels_name_the_metric():
    source = (analytics.ROOT / "web" / "routes_analytics.py").read_text(
        encoding="utf-8")

    assert '"оценка упущенных продаж — среднее по доступным наблюдениям за 7 календарных дней"' in source
    assert '"списания — среднее по доступным наблюдениям за 7 календарных дней"' in source
    assert '"оценка упущенной маржи — среднее по доступным наблюдениям за 7 календарных дней"' in source
    assert '"оценка себестоимости списаний — среднее по доступным наблюдениям за 7 календарных дней"' in source
    assert '"среднее за 7 дней":' not in source


# ---- «Данные сверяются» человеческим языком (C5, 08.09) ---------------------

def test_reconciliation_speaks_in_total_percent_not_point_days():
    assert analytics.RECON_LAG_DAYS == 3
    assert analytics._reconciliation_pct(1000, 1039) == 3.9
    assert analytics._reconciliation_pct(1000, 958) == -4.2
    assert analytics._reconciliation_pct(0, 10) is None
    assert analytics._reconciliation_pct(None, 10) is None

    assert analytics._reconciliation_reason(2.9) is None
    assert analytics._reconciliation_reason(-3.0) is None
    assert analytics._reconciliation_reason(None) is None

    low = analytics._reconciliation_reason(-4.2, frm="2025-07-31", to="2026-09-03")
    assert low["code"] == "sales_checks_not_reconciled"
    assert "два разных модуля 1С" in low["text"]
    assert "4,2% штук" in low["text"]
    assert "чеки ниже" in low["text"]
    assert "опираются на чеки" in low["text"]
    assert "точко-дн" not in low["text"]
    assert "абсолютное" not in low["text"]
    assert (low["from"], low["to"]) == ("2025-07-31", "2026-09-03")

    high = analytics._reconciliation_reason(3.1)
    assert "3,1% штук" in high["text"] and "чеки выше" in high["text"]


def test_quality_overview_source_excludes_lagging_check_days():
    source = (Path(__file__).resolve().parents[1] / "web" / "analytics.py").read_text(
        encoding="utf-8")
    assert "checks_max - timedelta(days=RECON_LAG_DAYS)" in source
    assert "sum(sales_qty) FILTER (WHERE has_sales AND has_checks AND d <= ?)" in source
    assert "sum(check_qty) FILTER (WHERE has_sales AND has_checks AND d <= ?)" in source
    assert "точко-дней ({_fmt_ru_number(mismatch_pct" not in source
