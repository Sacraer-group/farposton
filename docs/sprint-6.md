# Sprint 6 — end-to-end checks and usefulness review

Goal: prove the "must check" list on real and fixture data; fix what fails.

## S6-T1 Regression test suite for critical rules

- **Owns:** `tests/test_critical.py`, `tests/fixtures/offers_small.json`
- **Steps:** a fixture of ~10 hand-written API-like items (no real phones) covering every case below; one test per rule, going through `importer.run_import` -> `search.search` / web client:
  1. Percent commission computed only with known rent.
  2. Prepayment "за 1 месяц" not counted twice (40000 example -> 100000).
  3. Unknown payments are never 0 in UI (`"не указано"` shown).
  4. Two offers of one group with different terms: compare page shows the chosen offer's numbers only.
  5. Failed import keeps previous offers and the old "last import" date.
  6. Same filters give same set in `/`, `/swipe`, and with a sample photo (vectors inserted directly).
  7. Offer without photos is present in normal search.
  8. Compare page links to the exact source URL of each offer.
- **Accept:** `pytest -q tests/test_critical.py` all pass. A failing rule is reported with the owning module, not fixed here.

## S6-T2 Source re-check

- **Owns:** `docs/source-sample.md`
- **Steps:** after a full import, query SQLite and update the doc: total offers, offers per source, share with `group_id`, fill rate of deposit/commission/prepayment/utilities/pets, share of raw strings parsed as `unknown` by `money.py`, top 20 unparsed strings.
- **Accept:** doc updated with date and observed numbers.

## S6-T3 Usefulness session (manual)

- **Owns:** `docs/usefulness.md`
- **Steps:** one person, one catalog, one realistic profile. Round A: filters + list only. Round B: plus visual search and comparison. Record time to a 3–5 flat shortlist, number of important unknown terms noticed before calling, and confusing places in UI. Do not record swipe counts as a quality metric.
- **Accept:** doc with observed numbers and a short list of issues.

## Fix sprint (S7, planned after S6)

Each failing rule from S6-T1, each top unparsed string from S6-T2, and each UI issue from S6-T3 becomes one task owned by the module that owns that file in the table of `docs/architecture.md`. Group them so no two tasks share a file.
