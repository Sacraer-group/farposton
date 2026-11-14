from flask import Blueprint, abort, redirect, render_template, request, url_for

from flats import repo_user
from flats.nearby import nearby_places
from flats.repo_houses import get_house_passport, house_links_for_offers
from flats.search import search
from flats.web import get_conn

bp = Blueprint('houses', __name__)


def passport_context(passport):
    page = request.args.get('outage_page', '1')
    if len(page) > 7 or not page.isdigit() or int(page) < 1:
        abort(400)
    page = int(page)
    events = passport['outages']['events']
    pages = max(1, (len(events) + 24) // 25)
    if page > pages:
        abort(404)
    return {'passport': passport, 'outage_events': events[(page-1)*25:page*25],
            'outage_page': page, 'outage_pages': pages, 'nearby': nearby_places(passport['coordinates'])}


@bp.get('/house/<int:house_id>')
def house(house_id):
    if not 0 < house_id <= 9223372036854775807:
        abort(404)
    conn = get_conn()
    passport = get_house_passport(conn, house_id)
    if passport is None:
        abort(404)
    rows = search(conn, repo_user.get_profile(conn))
    links = house_links_for_offers(conn, [(r.offer.source, r.offer.source_id) for r in rows])
    related = [r for r in rows if links[(r.offer.source, r.offer.source_id)]['house_id'] == house_id]
    return render_template('house.html', **passport_context(passport), related=related)


@bp.get('/house/for-offer/<source>/<source_id>')
def house_for_offer(source, source_id):
    conn = get_conn()
    offer = conn.execute('SELECT 1 FROM offers WHERE source=? AND source_id=?', (source, source_id)).fetchone()
    if offer is None:
        abort(404)
    link = house_links_for_offers(conn, [(source, source_id)])[(source, source_id)]
    if link['house_id'] is not None:
        return redirect(url_for('houses.house', house_id=link['house_id']))
    return render_template('house.html', passport=None, link_status=link['status'])
