# Sprint 2 — import pipeline and repositories

Goal: `flats import` fills SQLite with real Vladivostok offers (source: the listings database; S2-T3/S2-T4 are re-scoped when its format is known); a failed import keeps old data; user state can be stored.

## S2-T1 Offer repository

Moved to Sprint 1 as S1-T9. Input is a normalized dict, not a raw Metrapi item; the loader (re-scoped S2-T3/S2-T4) maps source fields to it.

## S2-T2 User repository

Moved to Sprint 1 as S1-T7.

## S2-T3 Importer

- **Owns:** `flats/importer.py`, `tests/test_importer.py`
- **Steps:**
  1. `run_import(conn, items)`: insert `import_runs` row `running` and commit. Then in **one transaction** (`with conn:`) for every item: validate it is a dict with non-empty `source`, `source_id`, and `deal_type == "rent"`; skip invalid items and count them; call `repo_offers.upsert_offer`. On success set `status='ok'`, `fetched`, `finished_at`. On any exception: transaction rolls back, then set `status='failed'`, `error=str(exc)[:500]`, re-raise.
  2. Import is upsert only. Never delete offers missing from the new import.
- **Tests:** two items -> run ok, 2 offers; generator raising mid-way -> run `failed`, offers from a previous ok run unchanged, no half-written new offers.
- **Accept:** `pytest -q tests/test_importer.py` (after S2-T1 merged).

## S2-T4 CLI

- **Owns:** `flats/cli.py`, `tests/test_cli.py`
- **Steps:** `argparse` with subcommands:
  - `init-db` -> `db.init_db(db.connect())`.
  - `import [--limit N]` -> params `city=vladivostok, deal_type=rent, rent_period=long, realty_type=flat`; `source.fetch_all(config.metrapi_key(), params)`; if `--limit`, stop after N items (`itertools.islice`); `importer.run_import`. Print `imported N offers, run #id`. Return 1 with a message on error.
  - `embed [--limit N]` -> placeholder printing `not implemented, see sprint 4` and return 2 (replaced in S4-T3).
- **Tests:** `init-db` with `FLATS_DB` in `tmp_path` creates file; `import` with monkeypatched `flats.source.fetch_all` returning 2 dicts -> exit 0 and 2 rows.
- **Accept:** `pytest -q tests/test_cli.py`.

## S2-T5 README

- **Owns:** `README.md` (repo root)
- **Steps:** what the app does (2 sentences), Python version, setup commands from `docs/agent-rules.md`, `export METRAPI_KEY=...`, `flats init-db`, `flats import --limit 50`, `flats import`, link to `docs/`. Mark the web command "added in sprint 3". Mark any command you did not run as unverified.
- **Accept:** each command you ran is listed with its observed result in your report.

## Sprint check

With a real key: `flats init-db && flats import --limit 50` -> 50 rows in `offers`; then full `flats import` -> `import_runs` row `ok`. Record the observed counts in `docs/state.md` (planner).
