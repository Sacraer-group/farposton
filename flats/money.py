import re

from flats.types import Amount


def _normalize(raw: str) -> str:
    text = raw.lower().replace("ё", "е")
    for space in ("\u00a0", "\u2009", "\u202f"):
        text = text.replace(space, " ")
    return re.sub(r"(?<=\d) +(?=\d)", "", text).strip()


def _parse_amount(raw: str | None, rent: int | None) -> Amount:
    text = _normalize(raw) if raw is not None else ""
    if not text:
        return Amount(rub=None, kind="unknown", value=None, raw=raw)
    if text.startswith("без") or text == "нет":
        return Amount(rub=0, kind="none", value=None, raw=raw)

    match = re.search(r"(?<![\w.,+-])(\d+(?:[.,]\d+)?)\s*%", text)
    if match:
        value = float(match[1].replace(",", "."))
        rub = round(rent * value / 100) if rent is not None else None
        return Amount(rub=rub, kind="percent", value=value, raw=raw)

    match = re.search(r"(?<![\w.,+-])(\d+)\s*мес|\bза\s+мес", text)
    if match:
        months = int(match[1]) if match[1] is not None else 1
        rub = rent * months if rent is not None else None
        return Amount(rub=rub, kind="months", value=months, raw=raw)

    match = re.search(r"(?<![\w.,+-])(\d+)\s*(?:₽|руб\b|р\b)", text)
    if match:
        return Amount(rub=int(match[1]), kind="rub", value=None, raw=raw)
    return Amount(rub=None, kind="unknown", value=None, raw=raw)


def parse_deposit(raw: str | None, rent: int | None) -> Amount:
    return _parse_amount(raw, rent)


def parse_commission(raw: str | None, rent: int | None) -> Amount:
    return _parse_amount(raw, rent)


def parse_prepayment(raw: str | None, rent: int | None) -> Amount:
    return _parse_amount(raw, rent)


def utilities_included(raw: str | None) -> bool | None:
    if raw is None:
        return None
    text = _normalize(raw)
    if any(term in text for term in ("не включ", "арендатор", "отдельно", "счетчик")):
        return False
    if any(term in text for term in ("включ", "собственник")):
        return True
    return None


def pets_allowed(raw: str | bool | None) -> bool | None:
    if isinstance(raw, bool) or raw is None:
        return raw
    text = _normalize(raw)
    if text in ("нет", "false", "0") or any(
        term in text for term in ("нельзя", "запрещ")
    ):
        return False
    if text in ("да", "true", "1") or any(
        term in text for term in ("можно", "разреш")
    ):
        return True
    return None
