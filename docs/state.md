# State
Updated: 2026-11-13

## Objective
Local app that helps pick 3–5 long-term rentals in Vladivostok worth visiting.

## Previously verified baseline

The following checks predate the UI redesign and do not verify it.
- All five v1 features work on 50 real Metrapi/FarPost offers: search, flat page, visual search (SigLIP 2), favorites + compare, swipes.
- `pytest -q` passes (312 tests) locally on Python 3.11 and in CI.
- `tests/test_critical.py` covers the "must check" list; it fails when prepayment is double-counted or rejected offers leak into results (checked by mutation).
- Headless Chromium at 1200px and 390px: save, skip, drag, undo, favorites, compare, sample photo and reset work; no JS errors.
- Visual search: 803 photos embedded in 12 min on CPU; room-type agreement of top-5 matches 77.8% vs 12.6% random (`docs/ml-check.md`).

## Redesign verification

The redesigned UI organizes the journey around map, address and house context, costs, comparison and questions. House year, material, management company, outages and repairs are absent from the source snapshot and shown as unavailable.

Checks on the working tree based on `a697ee5` (not a committed revision):
- Full pytest: 313 passed, 2 skipped because optional Pillow was unavailable. Focused page, comparison and critical tests: 23 passed. Regression coverage includes saving grouped offers, unknown house data and escaped map attributes.
- `node --check` passed for `app.js` and `map.js`; the map DOM smoke check passed.
- Browser screenshot, visual and responsive checks were not completed: the local server was unreachable from the browser and browser policy blocked a data URL preview.

## Remaining
- Visual and responsive browser review of the redesigned UI.
- Manual usefulness session with a real person (S6-T3).
- Style-level visual check of matches by eye.
- Rotate the Metrapi key shared in chat.

## Next
Run the app (`README.md`), review the redesign in a browser, then do the usefulness session.
