from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from web import (accuracy, analytics, comparison, routes_accuracy,
                 routes_analytics, routes_comparison, routes_order,
                 routes_reports)
from web.access import require_admin
from web.ai import chat, tools


def _request(role: str, point_key: str | None = None, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "method": method,
        "path": "/api/test",
        "headers": [],
        "query_string": b"",
        "session": {
            "auth_at": 1,
            "username": f"test-{role}",
            "role": role,
            "point_key": point_key,
        },
    })


def test_network_analytics_requires_admin_role():
    assert require_admin(_request("admin")).is_admin
    with pytest.raises(HTTPException) as exc:
        require_admin(_request("manager", "point-a"))
    assert exc.value.status_code == 403
    assert "только администратору" in str(exc.value.detail)


def test_network_analytics_pages_and_http_routes_repeat_admin_guard():
    checked = 0
    for router in (routes_accuracy.router, routes_comparison.router,
                   routes_analytics.router):
        for route in router.routes:
            dependency_calls = {dep.call for dep in route.dependant.dependencies}
            assert require_admin in dependency_calls, route.path
            checked += 1
    assert checked >= 10

    app_source = (Path(routes_accuracy.__file__).with_name("app.py")
                  .read_text(encoding="utf-8"))
    assert 'return _admin_page(request, "accuracy.html")' in app_source
    assert 'return _admin_page(request, "comparison.html")' in app_source
    assert 'return _admin_page(request, "analytics.html")' in app_source


