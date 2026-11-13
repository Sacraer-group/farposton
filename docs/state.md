# State
Updated: 2026-11-13

## Objective
Build the local Vladivostok rental shortlist app by the sprint plan in `docs/README.md`.

## Verified
- Sprint 0: package `flats`, config, types, SQLite schema (#2, #3).
- Sprint 1 done: money parsing, costs, eligibility, questions, grouping, user repo, offers repo, Flask shell, actions API (#8, #9, #10, #14, #15, #16, #20, #21, #22).
- Sprint 2 wave 2A done: search service, importer (one transaction, failed run keeps data), demo dataset of 26 invented offers (#23, #24, #25).
- `pytest -q` -> 279 passed locally (Python 3.11); CI green on `main`.
- End-to-end on demo data (import -> search): reference offer move-in 100000; group `demo-g1` shows the conflict question.

## Remaining
- S0-T3 (#4, draft PR #5) on hold until the listings database arrives; then profile it and write a loader to normalized dicts.
- Sprint 2 wave 2B: CLI (`init-db`, `import-json`, `demo`) and README.
- Sprint 3 wave B pages, Sprint 4 visual search, Sprint 5 swipes, Sprint 6 checks.

## Next
Create Sprint 2 wave 2B issues (CLI, README) and Sprint 3 page issues (search + flat pages, favorites + compare).
