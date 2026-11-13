# Sprint 1 — pure domain logic

Goal: money, costs, eligibility, grouping and questions work as pure functions with tests; user state is stored; the web shell starts.

Listings will arrive later as a separate database (owner decision, 2026-11-13). Nothing in this sprint depends on the source. The Metrapi client (old S1-T6) is on hold; the loader is planned when the database format is known.

Each wave runs in parallel branches with no shared files. A wave starts after the previous wave is merged, because its modules import the previous wave's modules.

Test helper: every test file builds `Offer` objects with its own local `make_offer(**overrides)`. No shared fixture file.

## Wave 1A — no dependencies (issues #8, #9, #10)

### S1-T1 Money parsing

- **Owns:** `flats/money.py`, `tests/test_money.py`
- **Steps:** five functions from contracts §4 by the money table in contracts §5. Normalize: lowercase, `ё` -> `е`, NBSP/thin space -> space, remove spaces between digits. `нет` matches as a whole word only.
- **Accept:** `pytest -q tests/test_money.py`.

### S1-T7 User repository (moved from S2-T2)

- **Owns:** `flats/repo_user.py`, `tests/test_repo_user.py`
- **Steps:** as in the issue; uses only `flats.db` and `flats.types`.
- **Accept:** `pytest -q tests/test_repo_user.py`.

### S1-T8 Web shell (moved from S3-T2)

- **Owns:** `flats/web/__init__.py`, `flats/web/format.py`, `flats/templates/base.html`, `flats/static/app.css`, `tests/test_web_shell.py`
- **Steps:** as in the issue. `last_import` is read with direct SQL from `import_runs` (no repository import).
- **Accept:** `pytest -q tests/test_web_shell.py`.

## Wave 1B — after 1A merged (needs `flats.money`)

### S1-T2 Costs

- **Owns:** `flats/costs.py`, `tests/test_costs.py`
- **Steps:** `compute_costs` by contracts §5 "Costs".
- **Tests:** the 40000 example; all-unknown terms -> `move_in_known is None` and 3 unknown names; rent unknown + percent commission -> commission unknown; prepayment for 1 month not added twice; "Без комиссии" -> 0 and not in unknown.

### S1-T3 Eligibility

- **Owns:** `flats/eligibility.py`, `tests/test_eligibility.py`
- **Steps:** `check` by contracts §5 table. Build `Costs` directly in tests (do not call `compute_costs`). Pets via `money.pets_allowed(offer.pets_raw)`.
- **Tests:** per row: one reject, one unknown, one match; reject wins over unknown; empty profile -> match; reasons non-empty for reject/unknown.

### S1-T5 Questions

- **Owns:** `flats/questions.py`, `tests/test_questions.py`
- **Steps:** `questions_for` by contracts §5 table, table order, no duplicates. Unknowns detected via `money` functions. A `FlatCard` is built by hand in tests.
- **Tests:** fully known offer -> only "other payments"; each trigger alone; differing terms between two offers -> conflict question.

## Wave 1C — after 1B merged (needs `flats.costs`)

### S1-T4 Grouping

- **Owns:** `flats/grouping.py`, `tests/test_grouping.py`
- **Steps:** `group_offers` by contracts §5 "Grouping", ranking `best` with `costs.compute_costs`.
- **Tests:** same `group_id` -> one card with 2 offers; `group_id=None` stays separate; `best` = lowest known total; unknown-total never beats known; output sorted by key.

## Sprint check

`pytest -q` green in CI on `main`. The 40000 example gives 100000 move-in.

## When the listings database arrives

New task "Profile listings database" replaces S0-T3: record field fill rates and distinct raw strings of deposit/commission/prepayment/utilities/pets, then update the money table in contracts §5 and add those strings to `tests/test_money.py`.
