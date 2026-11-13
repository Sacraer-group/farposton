# Sprint 5 — swipe mode

Goal: an extra way to browse the same search result: save, skip, details, undo. Same filters, order and favorites as the list.

## S5-T1 Swipe page route and template

- **Owns:** `flats/web/swipe.py`, `flats/templates/swipe.html`, `tests/test_swipe.py`
- **Steps:**
  1. `bp = Blueprint("swipe", __name__)`. `GET /swipe`: `rows = search.search(conn, repo_user.get_profile(conn))`, drop rows with decision `save` (already handled). Render all rows into the page as cards stacked in order (server-rendered HTML, each card has `data-source`, `data-source-id`, link to `/flat/<key>`). Empty -> "Подборка закончилась" + link back to `/`.
  2. Under the stack: three real `<button>`s: "Пропустить", "Подробнее", "Сохранить", and "Отменить". Keyboard: ← skip, → save, Backspace/Z undo. Buttons must work without gestures.
- **Tests:** `/swipe` 200 with seeded data, contains cards in the same order as `search()`; saved offer absent; skipped offer absent.
- **Accept:** `pytest -q tests/test_swipe.py`.

## S5-T2 Swipe script

- **Owns:** `flats/static/swipe.js`
- **Steps:** plain JS, no libraries. Top card follows pointer (Pointer Events); release beyond 100 px right -> save, left -> skip; otherwise snap back. Action = `fetch("/api/decide", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({source, source_id, action})})`; on success remove the card and push it on a local undo stack; on error show the message and keep the card. Undo = `POST /api/undo`, then put the last card back on top. Respect `prefers-reduced-motion` (no animation). Never use `innerHTML` with listing data.
- **Accept:** manual check in browser: mouse drag, buttons, keyboard, undo; report what was observed.

## S5-T3 Wiring

- **Owns:** `flats/web/__init__.py`, `flats/templates/base.html`
- **Steps:** register `flats.web.swipe.bp` in the same `try/except ImportError` way; add nav link "Свайпы" `/swipe`; `base.html` keeps `{% block scripts %}` (swipe.html loads `swipe.js` there).
- **Accept:** `pytest -q` passes.

S5-T1 and S5-T3 own different files; S5-T1's tests create the app via `create_app`, so merge S5-T3 before running them, or have them register `swipe.bp` on a bare app in the test.

## Sprint check

`pytest -q`; manual: change filters on `/`, open `/swipe` -> same cards in same order; save 2 -> they are in `/favorites`; undo restores the last one.
