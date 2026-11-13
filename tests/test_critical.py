"""End-to-end checks of the rules that directly affect the user's choice."""
import re

import numpy as np
import pytest

from flats import db, repo_user
from flats.importer import run_import
from flats.similar import MODEL
from flats.types import Profile
from flats.web import create_app

KNOWN = {"deposit_raw": "Без залога", "commission_raw": "Без комиссии", "prepayment_raw": "за 1 месяц", "pets_raw": "можно"}


def offer(source_id, **kw):
    return {"source": "farpost", "source_id": source_id, "url": f"https://example.com/{source_id}",
            "rooms": 2, "district": "Центр", **KNOWN, **kw}


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "t.db"
    conn = db.connect(path)
    db.init_db(conn)
    run_import(conn, [
        offer("ref", price=40000, deposit_raw="40 000 ₽", commission_raw="50%", utilities_raw="Оплачиваются отдельно"),
        offer("pct-no-rent", price=None, commission_raw="50%"),
        offer("unknown-deposit", price=30000, deposit_raw=None),
        offer("g-a", group_id="g", price=35000, commission_raw="35 000 ₽"),
        offer("g-b", group_id="g", price=37000, commission_raw="Без комиссии"),
        offer("no-photo", price=31000),
        offer("dear", price=90000, photos=["https://x/dear.jpg"]),
        offer("cheap", price=32000, photos=["https://x/cheap.jpg"]),
    ])
    for url, sid, vec in [("https://x/dear.jpg", "dear", [1, 0]), ("https://x/cheap.jpg", "cheap", [0.6, 0.8])]:
        conn.execute("INSERT INTO photo_features VALUES (?, 'farpost', ?, 'h', ?, ?)",
                     (url, sid, MODEL, np.array(vec, dtype=np.float32).tobytes()))
    conn.commit()
    app = create_app(path)
    app.testing = True
    yield conn, app.test_client()
    conn.close()


def card_ids(html):
    return re.findall(r'name="source_id" value="([^"]+)"', html)


def test_percent_commission_needs_known_rent(env):
    _, client = env
    html = client.get("/flat/farpost:pct-no-rent").get_data(as_text=True)
    assert "50%" in html
    assert "не указано: аренда" not in html  # rent is not a move-in part
    assert re.search(r"Комиссия</th><td>не указано", html)


def test_prepayment_not_counted_twice(env):
    _, client = env
    html = client.get("/flat/farpost:ref").get_data(as_text=True)
    assert "100 000 ₽" in html and "140 000 ₽" not in html


def test_unknown_payments_are_not_zero(env):
    _, client = env
    html = client.get("/flat/farpost:unknown-deposit").get_data(as_text=True)
    assert re.search(r"Залог</th><td>не указано", html)
    assert "Какой залог" in html


def test_offers_of_one_flat_are_not_mixed(env):
    conn, client = env
    repo_user.decide(conn, "farpost", "g-a", "save")
    html = client.get("/compare?o=farpost:g-a").get_data(as_text=True)
    assert "70 000 ₽" in html  # 35 000 rent + 35 000 commission of offer g-a
    assert "37 000 ₽" not in html  # nothing from offer g-b
    assert "Условия на площадках различаются" in html


def test_failed_import_keeps_working_data(env):
    conn, client = env
    before = client.get("/").get_data(as_text=True)
    last = re.search(r"Данные от ([^<]+)", before).group(1)

    def broken():
        yield offer("ref", price=1)
        raise RuntimeError("source down")

    with pytest.raises(RuntimeError):
        run_import(conn, broken())
    after = client.get("/").get_data(as_text=True)
    assert f"Данные от {last}" in after
    assert card_ids(after) == card_ids(before)


def test_same_filters_everywhere(env):
    conn, client = env
    client.get("/?apply=1&monthly_max=40000")
    listed = set(card_ids(client.get("/").get_data(as_text=True)))
    swiped = set(re.findall(r'data-source-id="([^"]+)"', client.get("/swipe").get_data(as_text=True)))
    assert listed == swiped and "dear" not in listed
    client.post("/api/sample", data={"photo_url": "https://x/dear.jpg"})
    visual = card_ids(client.get("/").get_data(as_text=True))
    assert set(visual) == listed  # similarity re-orders, never adds the rejected 'dear'
    assert visual[0] == "cheap"


def test_offer_without_photos_is_listed(env):
    _, client = env
    assert "no-photo" in card_ids(client.get("/").get_data(as_text=True))


def test_compare_links_to_exact_source(env):
    conn, client = env
    repo_user.decide(conn, "farpost", "g-b", "save")
    html = client.get("/compare?o=farpost:g-b").get_data(as_text=True)
    assert 'href="https://example.com/g-b"' in html and "https://example.com/g-a" not in html


def test_saved_decision_survives_regrouping(env):
    conn, client = env
    repo_user.decide(conn, "farpost", "ref", "save")
    run_import(conn, [offer("ref", group_id="new-group", price=40000)])
    assert 'value="farpost:ref"' in client.get("/favorites").get_data(as_text=True)
