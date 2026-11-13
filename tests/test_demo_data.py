import json
from contextlib import closing
from pathlib import Path

import pytest

from flats import db, repo_offers

DEMO = Path(__file__).parents[1] / "flats" / "demo" / "offers.json"
DISTRICTS = {"Центр", "Эгершельд", "Вторая речка", "Чуркин", "Первая речка", "Снеговая падь", "Баляева", "Седанка"}


@pytest.fixture(scope="module")
def offers():
    return json.loads(DEMO.read_text("utf-8"))


def by_id(offers, source_id):
    return next(o for o in offers if o["source_id"] == source_id)


def test_shape(offers):
    assert 24 <= len(offers) <= 30
    keys = [(o["source"], o["source_id"]) for o in offers]
    assert len(set(keys)) == len(keys)
    for o in offers:
        assert o["source"] in {"farpost", "avito", "cian"} and o["source_id"]
        assert o["district"] is None or o["district"] in DISTRICTS
        assert o["url"] == f"https://example.com/demo/{o['source_id']}"
        assert o["photos"] == []


def test_no_personal_fields(offers):
    text = DEMO.read_text("utf-8")
    for key in ("phone", "phones", "seller_name"):
        assert f'"{key}"' not in text


def test_loads_into_db(offers, tmp_path):
    with closing(db.connect(tmp_path / "t.db")) as conn:
        db.init_db(conn)
        run_id = conn.execute(
            "INSERT INTO import_runs (started_at, status) VALUES ('2026-11-13', 'ok')"
        ).lastrowid
        for o in offers:
            repo_offers.upsert_offer(conn, o, run_id)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == len(offers)


def test_required_cases(offers):
    ref = by_id(offers, "demo-1")
    assert (ref["price"], ref["deposit_raw"], ref["commission_raw"], ref["prepayment_raw"], ref["utilities_raw"]) == (
        40000, "40 000 ₽", "50%", "за 1 месяц", "Оплачиваются отдельно")
    assert by_id(offers, "demo-2")["utilities_raw"] == "Включены в стоимость"
    unknown = by_id(offers, "demo-3")
    assert unknown["deposit_raw"] is None and unknown["prepayment_raw"] is None
    assert unknown["commission_raw"] == "по договорённости"
    four = by_id(offers, "demo-4")
    assert (four["commission_raw"], four["prepayment_raw"]) == ("100%", "за 2 месяца")
    assert by_id(offers, "demo-5")["price"] is None
    assert [by_id(offers, f"demo-{i}")["pets_raw"] for i in (6, 7, 8)] == ["можно", "нельзя", None]
    assert by_id(offers, "demo-9")["rooms"] is None
    assert by_id(offers, "demo-10")["district"] is None
    assert "<script>alert(1)</script>" in by_id(offers, "demo-11")["description"]


def test_groups(offers):
    g1 = [o for o in offers if o["group_id"] == "demo-g1"]
    g2 = [o for o in offers if o["group_id"] == "demo-g2"]
    assert len(g1) == 3 and len({(o["price"], o["commission_raw"]) for o in g1}) == 3
    assert len(g2) == 2
    terms = lambda o: (o["price"], o["deposit_raw"], o["commission_raw"], o["prepayment_raw"])
    assert terms(g2[0]) == terms(g2[1])
    assert {o["group_id"] for o in offers} == {None, "demo-g1", "demo-g2"}
