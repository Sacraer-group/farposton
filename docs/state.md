# State
Updated: 2026-11-13

## Objective
Build the local Vladivostok rental shortlist app by the sprint plan in `docs/README.md`.

## Verified
- S0-T1 (#2) and S0-T2 (#3) merged: package `flats`, config, types, SQLite schema.
- S1-T1 (#8), S1-T7 (#9), S1-T8 (#10) merged: money parsing, user profile/decisions repo, Flask shell.
- `pytest -q` passes locally (Python 3.11) and in CI (`.github/workflows/tests.yml`, runs on push to `main` and on every PR).

## Remaining
- S0-T3 (#4, draft PR #5) on hold: listings will be delivered later as a separate database (owner decision, 2026-11-13). When it arrives, profile it instead of the Metrapi probe and check its raw terms against contracts §5.
- S1-T6 (Metrapi client) on hold until the listings database format is known; it may be replaced by a loader from that database.
- Sprints 1–6.

## Next
Sprint 1 wave 1A merged (#8 money, #9 user repo, #10 web shell; 159 tests green in CI).
Wave 1B in progress: #14 (S1-T2 costs), #15 (S1-T3 eligibility), #16 (S1-T5 questions) — three parallel branches. After merge: wave 1C issue S1-T4 grouping. See `docs/sprint-1.md`.
