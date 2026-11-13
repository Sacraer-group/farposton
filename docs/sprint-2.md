# Sprint 2 — import pipeline, demo data, search service

Goal: offers get into SQLite through one import path; a made-up demo dataset lets the app run before the real listings database arrives; one search function drives every view.

Listings come later as a separate database (owner decision, 2026-11-13). The importer takes **normalized dicts** (`repo_offers.upsert_offer` format); a source-specific loader that maps the database fields to these dicts is planned once its format is known.

Starts after Sprint 1 wave 1C (#20, #21, #22) is merged.

## Wave 2A (issues #23, #24, #25) — parallel, no shared files

| Task | Owns | Summary |
| --- | --- | --- |
| S2-T6 Search service | `flats/search.py`, `tests/test_search.py` | Profile -> ordered `SearchRow` list (contracts §5 "Search order") |
| S2-T3 Importer | `flats/importer.py`, `tests/test_importer.py` | `run_import(conn, rows)` in one transaction, failed run keeps old data; `load_json(path)` |
| S2-T7 Demo dataset | `flats/demo/offers.json`, `tests/test_demo_data.py` | 24–30 invented Vladivostok offers covering all money/eligibility edge cases |

## Wave 2B — after 2A

| Task | Owns | Summary |
| --- | --- | --- |
| S2-T4 CLI | `flats/cli.py`, `tests/test_cli.py`, `pyproject.toml` | `flats init-db`, `flats import-json <path>`, `flats demo` (loads `flats/demo/offers.json`; add `demo/*.json` to package data) |
| S2-T5 README | `README.md` | Setup, `flats demo`, run the web app, link to `docs/` |

## Later (when the listings database arrives)

- Profile the database (replaces S0-T3).
- Loader `flats/loader_<name>.py`: database rows -> normalized dicts -> `importer.run_import`, plus a CLI subcommand.
- Update the money table in contracts §5 with real raw strings.

## Sprint check

`pytest -q` green in CI. `flats init-db && flats demo` loads the demo set; `search()` on the demo DB returns the reference 40000 offer with move-in 100000.
