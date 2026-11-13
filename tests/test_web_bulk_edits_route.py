from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from web import edits, routes_order


# Дата внутри окна правок (завтра…послезавтра по UTC+10): сегодня и раньше —
# отгружено, дальние даты с 07.09 отклоняются как предварительные —
# см. routes_order._editable.
TARGET_DATE = (routes_order._business_today() + timedelta(days=1)).isoformat()
RELEASE_ID = "sha256:" + "a" * 64


def _request(role: str, point_key: str | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/edits/bulk",
        "headers": [],
        "query_string": b"",
        "session": {
            "auth_at": 1,
            "username": f"test-{role}",
            "role": role,
            "point_key": point_key,
        },
    })


def _body(*rows: tuple[str, str, float | None, int]):
    return routes_order.BulkEditsIn(
        target_date=TARGET_DATE,
        release_id=RELEASE_ID,
        batch_id="batch-route-1",
        edits=[routes_order.BulkEditItem(
            point_key=point, sku_key=sku, qty_user=qty,
            expected_revision=revision)
            for point, sku, qty, revision in rows],
    )


def test_bulk_route_validates_every_row_before_one_storage_call(monkeypatch):
    allowed = {("point-a", "sku-1"), ("point-a", "sku-2")}
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs", lambda _date, _release: allowed)
    captured = {}

    def fake_add(target_date, release_id, entries, ip, actor, batch_id):
        captured.update(target_date=target_date, release_id=release_id,
                        entries=entries, ip=ip,
                        actor=actor, batch_id=batch_id)
        return {"edited_at": "2099-01-01T00:00:00", "count": 2, "replayed": False}

    monkeypatch.setattr(routes_order.edits_store, "add_edits_bulk", fake_add)
    response = asyncio.run(routes_order.api_add_edits_bulk(
        _body(("point-a", "sku-1", 12, 0), ("point-a", "sku-2", None, 3)),
        _request("manager", "point-a"),
    ))

    assert json.loads(response.body)["count"] == 2
    assert captured["target_date"] == TARGET_DATE
    assert captured["release_id"] == RELEASE_ID
    assert captured["entries"] == [
        ("point-a", "sku-1", 12.0, 0), ("point-a", "sku-2", None, 3)]
    assert captured["actor"] == "test-manager"
    assert captured["batch_id"] == "batch-route-1"


def test_bulk_route_rejects_foreign_point_without_writing(monkeypatch):
    called = False

    def fake_add(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr(routes_order.edits_store, "add_edits_bulk", fake_add)
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs",
        lambda _date, _release: {("point-a", "sku-1"), ("point-b", "sku-2")})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_order.api_add_edits_bulk(
            _body(("point-a", "sku-1", 12, 0), ("point-b", "sku-2", 7, 0)),
            _request("manager", "point-a"),
        ))

    assert exc.value.status_code == 403
    assert called is False


@pytest.mark.parametrize("rows", [
    (("point-a", "sku-1", 1.5, 0),),
    (("point-a", "sku-1", -1, 0),),
    (("point-a", "sku-1", 100001, 0),),
    (("point-a", "sku-1", 1, -1),),
    (("point-a", "sku-1", 1, 0), ("point-a", "sku-1", 2, 0)),
])
def test_bulk_route_rejects_invalid_batch_before_writing(monkeypatch, rows):
    monkeypatch.setattr(
        routes_order.edits_store, "add_edits_bulk",
        lambda *_args: pytest.fail("storage must not be called"))
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs",
        lambda _date, _release: {("point-a", "sku-1")})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_order.api_add_edits_bulk(
            _body(*rows), _request("manager", "point-a")))

    assert exc.value.status_code == 422


