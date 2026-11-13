from flats.costs import compute_costs
from flats.types import FlatCard, Offer


def _key(offer: Offer) -> str:
    return offer.group_id if offer.group_id is not None else f"{offer.source}:{offer.source_id}"


def _cost_rank(offer: Offer) -> tuple[bool, float]:
    costs = compute_costs(offer)
    known = costs.monthly_known is not None and costs.move_in_known is not None
    has_unknown = not known or bool(costs.move_in_unknown)
    total = costs.monthly_known + costs.move_in_known if known else float("inf")
    return has_unknown, total


def _rank(offers: list[Offer]) -> list[Offer]:
    # Stable sorts from the weakest tie-breaker to the strongest key.
    ranked = sorted(offers, key=lambda o: (o.source, o.source_id))
    ranked.sort(key=lambda o: o.last_seen or "", reverse=True)
    ranked.sort(key=lambda o: o.last_seen is None)
    ranked.sort(key=_cost_rank)
    return ranked


def group_offers(offers: list[Offer]) -> list[FlatCard]:
    groups: dict[str, list[Offer]] = {}
    for offer in offers:
        groups.setdefault(_key(offer), []).append(offer)
    cards = []
    for key in sorted(groups):
        ranked = tuple(_rank(groups[key]))
        cards.append(FlatCard(key=key, offers=ranked, best=ranked[0]))
    return cards
