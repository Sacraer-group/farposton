import sqlite3
from dataclasses import replace

from flats import repo_offers
from flats.costs import compute_costs
from flats.eligibility import check
from flats.grouping import group_offers
from flats.types import Profile, SearchRow

_STATUS_ORDER = {"match": 0, "unknown": 1}


def _card_decision(card, decisions) -> str | None:
    actions = {decisions.get((o.source, o.source_id)) for o in card.offers}
    if "save" in actions:
        return "save"
    return "skip" if "skip" in actions else None


def _base_order(row: SearchRow) -> tuple:
    monthly = row.costs.monthly_known
    return (_STATUS_ORDER[row.verdict.status], monthly is None, monthly or 0, row.card.key)


def search(conn: sqlite3.Connection, profile: Profile, *, include_skipped: bool = False,
           decisions: dict[tuple[str, str], str] | None = None) -> list[SearchRow]:
    decisions = decisions or {}
    rows = []
    for card in group_offers(repo_offers.all_offers(conn)):
        decision = _card_decision(card, decisions)
        if decision == "skip" and not include_skipped:
            continue
        for offer in card.offers:
            costs = compute_costs(offer)
            verdict = check(offer, costs, profile)
            if verdict.status != "reject":
                rows.append(SearchRow(card, offer, costs, verdict, decision, None))
                break

    scores = {}
    if profile.sample_photo_url and rows:
        try:
            from flats.similar import MODEL, rank_by_photo
        except ImportError:
            pass  # visual search is not installed yet
        else:
            scores = rank_by_photo(conn, profile.sample_photo_url, [r.card.key for r in rows], MODEL)

    rows = [replace(r, similar=scores.get(r.card.key)) for r in rows]
    rows.sort(key=_base_order)
    rows.sort(key=lambda r: -r.similar[0] if r.similar else float("inf"))
    return rows
