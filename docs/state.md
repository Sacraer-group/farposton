# State
Updated: 2026-11-14

## Objective
Local app that helps pick 3–5 long-term rentals in Vladivostok worth visiting.

## Baseline verified (2026-11-13)

The results below predate the browser-local decisions change and do not verify that change.
- All five v1 features work on 50 real Metrapi/FarPost offers: search, flat page, visual search (SigLIP 2), favorites + compare, swipes.
- `pytest -q` passes (312 tests) locally on Python 3.11 and in CI.
- `tests/test_critical.py` covers the "must check" list; it fails when prepayment is double-counted or rejected offers leak into results (checked by mutation).
- Headless Chromium at 1200px and 390px: save, skip, drag, undo, favorites, compare, sample photo and reset work; no JS errors.
- Visual search: 803 photos embedded in 12 min on CPU; room-type agreement of top-5 matches 77.8% vs 12.6% random (`docs/ml-check.md`).

## Current change

Save, skip and undo history is stored per browser in local storage; legacy SQLite decisions remain intact and are not automatically imported. The search profile and sample photo remain shared.

On the checked working tree of branch `fix/browser-local-favorites`, based on upstream `main` revision `455ef84b9c55dda6aca0d5cb8e6b3c94ff067683`, `.venv/bin/pytest -q` passed 314 tests in 1.00s; `node --test tests/test_decisions.cjs` passed 15 tests with no failures. The Node tests use a VM with DOM stubs and cover isolated storage and reload, cross-tab events and `pageshow`, undo, invalid data, storage failures, save priority, clearing storage, duplicate animation clicks and a cross-tab drag race. `node --check` passed for both scripts, and `git diff --check` passed. CI now runs the Node tests too.

A real-browser smoke test remains outstanding: Chromium could not create its process singleton Unix socket in the sandbox, and the Playwright headless shell download failed. The Node results do not verify end-to-end browser behavior.

## Remaining
- Verify the updated decision flows in a real browser.
- Manual usefulness session with a real person (S6-T3).
- Style-level visual check of matches by eye.
- Rotate the Metrapi key shared in chat.

## Next
Run the app (`README.md`) and do the usefulness session.
