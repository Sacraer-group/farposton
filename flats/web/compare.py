from flask import Blueprint, render_template, request

from flats import repo_offers, repo_user
from flats.costs import compute_costs
from flats.grouping import group_offers
from flats.questions import questions_for
from flats.web import get_conn

bp = Blueprint("compare", __name__)
MAX_COMPARE = 5


def _saved(conn):
    decisions = repo_user.current_decisions(conn)
    offers = repo_offers.all_offers(conn)
    cards = {o: card for card in group_offers(offers) for o in card.offers}
    return [(o, cards[o]) for o in offers if decisions.get((o.source, o.source_id)) == "save"]


@bp.get("/favorites")
def favorites():
    saved = [(o, compute_costs(o), card) for o, card in _saved(get_conn())]
    return render_template("favorites.html", saved=saved, max_compare=MAX_COMPARE)


@bp.get("/compare")
def compare():
    by_id = {f"{o.source}:{o.source_id}": (o, card) for o, card in _saved(get_conn())}
    wanted = request.args.getlist("o")
    notes = []
    if len(wanted) > MAX_COMPARE:
        notes.append(f"Можно сравнить не больше {MAX_COMPARE} вариантов — показаны первые {MAX_COMPARE}.")
        wanted = wanted[:MAX_COMPARE]
    missing = [w for w in wanted if w not in by_id]
    if missing:
        notes.append(f"Не найдены в избранном: {len(missing)}.")
    columns = [(o, compute_costs(o), questions_for(card, o)) for o, card in (by_id[w] for w in wanted if w in by_id)]
    return render_template("compare.html", columns=columns, notes=notes)
