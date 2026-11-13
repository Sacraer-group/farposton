import json
from pathlib import Path

from flats.metrapi import normalize

SNAPSHOT = Path(__file__).parents[1] / "flats" / "demo" / "metrapi_vladivostok.json"


def item(**kw):
    base = {"source": "farpost", "source_id": 123, "price": 40000, "deposit": "30 000 ₽",
            "commission": None, "utilities_payer": "Арендатор", "pets_allowed": None,
            "photos": ["https://x/1"], "description": "Цена: 40 000&#8381;. Можно с животными.",
            "phone": "+79140000000", "phones": [{"n": "+79140000000"}], "seller_name": "Иван"}
    base.update(kw)
    return base


def test_maps_fields_and_drops_private_data():
    row = normalize(item())
    assert row["source_id"] == "123"
    assert (row["price"], row["deposit_raw"], row["commission_raw"]) == (40000, "30 000 ₽", None)
    assert row["utilities_raw"] == "Арендатор"
    assert row["description"] == "Цена: 40 000₽. Можно с животными."
    assert row["photos"] == ["https://x/1"]
    for key in ("phone", "phones", "seller_name", "description"):
        assert key not in row["raw"]


def test_pets_from_description_only_when_field_empty():
    assert normalize(item())["pets_raw"] == "Можно с животными"
    assert normalize(item(description="Без условий"))["pets_raw"] is None
    assert normalize(item(pets_allowed=False))["pets_raw"] is False


def test_phone_numbers_in_text_are_masked():
    row = normalize(item(description="Звоните +7 (914) 123-45-67 или 89141234567. id 71f3ec97e88247755935d051"))
    assert "914" not in row["description"].split("id")[0]
    assert row["description"].count("[телефон скрыт]") == 2
    assert "71f3ec97e88247755935d051" in row["description"]


def test_snapshot_is_clean():
    text = SNAPSHOT.read_text("utf-8")
    rows = json.loads(text)
    assert len(rows) == 50
    assert all(r["source"] and r["source_id"] and r["price"] for r in rows)
    for key in ('"phone', '"phones"', '"seller_name"', '"seller_company"'):
        assert key not in text
