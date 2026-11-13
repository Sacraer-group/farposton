# Sprint 2 — import pipeline and repositories

Goal: `flats import` fills SQLite with real Vladivostok offers; a failed import keeps old data; user state can be stored.

## S2-T1 Offer repository

- **Owns:** `flats/repo_offers.py`, `tests/test_repo_offers.py`
- **Steps:**
  1. `upsert_offer(conn, item, run_id)`: map API fields to `offers` columns. `deposit_raw=item["deposit"]`, `commission_raw=item["commission"]`, `prepayment_raw=item["prepayment"]`, `utilities_raw=item["utilities_payer"]`, `pets_raw=item["pets_allowed"]`, `rent_period_raw=item["rent_period"]`; `source_id` and `group_id` stored as `str` (or `NULL`); `photos_json=json.dumps(item.get("photos") or [])`; `raw_json=json.dumps(item, ensure_ascii=False)` **after removing keys `phone`, `phones`, `seller_name`**. Use `INSERT ... ON CONFLICT(source, source_id) DO UPDATE`.
  2. `all_offers` returns `Offer` objects (photos as tuple). `offers_by_key` handles both a `group_id` and a `source:source_id` key.
  3. `last_ok_run` = latest `import_runs` row with `status='ok'`.
- **Tests:** on a `tmp_path` DB with `init_db`: upsert twice updates price; missing optional keys -> `NULL`; phones not in `raw_json`; `offers_by_key` both key forms.
- **Accept:** `pytest -q tests/test_repo_offers.py`.

## S2-T2 User repository

- **Owns:** `flats/repo_user.py`, `tests/test_repo_user.py`
- **Steps:** functions from contracts §4. `get_profile` returns an empty `Profile` (all `None`/empty, `pets_required=False`) when no row. `save_profile` uses `INSERT OR REPLACE` with `id=1`. `decide` inserts a row with UTC ISO time, returns id. `undo_last` sets `undone=1` on the latest not-undone row, returns its id or `None`. `current_decisions` returns latest not-undone action per offer.
- **Tests:** profile round-trip; save then skip same offer -> `skip`; undo -> back to `save`; undo on empty -> `None`.
- **Accept:** `pytest -q tests/test_repo_user.py`.

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
