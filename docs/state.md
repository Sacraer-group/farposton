# State
Updated: 2026-11-13

## Objective
Build the local Vladivostok rental shortlist app by the sprint plan in `docs/README.md`.

## Verified
- S0-T1 (#2, PR #6) and S0-T2 (#3, PR #7) merged: package `flats`, config, types, SQLite schema.
- `pytest -q` -> 10 passed locally (Python 3.11) and in CI (`.github/workflows/tests.yml`, runs on push to `main` and on every PR).

## Remaining
- S0-T3 (#4, draft PR #5) blocked: no `METRAPI_KEY`. After the real sample, check `docs/source-sample.md` against contracts §5 money table before Sprint 1.
- Sprints 1–6.

## Next
Provide `METRAPI_KEY` to the S0-T3 executor; in parallel Sprint 1 issues can be created (money parsing uses contracts §5 until the sample arrives).
