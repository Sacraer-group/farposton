import html
import re

API_URL = "https://api.metrapi.ru/v1/items"
VLADIVOSTOK_RENT = {
    "city": "vladivostok",
    "deal_type": "rent",
    "rent_period": "long",
    "realty_type": "flat",
    "with_photo": 1,
}
# Never stored: contacts and seller identity.
_PRIVATE = ("phone", "phones", "phone_redirect", "phone_ads_count", "phone_checked_at", "seller_name", "seller_company")
_PETS_RE = re.compile(r"(можно|нельзя)\s+с\s+животными", re.I)
_PHONE_RE = re.compile(r"(?<![\w/])(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\w)")


def normalize(item: dict) -> dict:
    """Map one Metrapi item to the normalized dict of repo_offers.upsert_offer."""
    # Sellers sometimes type a phone number into the free text; contacts are out of scope.
    description = _PHONE_RE.sub("[телефон скрыт]", html.unescape(item.get("description") or "")) or None
    pets = item.get("pets_allowed")
    if pets is None and description:
        # FarPost puts the pets condition only into the generated description text.
        match = _PETS_RE.search(description)
        pets = match.group(0) if match else None
    return {
        "source": item["source"],
        "source_id": str(item["source_id"]),
        "group_id": item.get("group_id"),
        "url": item.get("url"),
        "address": item.get("address"),
        "district": item.get("district"),
        "lat": item.get("lat"),
        "lon": item.get("lon"),
        "rooms": item.get("rooms"),
        "area_total": item.get("area_total"),
        "floor": item.get("floor"),
        "floors_total": item.get("floors_total"),
        "price": item.get("price"),
        "deposit_raw": item.get("deposit"),
        "commission_raw": item.get("commission"),
        "prepayment_raw": item.get("prepayment"),
        "utilities_raw": item.get("utilities_payer"),
        "pets_raw": pets,
        "rent_period_raw": item.get("rent_period"),
        "description": description,
        "photos": list(item.get("photos") or []),
        "published_at": item.get("published_at"),
        "last_seen": item.get("last_seen"),
        "raw": {k: v for k, v in item.items() if k not in _PRIVATE and k != "description"},
    }
