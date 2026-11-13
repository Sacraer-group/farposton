# State
Updated: 2026-11-13

## Objective
Build the local Vladivostok rental shortlist app by the sprint plan in `docs/README.md`.

## Verified
- Sprints 0–1 and Sprint 2 wave A merged: domain logic, repositories, importer, search service, actions API.
- Real data: 50 Metrapi/FarPost long-term rentals in Vladivostok (`flats/demo/metrapi_vladivostok.json`, 2026-11-13, trial API key). Findings: `docs/source-sample.md`.
- CLI `flats init-db | import-json | demo`; web search page and flat page work on real data (#32).
- `pytest -q` -> 291 passed; CI green on `main`.

## Remaining
1. Favorites + comparison page (S3-T5).
2. Visual interior search: embeddings + similarity (Sprint 4).
3. Swipe mode (Sprint 5); end-to-end checks (Sprint 6).
4. Metrapi key was shared in chat: rotate it after the trial.

## Next
Create issues for S3-T5 (favorites/compare) and Sprint 4 wave A (embed, similar).