def test_bulk_route_maps_stale_revision_to_conflict(monkeypatch):
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs",
        lambda _date, _release: {("point-a", "sku-1")})
    monkeypatch.setattr(
        routes_order.edits_store, "add_edits_bulk",
        lambda *_args: (_ for _ in ()).throw(edits.EditConflictError("stale")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_order.api_add_edits_bulk(
            _body(("point-a", "sku-1", 12, 0)),
            _request("manager", "point-a"),
        ))

    assert exc.value.status_code == 409


@pytest.mark.parametrize(("field", "value"), [
    ("expected_revision", 1.5),
    ("expected_revision", True),
    ("expected_revision", "1"),
    ("qty_user", True),
    ("qty_user", "12"),
])
def test_bulk_item_rejects_coerced_json_scalars(field, value):
    payload = {
        "point_key": "point-a", "sku_key": "sku-1",
        "qty_user": 12, "expected_revision": 0,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        routes_order.BulkEditItem.model_validate(payload)


def test_admin_batch_may_intentionally_span_multiple_points(monkeypatch):
    allowed = {("point-a", "sku-1"), ("point-b", "sku-2")}
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs", lambda _date, _release: allowed)
    captured = {}

    def fake_add(_date, _release, entries, *_rest):
        captured["entries"] = entries
        return {"edited_at": "2099-01-01T00:00:00", "count": 2,
                "replayed": False}

    monkeypatch.setattr(routes_order.edits_store, "add_edits_bulk", fake_add)

    response = asyncio.run(routes_order.api_add_edits_bulk(
        _body(("point-a", "sku-1", 12, 0), ("point-b", "sku-2", 7, 0)),
        _request("admin"),
    ))

    assert json.loads(response.body)["count"] == 2
    assert {row[:2] for row in captured["entries"]} == allowed


# ---- окно правок: завтра и послезавтра (C1, 08.09) ---------------------------

def test_edit_window_is_tomorrow_and_day_after():
    today = date(2026, 9, 8)
    assert routes_order._operational_date(today) == date(2026, 9, 9)
    assert routes_order._edit_window(today) == (date(2026, 9, 9), date(2026, 9, 10))
    assert not routes_order._editable("2026-09-07", today)   # вчера — отгружено
    assert not routes_order._editable("2026-09-08", today)   # сегодня — отгружено
    assert routes_order._editable("2026-09-09", today)       # завтра — рабочая
    assert routes_order._editable("2026-09-10", today)       # послезавтра — окончательная
    assert not routes_order._editable("2026-09-11", today)   # D+3 — предварительная


@pytest.mark.parametrize(("offset", "fragment"), [
    (-1, "уже отгружена"),
    (0, "уже отгружена"),
    (3, "завтра и послезавтра"),
    (4, "ночью пересчитываются"),
])
def test_bulk_route_explains_dates_outside_window(monkeypatch, offset, fragment):
    today = date(2026, 9, 8)
    monkeypatch.setattr(routes_order, "_business_today", lambda: today)
    monkeypatch.setattr(
        routes_order.edits_store, "add_edits_bulk",
        lambda *_args: pytest.fail("storage must not be called"))
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs",
        lambda _date, _release: {("point-a", "sku-1")})
    body = _body(("point-a", "sku-1", 12, 0))
    body.target_date = (today + timedelta(days=offset)).isoformat()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_order.api_add_edits_bulk(body, _request("manager", "point-a")))

    assert exc.value.status_code == 422
    assert fragment in exc.value.detail


@pytest.mark.parametrize("offset", [1, 2])
def test_bulk_route_accepts_tomorrow_and_day_after(monkeypatch, offset):
    today = date(2026, 9, 8)
    monkeypatch.setattr(routes_order, "_business_today", lambda: today)
    monkeypatch.setattr(
        routes_order.order_mod, "release_fingerprint", lambda _date: RELEASE_ID)
    monkeypatch.setattr(
        routes_order.order_mod, "valid_pairs",
        lambda _date, _release: {("point-a", "sku-1")})
    monkeypatch.setattr(
        routes_order.edits_store, "add_edits_bulk",
        lambda *_args: {"edited_at": "2099-01-01T00:00:00", "count": 1,
                        "replayed": False})
    body = _body(("point-a", "sku-1", 12, 0))
    body.target_date = (today + timedelta(days=offset)).isoformat()

    response = asyncio.run(routes_order.api_add_edits_bulk(
        body, _request("manager", "point-a")))
    assert json.loads(response.body)["count"] == 1


# ---- запись в 1С: окно дат и номер документа (B2, 08.09) ---------------------

class _FakeWriterModule:
    def __init__(self, result):
        outer = self

        class TransferOrderWriter:
            def push(self, _date, _point, **_who):
                return outer.result

        self.result = result
        self.TransferOrderWriter = TransferOrderWriter


def test_push1c_rejects_preliminary_date_with_human_text(monkeypatch):
    today = date(2026, 9, 8)
    monkeypatch.setattr(routes_order, "_business_today", lambda: today)
    monkeypatch.setenv("PROGNOZ_1C_WRITE", "1")
    monkeypatch.setattr(routes_order, "_load_writer",
                        lambda: pytest.fail("writer must not be loaded"))
    body = routes_order.Push1cIn(date="2026-09-11", point="Точка А")

    with pytest.raises(HTTPException) as exc:
        routes_order.api_push1c(body, _request("admin"))
    assert exc.value.status_code == 422
    assert "завтра и послезавтра" in exc.value.detail


@pytest.mark.parametrize(("result", "expected"), [
    ({"number": "DEMO-000123", "lines": 5}, "DEMO-000123"),
    ({"lines": 5}, None),
])
def test_push1c_returns_document_number_when_writer_has_it(monkeypatch, result, expected):
    today = date(2026, 9, 8)
    monkeypatch.setattr(routes_order, "_business_today", lambda: today)
    monkeypatch.setenv("PROGNOZ_1C_WRITE", "1")
    monkeypatch.setattr(routes_order, "_load_writer",
                        lambda: _FakeWriterModule(result))
    body = routes_order.Push1cIn(date="2026-09-09", point="Точка А")

    response = routes_order.api_push1c(body, _request("admin"))
    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["number"] == expected
