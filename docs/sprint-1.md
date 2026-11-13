# Sprint 1 — pure domain logic and source client

Goal: all money, eligibility, grouping and question rules work as pure functions with tests; the API client pages correctly.

All modules depend only on `flats/types.py` (done in S0). Tasks run in parallel.

Test fixture helper: each test file builds `Offer` objects itself with a local `make_offer(**overrides)` function. Do not create a shared fixture file in this sprint.

## S1-T1 Money parsing

- **Owns:** `flats/money.py`, `tests/test_money.py`
- **Steps:** implement the five functions from contracts §4 by the table in contracts §5. Normalize: lowercase, replace NBSP and thin space with space, remove spaces between digits.
- **Tests (at least):** `None`, `""`, `"Без залога"`, `"63 000 ₽"`, `"63000 руб."`, `"50%"` with rent 40000 -> 20000, `"50%"` with rent `None` -> rub None, `"за 1 месяц"` rent 40000 -> 40000, `"за 2 месяца"`, `"по договорённости"` -> unknown, `raw` preserved; utilities and pets examples incl. unknown -> `None`. Plus every raw string listed in `docs/source-sample.md`.
- **Accept:** `pytest -q tests/test_money.py`.

## S1-T2 Costs

- **Owns:** `flats/costs.py`, `tests/test_costs.py`
- **Depends on:** `flats/money.py` signatures (import them; if T1 is not merged yet, tests run after merge).
- **Steps:** implement `compute_costs` by contracts §5 "Costs".
- **Tests:** the 40000 example from contracts; all-unknown terms -> `move_in_known is None` and 3 unknown names; rent unknown + percent commission -> commission unknown; prepayment for 1 month not added twice; "Без комиссии" -> 0 and not in unknown.
- **Accept:** `pytest -q tests/test_costs.py` (after S1-T1 merged).

## S1-T3 Eligibility

- **Owns:** `flats/eligibility.py`, `tests/test_eligibility.py`
- **Steps:** implement `check` by contracts §5 table. It receives ready `Costs`; build `Costs` directly in tests (do not call `compute_costs`). Pets: call `money.pets_allowed(offer.pets_raw)`.
- **Tests:** each row of the table: one reject case, one unknown case, one match case; reject wins over unknown; empty profile -> match when data known; reasons are non-empty for reject/unknown.
- **Accept:** `pytest -q tests/test_eligibility.py` (after S1-T1 merged).

## S1-T4 Grouping

- **Owns:** `flats/grouping.py`, `tests/test_grouping.py`
- **Steps:** implement `group_offers` by contracts §5 "Grouping". Use `costs.compute_costs` for ranking `best`.
- **Tests:** two offers same `group_id` -> one card with 2 offers; `group_id=None` offers stay separate; `best` picks lowest known total; unknown-total offer never beats known one; output order stable (sort by key).
- **Accept:** `pytest -q tests/test_grouping.py` (after S1-T2 merged).

## S1-T5 Questions

- **Owns:** `flats/questions.py`, `tests/test_questions.py`
- **Steps:** implement `questions_for` by contracts §5 table, in table order, no duplicates. Use `money` functions to detect unknowns.
- **Tests:** fully known offer -> only the "other payments" question; each trigger alone; differing terms between two offers of a card -> conflict question.
- **Accept:** `pytest -q tests/test_questions.py` (after S1-T1 merged).

## S1-T6 Source client

- **Owns:** `flats/source.py`, `tests/test_source.py`
- **Steps:**
  1. `fetch_all(key, params, page_size, sleep, http)`: GET `https://api.metrapi.ru/v1/items` with `X-Api-Key`, `limit=page_size`, plus `cursor` from previous `next_cursor` until `null`. Yield each item dict.
  2. Call `sleep(2)` before every request except the first.
  3. On 429 with `detail.reason == "too_soon"`: `sleep(retry_after)` and retry the same page, at most 3 times. On `daily limit exceeded` or other non-200: raise `RuntimeError` with status and body text (never the key).
  4. `timeout=30` on every request.
- **Tests:** fake `http` object with `.get()` returning fake responses: two pages then `null` cursor -> all items yielded, `sleep` called with 2; 429 then 200 -> retried with `retry_after`; 500 -> `RuntimeError`; key absent from error message.
- **Accept:** `pytest -q tests/test_source.py`.

## Sprint check

`pytest -q` passes on `main`. The 40000 example gives 100000 move-in.

Note: T2, T3, T5 import `flats.money`, T4 imports `flats.costs`. They own different files, so they can be written in parallel; merge order T1 -> (T2, T3, T5, T6) -> T4.
