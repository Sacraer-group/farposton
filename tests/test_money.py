import pytest

from flats.money import (
    parse_commission,
    parse_deposit,
    parse_prepayment,
    pets_allowed,
    utilities_included,
)
from flats.types import Amount


@pytest.mark.parametrize("parse", [parse_deposit, parse_commission, parse_prepayment])
@pytest.mark.parametrize(
    ("raw", "rent", "kind", "value", "rub"),
    [
        (None, 40000, "unknown", None, None),
        ("", 40000, "unknown", None, None),
        ("  ", 40000, "unknown", None, None),
        ("Без залога", 40000, "none", None, 0),
        ("Без комиссии", None, "none", None, 0),
        ("  НЕТ  ", 40000, "none", None, 0),
        ("нетто", 40000, "unknown", None, None),
        ("по договорённости", 40000, "unknown", None, None),
        ("частично", 40000, "unknown", None, None),
        ("63000", None, "unknown", None, None),
        ("63 000 ₽", None, "rub", None, 63000),
        ("63\u00a0000 руб.", None, "rub", None, 63000),
        ("63\u2009000 р", None, "rub", None, 63000),
        ("  63\u202f000 РУБ.  ", None, "rub", None, 63000),
        ("0 ₽", None, "rub", None, 0),
        ("50%", 40000, "percent", 50, 20000),
        ("50%", None, "percent", 50, None),
        ("50%", 0, "percent", 50, 0),
        ("12,5%", 40000, "percent", 12.5, 5000),
        ("12.5%", 40000, "percent", 12.5, 5000),
        ("50%", 5, "percent", 50, 2),
        ("50%", 7, "percent", 50, 4),
        ("за 1 месяц", 40000, "months", 1, 40000),
        ("за 2 месяца", 40000, "months", 2, 80000),
        ("за месяц", None, "months", 1, None),
        ("за месяц", 40000, "months", 1, 40000),
        ("за 2 месяца", None, "months", 2, None),
        ("  2 МЕС.  ", 0, "months", 2, 0),
        ("Без комиссии, 50%", 40000, "none", None, 0),
        ("за 2 месяца или 50% и 20 000 ₽", 40000, "percent", 50, 20000),
        ("за 2 месяца или 80 000 ₽", 40000, "months", 2, 80000),
    ],
)
def test_parse_amount(parse, raw, rent, kind, value, rub):
    assert parse(raw, rent) == Amount(rub=rub, kind=kind, value=value, raw=raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("что-то", None),
        ("Оплачиваются отдельно", False),
        ("Не включены", False),
        ("Оплачивает арендатор", False),
        ("По счётчикам", False),
        ("Включены в стоимость", True),
        ("  Оплачивает СОБСТВЕННИК  ", True),
        ("Включены, но счётчики отдельно", False),
        ("Собственник указал: платит арендатор", False),
    ],
)
def test_utilities_included(raw, expected):
    assert utilities_included(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (None, None),
        ("", None),
        ("  ", None),
        ("по договорённости", None),
        ("можно", True),
        ("да", True),
        ("С животными РАЗРЕШЕНО", True),
        ("  TRUE  ", True),
        ("1", True),
        ("нельзя", False),
        ("нет", False),
        ("Строго ЗАПРЕЩЕНЫ", False),
        ("  FALSE  ", False),
        ("0", False),
        ("Можно, но кошкам запрещено", False),
    ],
)
def test_pets_allowed(raw, expected):
    assert pets_allowed(raw) is expected
