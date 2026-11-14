"""Conservative identities for the observed Vladivostok address forms."""
import re
import unicodedata

from .house_types import HouseAddress

_TYPES = {"ул": "улица", "улица": "улица", "пр-кт": "проспект", "проспект": "проспект",
          "пер": "переулок", "переулок": "переулок"}


def normalize_house_address(city: str, raw: str) -> HouseAddress | None:
    if not isinstance(city, str) or not isinstance(raw, str):
        return None
    city = " ".join(unicodedata.normalize("NFKC", city).casefold().split())
    text = " ".join(unicodedata.normalize("NFKC", raw).casefold().split())
    text = re.sub(r"\s+подробности о доме$", "", text)
    text = re.sub(r"^г\.?\s*" + re.escape(city) + r"\s*,?\s*", "", text)
    text = text.replace(",", " ")
    text = re.sub(r"\b(ул|пер)\.", r"\1", text)
    text = re.sub(r"\b(?:корпус|корп\.?|кор\.?|к\.)\s*", " корпус ", text)
    text = re.sub(r"\b(?:строение|стр\.?)\s*", " строение ", text)
    text = re.sub(r"\bд\.?\s+(?=\d)", "", text)
    text = " ".join(text.split())
    match = re.fullmatch(r"(?P<street>.+?)\s+(?P<number>\d+[а-яa-z]?(?:/\d+[а-яa-z]?)?)"
                         r"(?:\s+корпус\s+(?P<corpus>\d+[а-яa-z]?))?"
                         r"(?:\s+строение\s+(?P<structure>\d+[а-яa-z]?))?", text)
    if not match:
        return None
    street = match['street'].split()
    if street[0] in _TYPES:
        kind, name = _TYPES[street[0]], " ".join(street[1:])
    elif street[-1] in _TYPES:
        kind, name = _TYPES[street[-1]], " ".join(street[:-1])
    else:
        return None
    name = {"1-я поселковая": "поселковая 1-я", "3-я поселковая": "поселковая 3-я"}.get(name, name)
    if not city or not name:
        return None
    return HouseAddress(city, kind, name, match['number'], match['corpus'] or '', match['structure'] or '')
