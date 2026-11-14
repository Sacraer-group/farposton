from dataclasses import fields

import pytest

from flats.costs import compute_costs
from flats.types import Amount, Costs, Offer


def make_offer(**overrides) -> Offer:
    values = {field.name: None for field in fields(Offer)}
    values.update(source="farpost", source_id="1", photos=())
    values.update(overrides)
    return Offer(**values)


@pytest.mark.parametrize(
    ("utilities_raw", "monthly_unknown"),
    [
        ("Оплачиваются отдельно", ("utilities",)),
        ("Включены в стоимость", ()),
        (None, ("utilities",)),
        ("", ("utilities",)),
        ("по договорённости", ("utilities",)),
    ],
)
def test_known_costs(utilities_raw, monthly_unknown):
    offer = make_offer(
        price=40000,
        deposit_raw="40 000 ₽",
        commission_raw="50%",
        prepayment_raw="за 1 месяц",
        utilities_raw=utilities_raw,
    )

    assert compute_costs(offer) == Costs(
        rent=40000,
        monthly_known=40000,
        monthly_unknown=monthly_unknown,
        prepayment=Amount(40000, "months", 1, "за 1 месяц"),
        deposit=Amount(40000, "rub", None, "40 000 ₽"),
        commission=Amount(20000, "percent", 50, "50%"),
        move_in_known=100000,
        move_in_unknown=(),
    )


@pytest.mark.parametrize("price", [40000, None, 0])
def test_unknown_terms(price):
    costs = compute_costs(make_offer(price=price))

    assert costs.rent == price
    assert costs.monthly_known == price
    assert costs.monthly_unknown == (
        ("rent", "utilities") if price is None else ("utilities",)
    )
    assert costs.move_in_known == price
    assert costs.move_in_unknown == (("rent",) if price is None else ()) + ("prepayment", "deposit", "commission")


def test_unknown_rent_keeps_fixed_deposit_known():
    costs = compute_costs(make_offer(
        commission_raw="50%", deposit_raw="30 000 ₽", prepayment_raw="за 1 месяц",
    ))

    assert costs.monthly_known is None
    assert costs.monthly_unknown == ("rent", "utilities")
    assert costs.prepayment == Amount(None, "months", 1, "за 1 месяц")
    assert costs.commission == Amount(None, "percent", 50, "50%")
    assert costs.move_in_known == 30000
    assert costs.move_in_unknown == ("rent", "prepayment", "commission")


def test_no_commission_is_known_zero():
    costs = compute_costs(make_offer(
        price=40000,
        prepayment_raw="за 1 месяц",
        deposit_raw="40 000 ₽",
        commission_raw="Без комиссии",
    ))

    assert costs.commission == Amount(0, "none", None, "Без комиссии")
    assert costs.move_in_known == 80000
    assert costs.move_in_unknown == ()


@pytest.mark.parametrize(
    ("prepayment_raw", "expected"),
    [("за 1 месяц", 40000), ("за 2 месяца", 80000)],
)
def test_prepayment_does_not_add_rent_again(prepayment_raw, expected):
    costs = compute_costs(make_offer(
        price=40000,
        prepayment_raw=prepayment_raw,
        deposit_raw="Без залога",
        commission_raw="Без комиссии",
    ))

    assert costs.move_in_known == expected
    assert costs.move_in_unknown == ()


def test_unknown_deposit_preserves_raw_and_other_known_costs():
    offer = make_offer(
        price=40000,
        prepayment_raw="за 1 месяц",
        deposit_raw="  по договорённости  ",
        commission_raw="50%",
    )
    costs = compute_costs(offer)

    assert costs.deposit == Amount(None, "unknown", None, offer.deposit_raw)
    assert costs.move_in_known == 60000
    assert costs.move_in_unknown == ("deposit",)


@pytest.mark.parametrize(
    ("overrides", "unknown"),
    [
        ({"prepayment_raw": "Без предоплаты"}, ("deposit", "commission")),
        ({"deposit_raw": "Без залога"}, ("prepayment", "commission")),
        ({"commission_raw": "Без комиссии"}, ("prepayment", "deposit")),
    ],
)
def test_known_zero_is_kept_when_other_terms_are_unknown(overrides, unknown):
    costs = compute_costs(make_offer(**overrides))

    assert costs.move_in_known == 0
    assert costs.move_in_unknown == ("rent",) + unknown


def test_zero_rent_is_known_for_percent_and_month_terms():
    costs = compute_costs(make_offer(
        price=0,
        prepayment_raw="за 2 месяца",
        deposit_raw="Без залога",
        commission_raw="50%",
        utilities_raw="Включены в стоимость",
    ))

    assert costs.rent == 0
    assert costs.monthly_known == 0
    assert costs.monthly_unknown == ()
    assert costs.prepayment.rub == 0
    assert costs.deposit.rub == 0
    assert costs.commission.rub == 0
    assert costs.move_in_known == 0
    assert costs.move_in_unknown == ()


@pytest.mark.parametrize(("prepayment", "expected"), [(None, 100000), ("Без предоплаты", 100000), ("20 000 ₽", 100000), ("за 1 месяц", 100000), ("за 2 месяца", 140000)])
def test_first_month_always_included(prepayment, expected):
    costs = compute_costs(make_offer(price=40000, deposit_raw="40 000 ₽", commission_raw="50%", prepayment_raw=prepayment))
    assert costs.move_in_known == expected
    assert ("prepayment" in costs.move_in_unknown) == (prepayment is None)


def test_unknown_rent_with_fixed_prepayment_stays_unknown():
    costs = compute_costs(make_offer(prepayment_raw="40 000 ₽", deposit_raw="Без залога", commission_raw="Без комиссии"))
    assert costs.move_in_known == 40000
    assert costs.move_in_unknown == ("rent",)
