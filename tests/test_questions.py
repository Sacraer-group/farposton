from dataclasses import replace

import pytest

from flats.questions import questions_for
from flats.types import FlatCard, Offer


OTHER_PAYMENTS = "Есть ли другие платежи при въезде?"
CONFLICT = "Условия на площадках различаются — подтвердите условия этого предложения"
UNKNOWN_QUESTIONS = [
    "Какова обычная сумма коммунальных платежей?",
    "Какой залог и на каких условиях он возвращается?",
    "Есть ли комиссия и какой она суммы?",
    "За сколько месяцев нужна предоплата?",
    "Можно ли жить с животными?",
]


def make_offer(**overrides):
    values = dict(
        source="farpost",
        source_id="1",
        group_id=None,
        url=None,
        address=None,
        district=None,
        lat=None,
        lon=None,
        rooms=None,
        area_total=None,
        floor=None,
        floors_total=None,
        price=None,
        deposit_raw=None,
        commission_raw=None,
        prepayment_raw=None,
        utilities_raw=None,
        pets_raw=None,
        rent_period_raw=None,
        description=None,
        photos=(),
        published_at=None,
        last_seen=None,
    )
    values.update(overrides)
    return Offer(**values)


@pytest.fixture
def known_offer():
    return make_offer(
        price=40000,
        deposit_raw="40 000 ₽",
        commission_raw="Без комиссии",
        prepayment_raw="за 1 месяц",
        utilities_raw="Включены",
        pets_raw="можно",
    )


def test_fully_known_offer(known_offer):
    card = FlatCard(key="flat", offers=(known_offer,), best=known_offer)
    assert questions_for(card, known_offer) == [OTHER_PAYMENTS]


@pytest.mark.parametrize(
    ("field", "raw", "question"),
    [
        ("utilities_raw", None, UNKNOWN_QUESTIONS[0]),
        ("utilities_raw", "Оплачиваются отдельно", UNKNOWN_QUESTIONS[0]),
        ("deposit_raw", None, UNKNOWN_QUESTIONS[1]),
        ("commission_raw", None, UNKNOWN_QUESTIONS[2]),
        ("prepayment_raw", None, UNKNOWN_QUESTIONS[3]),
        ("pets_raw", None, UNKNOWN_QUESTIONS[4]),
    ],
)
def test_each_unknown_term_alone(known_offer, field, raw, question):
    chosen = replace(known_offer, **{field: raw})
    card = FlatCard(key="flat", offers=(chosen,), best=chosen)
    assert questions_for(card, chosen) == [question, OTHER_PAYMENTS]


def test_all_unknown():
    chosen = make_offer()
    card = FlatCard(key="flat", offers=(chosen,), best=chosen)
    assert questions_for(card, chosen) == UNKNOWN_QUESTIONS + [OTHER_PAYMENTS]


@pytest.mark.parametrize(
    "changes",
    [
        {"price": 41000},
        {"deposit_raw": "Без залога"},
        {"commission_raw": "50%"},
        {"prepayment_raw": "за 2 месяца"},
    ],
)
def test_differing_terms(known_offer, changes):
    other = replace(known_offer, source_id="2", **changes)
    card = FlatCard(key="flat", offers=(known_offer, other), best=known_offer)
    assert questions_for(card, known_offer) == [CONFLICT, OTHER_PAYMENTS]


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {
            "deposit_raw": " 40 000 ₽ ",
            "commission_raw": " БЕЗ КОМИССИИ ",
            "prepayment_raw": " ЗА 1 МЕСЯЦ ",
        },
    ],
)
def test_equivalent_terms(known_offer, changes):
    other = replace(known_offer, source_id="2", **changes)
    card = FlatCard(key="flat", offers=(known_offer, other), best=known_offer)
    assert questions_for(card, known_offer) == [OTHER_PAYMENTS]


def test_percent_terms_ignore_outer_whitespace(known_offer):
    chosen = replace(known_offer, commission_raw="50%")
    other = replace(chosen, source_id="2", commission_raw=" 50% ")
    card = FlatCard(key="flat", offers=(chosen, other), best=chosen)
    assert questions_for(card, chosen) == [OTHER_PAYMENTS]


def test_none_terms_match():
    chosen = make_offer()
    other = make_offer(source_id="2")
    card = FlatCard(key="flat", offers=(chosen, other), best=chosen)
    assert questions_for(card, chosen) == UNKNOWN_QUESTIONS + [OTHER_PAYMENTS]


def test_none_and_empty_terms_differ(known_offer):
    chosen = replace(known_offer, deposit_raw=None)
    other = replace(chosen, source_id="2", deposit_raw="")
    card = FlatCard(key="flat", offers=(chosen, other), best=chosen)
    assert questions_for(card, chosen) == [
        UNKNOWN_QUESTIONS[1], CONFLICT, OTHER_PAYMENTS
    ]


@pytest.mark.parametrize("price", [40000, None])
def test_percent_and_months_have_known_kinds(known_offer, price):
    chosen = replace(
        known_offer, price=price, deposit_raw="100%", commission_raw="50%"
    )
    card = FlatCard(key="flat", offers=(chosen,), best=chosen)
    assert questions_for(card, chosen) == [OTHER_PAYMENTS]


def test_disallowed_pets_are_known(known_offer):
    chosen = replace(known_offer, pets_raw="нельзя")
    card = FlatCard(key="flat", offers=(chosen,), best=chosen)
    assert questions_for(card, chosen) == [OTHER_PAYMENTS]


def test_same_source_and_id_are_ignored(known_offer):
    other = replace(known_offer, price=50000, deposit_raw=None)
    card = FlatCard(key="flat", offers=(known_offer, other), best=known_offer)
    assert questions_for(card, known_offer) == [OTHER_PAYMENTS]


def test_different_source_with_same_id_conflicts(known_offer):
    other = replace(known_offer, source="avito", price=50000)
    card = FlatCard(key="flat", offers=(known_offer, other), best=known_offer)
    assert questions_for(card, known_offer) == [CONFLICT, OTHER_PAYMENTS]


def test_chosen_offer_and_multiple_conflicts_keep_order(known_offer):
    chosen = make_offer()
    best = replace(known_offer, source_id="2")
    other = replace(known_offer, source="avito", price=50000)
    card = FlatCard(key="flat", offers=(best, chosen, other), best=best)
    assert questions_for(card, chosen) == UNKNOWN_QUESTIONS + [CONFLICT, OTHER_PAYMENTS]


def test_other_fields_do_not_conflict(known_offer):
    other = replace(
        known_offer,
        source_id="2",
        utilities_raw=None,
        pets_raw="нельзя",
        description="Other listing text",
    )
    card = FlatCard(key="flat", offers=(known_offer, other), best=known_offer)
    assert questions_for(card, known_offer) == [OTHER_PAYMENTS]
