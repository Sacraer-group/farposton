# State
Updated: 2026-11-13

## Objective
Build the local Vladivostok rental shortlist app by the sprint plan in `docs/README.md`.

## Verified
- S0-T1 (#2, PR #6) and S0-T2 (#3, PR #7) merged: package `flats`, config, types, SQLite schema.
- `pytest -q` -> 10 passed locally (Python 3.11) and in CI (`.github/workflows/tests.yml`, runs on push to `main` and on every PR).

## Remaining
- S0-T3 (#4, draft PR #5) on hold: listings will be delivered later as a separate database (owner decision, 2026-11-13). When it arrives, profile it instead of the Metrapi probe and check its raw terms against contracts §5.
- S1-T6 (Metrapi client) on hold until the listings database format is known; it may be replaced by a loader from that database.
- Sprints 1–6.

## Next
Create Sprint 1 issues for S1-T1..S1-T5 (pure logic, no source dependency). Money parsing follows contracts §5 until real listings arrive.
