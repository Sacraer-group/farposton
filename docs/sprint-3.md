# Sprint 3 — web core (search, flat page, favorites, comparison)

> Historical task specification: the current move-in cost rule in [contracts §5](contracts.md#costs-flatscostspy) supersedes earlier descriptions below.

Goal: first end-to-end result — real listing -> clear card -> correct costs -> save -> compare.

Split into two waves so no task waits on a file it does not own. Wave B starts after wave A is merged.

## Wave 3A

### S3-T1 Search service

- **Owns:** `flats/search.py`, `tests/test_search.py`
- **Steps:** implement `search(conn, profile, include_skipped=False)` by contracts §5 "Search order". Use `repo_offers.all_offers`, `grouping.group_offers`, `costs.compute_costs`, `eligibility.check`, `repo_user.current_decisions`. Similarity: if `profile.sample_photo_url` is set, try `from flats.similar import rank_by_photo`; on `ImportError` ignore similarity (module comes in sprint 4). A card's decision = decision of any of its offers (`save` wins over `skip`).
- **Tests:** DB fixture with 4 offers: one rejected by budget (absent), one unknown deposit (after matches), one skipped (absent; present with `include_skipped`), cheaper match first.
- **Accept:** `pytest -q tests/test_search.py`.

### S3-T2 Web shell

Moved to Sprint 1 as S1-T8.

## Wave 3B (after 3A merged)

### S3-T3 Pages: search and flat

- **Owns:** `flats/web/pages.py` (routes `/` and `/flat/<key>` only), `flats/templates/search.html`, `flats/templates/flat.html`, `flats/templates/_card.html`, `tests/test_pages.py`
- **Steps:**
  1. `bp = Blueprint("pages", __name__)`.
  2. `GET /`: form (GET params) — `monthly_max`, `move_in_max`, `rooms` (checkboxes 1–4), `districts` (multi-select from distinct `offers.district`), `pets_required`. If params present: validate ints ≥ 0 (ignore invalid with a visible message), `repo_user.save_profile`. Render `search.search()` rows with `_card.html`. Show count; if fewer than 3, show "Найдено N" and count of rejected per reason (compute by calling `eligibility.check` on all cards; keep it in this route). Link "Режим свайпов" to `/swipe` (built in sprint 5).
  3. `_card.html`: first photo (`loading="lazy"`, `alt` = title), monthly line `rent + "коммунальные: уточнить"` when unknown, move-in known sum + list of unknown parts, district, area, floor/floors, status label, buttons Save (form `POST /api/decide`) and "Подробнее".
  4. `GET /flat/<key>`: 404 if no offers. Photos gallery (plain `<img>` list), each photo has a form `POST /api/sample` with `photo_url` ("Найти похожие интерьеры"). For **each offer** a block: source, price, raw deposit/commission/prepayment/utilities/pets strings next to parsed `Costs`, link `url` (`rel="noopener noreferrer" target="_blank"`). Description as plain text (autoescape, `white-space: pre-line`). Map link `https://yandex.ru/maps/?pt={lon},{lat}&z=17&l=map` or address search when no coords. Questions from `questions.questions_for(card, chosen)`.
- **Tests:** seeded DB: `/` 200 and contains price; `/flat/<key>` 200, shows raw string `"по договорённости"`; unknown key -> 404; `<script>` in description is escaped.
- **Accept:** `pytest -q tests/test_pages.py`.

### S3-T4 Actions API

Moved to Sprint 1 as S1-T10.

### S3-T5 Favorites and comparison

- **Owns:** `flats/web/compare.py`, `flats/templates/favorites.html`, `flats/templates/compare.html`, `tests/test_compare.py`
- **Steps:**
  1. `bp = Blueprint("compare", __name__)`. Do not edit `flats/web/__init__.py`; the shell already registers `flats.web.compare.bp`.
  2. `GET /favorites`: offers with current decision `save`, as cards with a checkbox (max 5 selected; server rejects >5 with a message). Submit `GET /compare?o=source:source_id&o=...`.
  3. `GET /compare`: table, rows from `docs/architecture.md`-style list: Ежемесячно (rent, known monthly, unknown items), На въезд (prepayment, deposit, commission, known total, unknown items), Квартира (area, rooms, floor/floors), Место (address, district), Условия (pets raw, rent period raw), Что уточнить (`questions_for`), Источник (link to the exact offer). Each column = one chosen offer; never mix offers.
- **Tests:** saved 2 offers -> `/favorites` lists both; `/compare` with 2 shows both prices and the 100000 move-in example; 6 params -> error message; unknown id -> skipped with notice.
- **Accept:** `pytest -q tests/test_compare.py` .

## Sprint check

1. `pytest -q` passes.
2. With real data: `flask --app flats.web:create_app run --host 127.0.0.1` -> set filters, open a flat, save 3, compare. Note results in `docs/state.md`.
