from flats.money import (
    parse_commission,
    parse_deposit,
    parse_prepayment,
    pets_allowed,
    utilities_included,
)
from flats.types import FlatCard, Offer


def _terms(offer: Offer) -> tuple[int | str | None, ...]:
    return (offer.price,) + tuple(
        raw.strip().lower() if raw is not None else None
        for raw in (offer.deposit_raw, offer.commission_raw, offer.prepayment_raw)
    )


def questions_for(card: FlatCard, chosen: Offer) -> list[str]:
    questions = []
    if utilities_included(chosen.utilities_raw) is not True:
        questions.append("Какова обычная сумма коммунальных платежей?")
    if parse_deposit(chosen.deposit_raw, chosen.price).kind == "unknown":
        questions.append("Какой залог и на каких условиях он возвращается?")
    if parse_commission(chosen.commission_raw, chosen.price).kind == "unknown":
        questions.append("Есть ли комиссия и какой она суммы?")
    if parse_prepayment(chosen.prepayment_raw, chosen.price).kind == "unknown":
        questions.append("За сколько месяцев нужна предоплата?")
    if pets_allowed(chosen.pets_raw) is None:
        questions.append("Можно ли жить с животными?")

    chosen_terms = _terms(chosen)
    if any(
        (offer.source, offer.source_id) != (chosen.source, chosen.source_id)
        and _terms(offer) != chosen_terms
        for offer in card.offers
    ):
        questions.append(
            "Условия на площадках различаются — подтвердите условия этого предложения"
        )
    questions.append("Есть ли другие платежи при въезде?")
    return questions
