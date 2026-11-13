import pytest

from flats.eligibility import check
from flats.types import Amount, Costs, Offer, Profile, Verdict


def make_offer(**kw):
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
    values.update(kw)
    return Offer(**values)


def make_costs(**kw):
    unknown = Amount(None, "unknown", None, None)
    values = dict(
        rent=None,
        monthly_known=None,
        monthly_unknown=("rent", "utilities"),
        prepayment=unknown,
        deposit=unknown,
        commission=unknown,
        move_in_known=None,
        move_in_unknown=("prepayment", "deposit", "commission"),
    )
    values.update(kw)
    return Costs(**values)


def make_profile(**kw):
    values = dict(
        monthly_max=None,
        move_in_max=None,
        rooms=(),
        districts=(),
        pets_required=False,
        sample_photo_url=None,
    )
    values.update(kw)
    return Profile(**values)


@pytest.mark.parametrize(
    ("monthly", "expected"),
    [
        (55000, Verdict("reject", ("Аренда выше бюджета: 55 000 ₽ > 50 000 ₽",))),
        (None, Verdict("unknown", ("Аренда не указана",))),
        (50000, Verdict("match", ())),
        (0, Verdict("match", ())),
    ],
)
def test_monthly_budget(monthly, expected):
    assert check(
        make_offer(),
        make_costs(monthly_known=monthly),
        make_profile(monthly_max=50000),
    ) == expected


@pytest.mark.parametrize(
    ("known", "unknown", "expected"),
    [
        (130000, (), Verdict("reject", ("На въезд больше бюджета: 130 000 ₽ > 100 000 ₽",))),
        (None, ("prepayment",), Verdict("unknown", ("Предоплата не указана",))),
        (100000, (), Verdict("match", ())),
        (0, (), Verdict("match", ())),
        (130000, ("deposit",), Verdict("reject", ("На въезд больше бюджета: 130 000 ₽ > 100 000 ₽",))),
        (50000, ("deposit",), Verdict("unknown", ("Залог не указан",))),
        (50000, ("commission",), Verdict("unknown", ("Комиссия не указана",))),
    ],
)
def test_move_in_budget(known, unknown, expected):
    assert check(
        make_offer(),
        make_costs(move_in_known=known, move_in_unknown=unknown),
        make_profile(move_in_max=100000),
    ) == expected


@pytest.mark.parametrize(
    ("rooms", "expected"),
    [
        (3, Verdict("reject", ("Комнат: 3, нужно: 1, 2",))),
        (None, Verdict("unknown", ("Число комнат не указано",))),
        (1, Verdict("match", ())),
        (0, Verdict("reject", ("Комнат: 0, нужно: 1, 2",))),
    ],
)
def test_rooms(rooms, expected):
    assert check(
        make_offer(rooms=rooms), make_costs(), make_profile(rooms=(1, 2))
    ) == expected


@pytest.mark.parametrize(
    ("district", "expected"),
    [
        ("Чуркин", Verdict("reject", ("Район не из списка: Чуркин",))),
        (None, Verdict("unknown", ("Район не указан",))),
        ("Центр", Verdict("match", ())),
    ],
)
def test_districts(district, expected):
    assert check(
        make_offer(district=district), make_costs(), make_profile(districts=("Центр",))
    ) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("нельзя", Verdict("reject", ("Нельзя с животными",))),
        (None, Verdict("unknown", ("Не указано, можно ли с животными",))),
        ("можно", Verdict("match", ())),
        (True, Verdict("match", ())),
        (False, Verdict("reject", ("Нельзя с животными",))),
    ],
)
def test_pets(raw, expected):
    assert check(
        make_offer(pets_raw=raw), make_costs(), make_profile(pets_required=True)
    ) == expected


def test_reject_wins_over_unknown_and_keeps_only_reject_reasons():
    assert check(
        make_offer(rooms=3, pets_raw="нельзя"),
        make_costs(),
        make_profile(
            monthly_max=50000, move_in_max=100000, rooms=(1, 2),
            districts=("Центр",), pets_required=True,
        ),
    ) == Verdict("reject", ("Комнат: 3, нужно: 1, 2", "Нельзя с животными"))


def test_collects_all_reject_reasons_in_check_order():
    assert check(
        make_offer(rooms=3, district="Чуркин", pets_raw="нельзя"),
        make_costs(monthly_known=55000, move_in_known=130000),
        make_profile(
            monthly_max=50000, move_in_max=100000, rooms=(1, 2),
            districts=("Центр",), pets_required=True,
        ),
    ) == Verdict("reject", (
        "Аренда выше бюджета: 55 000 ₽ > 50 000 ₽",
        "На въезд больше бюджета: 130 000 ₽ > 100 000 ₽",
        "Комнат: 3, нужно: 1, 2",
        "Район не из списка: Чуркин",
        "Нельзя с животными",
    ))


def test_collects_all_unknown_reasons_in_check_order():
    assert check(
        make_offer(),
        make_costs(),
        make_profile(
            monthly_max=50000, move_in_max=100000, rooms=(1, 2),
            districts=("Центр",), pets_required=True,
        ),
    ) == Verdict("unknown", (
        "Аренда не указана",
        "Предоплата не указана",
        "Залог не указан",
        "Комиссия не указана",
        "Число комнат не указано",
        "Район не указан",
        "Не указано, можно ли с животными",
    ))


@pytest.mark.parametrize("limit", [None, 0, False])
@pytest.mark.parametrize("known", [None, 130000])
def test_disabled_limits_match_any_offer(limit, known):
    assert check(
        make_offer(rooms=3, district="Чуркин", pets_raw=False),
        make_costs(monthly_known=known, move_in_known=known),
        make_profile(monthly_max=limit, move_in_max=limit),
    ) == Verdict("match", ())


def test_all_conditions_match_with_unknown_utilities():
    assert check(
        make_offer(rooms=2, district="Центр", pets_raw=True),
        make_costs(
            monthly_known=40000, monthly_unknown=("utilities",),
            move_in_known=90000, move_in_unknown=(),
        ),
        make_profile(
            monthly_max=50000, move_in_max=100000, rooms=(1, 2),
            districts=("Центр",), pets_required=True,
        ),
    ) == Verdict("match", ())
