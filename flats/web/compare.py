from flask import Blueprint, render_template, request

from flats import repo_houses, repo_offers
from flats.costs import compute_costs
from flats.grouping import group_offers
from flats.questions import questions_for
from flats.web import get_conn

bp = Blueprint("compare", __name__)
MAX_COMPARE = 5


def _offers(conn):
    offers = repo_offers.all_offers(conn)
    cards = {o: card for card in group_offers(offers) for o in card.offers}
    return [(o, cards[o]) for o in offers]


@bp.get("/favorites")
def favorites():
    offers = [(o, compute_costs(o), card) for o, card in _offers(get_conn())]
    return render_template("favorites.html", offers=offers, max_compare=MAX_COMPARE)


@bp.get("/compare")
def compare():
    conn = get_conn()
    by_id = {f"{o.source}:{o.source_id}": (o, card) for o, card in _offers(conn)}
    wanted = request.args.getlist("o")
    notes = []
    if len(wanted) > MAX_COMPARE:
        notes.append(f"Можно сравнить не больше {MAX_COMPARE} вариантов — показаны первые {MAX_COMPARE}.")
        wanted = wanted[:MAX_COMPARE]
    missing = [w for w in wanted if w not in by_id]
    if missing:
        notes.append(f"Объявления не найдены: {len(missing)}.")
    columns = [(o, compute_costs(o), questions_for(card, o)) for o, card in (by_id[w] for w in wanted if w in by_id)]
    links = repo_houses.house_links_for_offers(conn, [(o.source, o.source_id) for o, _, _ in columns])
    passports = {house_id: repo_houses.get_house_passport(conn, house_id)
                 for house_id in {link['house_id'] for link in links.values() if link['house_id'] is not None}}
    house_by_offer = {f"{source}:{source_id}": passports.get(link['house_id'])
                      for (source, source_id), link in links.items()}
    return render_template("compare.html", columns=columns, notes=notes, house_by_offer=house_by_offer)
