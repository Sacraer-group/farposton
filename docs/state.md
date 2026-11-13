# State
Updated: 2026-11-13

## Objective
Local app that helps pick 3–5 long-term rentals in Vladivostok worth visiting.

## Verified
- All five v1 features work on 50 real Metrapi/FarPost offers: search, flat page, visual search (SigLIP 2), favorites + compare, swipes.
- `pytest -q` passes (312 tests) locally on Python 3.11 and in CI.
- `tests/test_critical.py` covers the "must check" list; it fails when prepayment is double-counted or rejected offers leak into results (checked by mutation).
- Headless Chromium at 1200px and 390px: save, skip, drag, undo, favorites, compare, sample photo and reset work; no JS errors.
- Visual search: 803 photos embedded in 12 min on CPU; room-type agreement of top-5 matches 77.8% vs 12.6% random (`docs/ml-check.md`).

## Remaining
- Manual usefulness session with a real person (S6-T3).
- Style-level visual check of matches by eye.
- Rotate the Metrapi key shared in chat.

## Next
Run the app (`README.md`) and do the usefulness session.
