from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["match", "unknown", "reject"]


@dataclass(frozen=True)
class Amount:  # parsed money term
    rub: int | None  # known sum in rubles, None if unknown
    kind: Literal["none", "rub", "percent", "months", "unknown"]
    value: float | None  # percent or months count when kind is percent/months
    raw: str | None  # original string


@dataclass(frozen=True)
class Offer:  # one row of `offers`, parsed
    source: str
    source_id: str
    group_id: str | None
    url: str | None
    address: str | None
    district: str | None
    lat: float | None
    lon: float | None
    rooms: int | None
    area_total: float | None
    floor: int | None
    floors_total: int | None
    price: int | None
    deposit_raw: str | None
    commission_raw: str | None
    prepayment_raw: str | None
    utilities_raw: str | None
    pets_raw: str | None
    rent_period_raw: str | None
    description: str | None
    photos: tuple[str, ...]
    published_at: str | None
    last_seen: str | None


@dataclass(frozen=True)
class Costs:
    rent: int | None
    monthly_known: int | None  # rent + known mandatory monthly payments
    monthly_unknown: tuple[str, ...]  # e.g. ("utilities",)
    prepayment: Amount
    deposit: Amount
    commission: Amount
    move_in_known: int | None  # rent or prepayment (whichever is greater) + deposit + commission, known parts only
    move_in_unknown: tuple[str, ...]  # e.g. ("deposit", "commission")


@dataclass(frozen=True)
class Profile:
    monthly_max: int | None
    move_in_max: int | None
    rooms: tuple[int, ...]
    districts: tuple[str, ...]
    pets_required: bool
    sample_photo_url: str | None


@dataclass(frozen=True)
class Verdict:
    status: Status
    reasons: tuple[str, ...]  # human Russian text, why reject/unknown


@dataclass(frozen=True)
class FlatCard:  # one flat = offers sharing group_id
    key: str  # group_id or f"{source}:{source_id}"
    offers: tuple[Offer, ...]
    best: Offer  # offer used for list display (see grouping rule)


@dataclass(frozen=True)
class SearchRow:
    card: FlatCard
    offer: Offer
    costs: Costs
    verdict: Verdict
    decision: str | None
    similar: tuple[float, str] | None
