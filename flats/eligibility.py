from flats.money import pets_allowed
from flats.types import Costs, Offer, Profile, Verdict


_MOVE_IN_UNKNOWN_REASONS = {
    "rent": "Аренда не указана",
    "prepayment": "Предоплата не указана",
    "deposit": "Залог не указан",
    "commission": "Комиссия не указана",
}


def _rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def check(offer: Offer, costs: Costs, profile: Profile) -> Verdict:
    reject_reasons: list[str] = []
    unknown_reasons: list[str] = []

    if profile.monthly_max:
        if costs.monthly_known is None:
            unknown_reasons.append("Аренда не указана")
        elif costs.monthly_known > profile.monthly_max:
            reject_reasons.append(
                f"Аренда выше бюджета: {_rub(costs.monthly_known)} > "
                f"{_rub(profile.monthly_max)}"
            )

    if profile.move_in_max:
        if costs.move_in_known is not None and costs.move_in_known > profile.move_in_max:
            reject_reasons.append(
                f"На въезд больше бюджета: {_rub(costs.move_in_known)} > "
                f"{_rub(profile.move_in_max)}"
            )
        else:
            unknown_reasons.extend(
                _MOVE_IN_UNKNOWN_REASONS[name] for name in costs.move_in_unknown
            )

    if profile.rooms:
        if offer.rooms is None:
            unknown_reasons.append("Число комнат не указано")
        elif offer.rooms not in profile.rooms:
            reject_reasons.append(
                f"Комнат: {offer.rooms}, нужно: {', '.join(map(str, profile.rooms))}"
            )

    if profile.districts:
        if offer.district is None:
            unknown_reasons.append("Район не указан")
        elif offer.district not in profile.districts:
            reject_reasons.append(f"Район не из списка: {offer.district}")

    if profile.pets_required:
        allowed = pets_allowed(offer.pets_raw)
        if allowed is False:
            reject_reasons.append("Нельзя с животными")
        elif allowed is None:
            unknown_reasons.append("Не указано, можно ли с животными")

    if reject_reasons:
        return Verdict("reject", tuple(reject_reasons))
    if unknown_reasons:
        return Verdict("unknown", tuple(dict.fromkeys(unknown_reasons)))
    return Verdict("match", ())
