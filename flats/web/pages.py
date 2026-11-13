from dataclasses import replace

from flask import Blueprint, abort, render_template, request

from flats import repo_offers, repo_user
from flats.costs import compute_costs
from flats.grouping import group_offers
from flats.questions import questions_for
from flats.search import search
from flats.web import get_conn

bp = Blueprint("pages", __name__)


def _int(name, errors):
    value = request.args.get(name, "").replace(" ", "")
    if not value:
        return None
    if value.isdigit():
        return int(value)
    errors.append(f"Неверное число: {value}")
    return None


@bp.get("/")
def index():
    conn = get_conn()
    profile = repo_user.get_profile(conn)
    errors = []
    if request.args.get("apply"):
        profile = replace(
            profile,
            monthly_max=_int("monthly_max", errors),
            move_in_max=_int("move_in_max", errors),
            rooms=tuple(int(r) for r in request.args.getlist("rooms") if r.isdigit()),
            districts=tuple(request.args.getlist("districts")),
            pets_required=bool(request.args.get("pets_required")),
        )
        repo_user.save_profile(conn, profile)
    rows = search(conn, profile)
    total = len(group_offers(repo_offers.all_offers(conn)))
    districts = [r[0] for r in conn.execute(
        "SELECT DISTINCT district FROM offers WHERE district IS NOT NULL ORDER BY district")]
    return render_template("search.html", rows=rows, total=total, profile=profile,
                           districts=districts, errors=errors)


@bp.get("/flat/<path:key>")
def flat(key):
    offers = repo_offers.offers_by_key(get_conn(), key)
    if not offers:
        abort(404)
    card = group_offers(offers)[0]
    blocks = [(o, compute_costs(o), questions_for(card, o)) for o in card.offers]
    return render_template("flat.html", card=card, blocks=blocks)