def test_accuracy_manager_is_forced_to_own_point(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_summary(period, level, point):
        captured.update(period=period, level=level, point=point)
        return {"points": ["Точка А", "Точка Б"], "daily": []}

    monkeypatch.setattr(routes_accuracy.accuracy, "summary", fake_summary)
    response = routes_accuracy.api_accuracy(
        _request("manager", "point-a"), period="7d", level="network", point=None)
    payload = json.loads(response.body)

    assert captured == {"period": "7d", "level": "point", "point": "Точка А"}
    assert payload["points"] == ["Точка А"]


def test_accuracy_manager_cannot_request_foreign_point(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    with pytest.raises(HTTPException) as exc:
        routes_accuracy.api_accuracy_top(
            _request("manager", "point-a"), period="7d", direction="down",
            point="Точка Б", n=15)
    assert exc.value.status_code == 403


def test_order_manager_cannot_request_foreign_point(monkeypatch):
    monkeypatch.setattr(
        routes_order.order_mod, "point_name_for_key",
        lambda _date, _key: "Точка А")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_order.api_order(
            "2026-08-27", _request("manager", "point-a"), point="Точка Б"))

    assert exc.value.status_code == 403


def test_accuracy_point_metadata_does_not_describe_other_points(monkeypatch):
    frame = pd.DataFrame({
        "date": ["2026-08-20", "2026-08-21", "2026-08-22"],
        "canon_name": ["Точка А", "Точка А", "Точка Б"],
    })
    monkeypatch.setattr(accuracy, "_shadow_all", lambda: frame)
    monkeypatch.setattr(accuracy, "_slice", lambda *_a: pd.DataFrame())
    monkeypatch.setattr(accuracy, "engine_epoch", lambda: date(2026, 8, 14))
    monkeypatch.setattr(accuracy, "excluded_days", lambda: ["2026-08-22"])

    payload = accuracy.summary("all", "point", "Точка А")

    assert payload["available_days"] == 2
    assert payload["last_date"] == "2026-08-21"
    assert payload["points"] == ["Точка А"]
    assert payload["excluded_days"] == []


def test_comparison_manager_is_forced_to_own_point(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_summary(period, point, base, scope):
        captured.update(period=period, point=point, base=base, scope=scope)
        return {"points": ["Точка А", "Точка Б"], "daily": []}

    monkeypatch.setattr(routes_comparison.comparison, "summary", fake_summary)
    monkeypatch.setattr(
        routes_comparison.comparison, "health",
        lambda point: captured.update(health_point=point) or {})
    response = routes_comparison.api_comparison(
        _request("manager", "point-a"), period="30d", point=None,
        base="demand", scope="paired")
    payload = json.loads(response.body)

    assert captured["point"] == "Точка А"
    assert captured["health_point"] == "Точка А"
    assert payload["points"] == ["Точка А"]


def test_comparison_point_metadata_does_not_describe_other_points(monkeypatch):
    frame = pd.DataFrame({
        "date": ["2026-08-20", "2026-08-21", "2026-08-22"],
        "canon_name": ["Точка А", "Точка А", "Точка Б"],
    })
    monkeypatch.setattr(comparison, "_all", lambda: frame)
    monkeypatch.setattr(comparison, "_slice", lambda *_a: pd.DataFrame())
    monkeypatch.setattr(comparison, "excluded_days", lambda: ["2026-08-22"])

    payload = comparison.summary("all", "Точка А")
    health = comparison.health("Точка А")

    assert payload["available_days"] == 2
    assert payload["first_date"] == "2026-08-20"
    assert payload["last_date"] == "2026-08-21"
    assert payload["points"] == ["Точка А"]
    assert payload["excluded_days"] == []
    assert health == {
        "days": 2, "first": "2026-08-20", "last": "2026-08-21", "mtime": None,
    }


def test_comparison_period_is_chosen_after_point_scope(monkeypatch):
    frame = pd.DataFrame({
        "date": ["2026-08-20", "2026-08-21", "2026-08-22"],
        "canon_name": ["Точка А", "Точка А", "Точка Б"],
        "supply_gap": [False, False, False],
        "no_report": [False, False, False],
        "wo_settled": [True, True, True],
    })
    monkeypatch.setattr(comparison, "_all", lambda: frame)

    result = comparison._slice("last", "Точка А", "demand")

    assert result["date"].tolist() == ["2026-08-21"]
    assert result["canon_name"].tolist() == ["Точка А"]


def test_comparison_export_manager_cannot_escape_scope(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_export(period, point, base, scope):
        captured["point"] = point
        return pd.DataFrame([{"Точка": point, "Значение": 1}])

    monkeypatch.setattr(routes_comparison.comparison, "export_rows", fake_export)
    response = routes_comparison.api_comparison_export(
        _request("manager", "point-a"), period="30d", point=None,
        base="demand", scope="paired")
    try:
        assert captured["point"] == "Точка А"
    finally:
        Path(response.path).unlink(missing_ok=True)


def test_comparison_manager_cannot_request_foreign_point(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")

    with pytest.raises(HTTPException) as exc:
        routes_comparison.api_comparison_top(
            _request("manager", "point-a"), period="30d", point="Точка Б",
            base="demand", n=20)

    assert exc.value.status_code == 403


def test_analytics_manager_is_forced_to_own_point(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")
    captured = {}

    def fake_sales(granularity, point, frm, to, preset):
        captured.update(granularity=granularity, point=point, preset=preset)
        return {"rows": [], "from": None, "to": None, "granularity": granularity}

    monkeypatch.setattr(routes_analytics.analytics, "sales_series", fake_sales)
    monkeypatch.setattr(
        routes_analytics.analytics, "section_quality", lambda *_a, **_k: {})
    response = routes_analytics.api_sales(
        _request("manager", "point-a"), granularity="day", point=None,
        frm=None, to=None, preset="7d")
    payload = json.loads(response.body)

    assert captured == {"granularity": "day", "point": "Точка А", "preset": "7d"}
    assert payload["points"] == ["Точка А"]


def test_analytics_manager_cannot_export_foreign_point(monkeypatch):
    monkeypatch.setattr(analytics, "point_name_for_key", lambda _key: "Точка А")

    with pytest.raises(HTTPException) as exc:
        routes_analytics.api_export(
            _request("manager", "point-a"), section="sales", point="Точка Б",
            granularity="day", frm=None, to=None, preset="7d", days=None,
            by="qty", n=20)

    assert exc.value.status_code == 403


def test_network_report_list_is_empty_for_manager():
    """Сетевые сводки — только админу. С 20.09 у менеджера есть СВОЯ оперативная
    сводка (scope_report по его point_key), поэтому на пустом стенде он получает
    404 «нет сводки для вашей точки», а не 403: до сетевых данных он всё равно
    не доходит — проверка is_admin стоит раньше выдачи сетевой сводки."""
    manager = _request("manager", "point-a")
    response = asyncio.run(routes_reports.api_reports(manager))
    assert json.loads(response.body) == []
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_reports.api_reports_latest(manager))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_reports.api_reports_date("2026-08-27", manager))
    assert exc.value.status_code == 404


def test_push1c_rejects_foreign_point_before_disabled_writer(monkeypatch):
    monkeypatch.setattr(routes_order.order_mod, "point_name_for_key",
                        lambda _date, _key: "Точка А")
    monkeypatch.setenv("PROGNOZ_1C_WRITE", "0")
    body = routes_order.Push1cIn(date="2026-08-27", point="Точка Б")

    with pytest.raises(HTTPException) as exc:
        routes_order.api_push1c(body, _request("manager", "point-a", "POST"))
    assert exc.value.status_code == 403


def test_ai_tools_ignore_llm_point_for_manager(monkeypatch):
    captured = {}

    def fake_sales(granularity=None, point=None, preset=None):
        captured.update(granularity=granularity, point=point, preset=preset)
        return {"point": point, "points": ["Точка А", "Точка Б"],
                "rows": [], "from": None, "to": None,
                "granularity": granularity}

    monkeypatch.setattr(
        tools, "_fn",
        lambda module, name: fake_sales if (module, name) == (
            "web.analytics", "sales_series") else None,
    )
    payload = json.loads(tools.execute_tool(
        "get_sales", {"point": "Точка Б"}, point_scope="Точка А"))

    assert captured["point"] == "Точка А"
    assert payload["point"] == "Точка А"
    assert "points" not in payload


@pytest.mark.parametrize(
    ("tool_name", "module", "function_name"),
    [
        ("get_accuracy", "web.accuracy", "summary"),
        ("get_top_miss", "web.accuracy", "top_miss"),
        ("get_writeoffs", "web.analytics", "writeoffs"),
        ("get_shelf_top", "web.analytics", "shelf_top"),
        ("get_comparison", "web.comparison", "summary"),
    ],
)
def test_all_ai_analytics_tools_receive_manager_scope(
        monkeypatch, tool_name, module, function_name):
    captured = {}

    def fake(point=None, **_kwargs):
        captured["point"] = point
        if tool_name == "get_top_miss":
            return []
        return {
            "point": point,
            "points": ["Точка А", "Точка Б"],
            "rows": [],
            "by_point": [],
            "deficit": [],
            "writeoff": [],
        }

    monkeypatch.setattr(
        tools, "_fn",
        lambda got_module, got_name: fake
        if (got_module, got_name) == (module, function_name) else None,
    )
    payload = json.loads(tools.execute_tool(
        tool_name, {"point": "Точка Б"}, point_scope="Точка А"))

    assert captured["point"] == "Точка А"
    if "points" in payload:
        assert payload["points"] == ["Точка А"]
    if tool_name != "get_top_miss":
        assert payload["point"] == "Точка А"


def test_ai_order_tool_receives_manager_scope(monkeypatch):
    captured = {}

    def fake_order(target_date=None, point=None):
        captured.update(target_date=target_date, point=point)
        return {"rows": [], "meta": {}}

    def fake_fn(module, name):
        if (module, name) == ("web.order", "get_order"):
            return fake_order
        if (module, name) == ("web.data", "forecast_dates"):
            return lambda: ["2026-08-28"]
        return None

    monkeypatch.setattr(tools, "_fn", fake_fn)
    json.loads(tools.execute_tool(
        "get_order", {"point": "Точка Б"}, point_scope="Точка А"))

    assert captured == {"target_date": "2026-08-28", "point": "Точка А"}


def test_manager_ai_prompt_excludes_network_artifacts(monkeypatch):
    chat.build_system_message.cache_clear()
    monkeypatch.setattr(chat, "compact_facts", lambda: "NETWORK_FACT_SENTINEL")
    monkeypatch.setattr(
        chat, "latest_report_json", lambda: ("2026-08-25", "NETWORK_REPORT_SENTINEL"))

    prompt = chat.build_system_message("Точка А")

    assert "Точка А" in prompt
    assert "NETWORK_FACT_SENTINEL" not in prompt
    assert "NETWORK_REPORT_SENTINEL" not in prompt
