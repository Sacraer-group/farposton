from dataclasses import fields

from flats.grouping import group_offers
from flats.types import Offer

KNOWN = dict(deposit_raw="Без залога", commission_raw="Без комиссии", prepayment_raw="за 1 месяц")


def make_offer(**overrides) -> Offer:
    values = {field.name: None for field in fields(Offer)}
    values.update(source="farpost", source_id="1", photos=())
    values.update(overrides)
    return Offer(**values)


def test_same_group_is_one_card():
    a = make_offer(source_id="1", group_id="g1")
    b = make_offer(source="avito", source_id="2", group_id="g1")
    [card] = group_offers([a, b])
    assert card.key == "g1"
    assert set(card.offers) == {a, b}


def test_ungrouped_offers_stay_separate():
    cards = group_offers([make_offer(source_id="2"), make_offer(source_id="1")])
    assert [c.key for c in cards] == ["farpost:1", "farpost:2"]


def test_best_is_lowest_known_total():
    cheap = make_offer(source_id="1", group_id="g", price=40000, **KNOWN)  # 80000
    dear = make_offer(source_id="2", group_id="g", price=45000, **KNOWN)  # 90000
    [card] = group_offers([dear, cheap])
    assert card.best is cheap
    assert card.offers == (cheap, dear)


def test_unknown_never_beats_known():
    cheaper_unknown = make_offer(source_id="1", group_id="g", price=20000,
                                 commission_raw="Без комиссии", prepayment_raw="за 1 месяц")
    known = make_offer(source_id="2", group_id="g", price=45000, **KNOWN)
    [card] = group_offers([cheaper_unknown, known])
    assert card.best is known


def test_unknown_rent_goes_last():
    no_rent = make_offer(source_id="1", group_id="g", **KNOWN)
    known = make_offer(source_id="2", group_id="g", price=90000, **KNOWN)
    [card] = group_offers([no_rent, known])
    assert card.offers == (known, no_rent)


def test_unknown_utilities_do_not_count():
    a = make_offer(source_id="1", group_id="g", price=40000, utilities_raw=None, **KNOWN)
    b = make_offer(source_id="2", group_id="g", price=45000, utilities_raw="Включены", **KNOWN)
    assert group_offers([b, a])[0].best is a


def test_tie_prefers_newest_last_seen_and_none_loses():
    old = make_offer(source_id="1", group_id="g", price=40000, last_seen="2026-11-10", **KNOWN)
    new = make_offer(source_id="2", group_id="g", price=40000, last_seen="2026-11-12", **KNOWN)
    never = make_offer(source_id="3", group_id="g", price=40000, last_seen=None, **KNOWN)
    [card] = group_offers([never, old, new])
    assert card.offers == (new, old, never)


def test_full_tie_is_deterministic():
    a = make_offer(source="avito", source_id="9", group_id="g")
    b = make_offer(source="farpost", source_id="1", group_id="g")
    assert group_offers([b, a])[0].offers == (a, b)
    assert group_offers([a, b])[0].offers == (a, b)


def test_cards_sorted_by_key_and_empty():
    cards = group_offers([make_offer(source_id="1", group_id="z"), make_offer(source_id="2", group_id="a")])
    assert [c.key for c in cards] == ["a", "z"]
    assert group_offers([]) == []
