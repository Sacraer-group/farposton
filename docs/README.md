# Development plan: Vladivostok long-term rental shortlist

Closed study project, run locally by one user.

**Goal:** help a person pick 3–5 long-term rental flats in Vladivostok worth visiting: they match the conditions, their costs are clear, and their interior is attractive.

**User result:** a short candidate list, a payment comparison, and questions to ask before a viewing.

## Documents

| File | Purpose |
| --- | --- |
| [architecture.md](architecture.md) | Scope, modules, data flow, stack, out-of-scope list |
| [constraints.md](constraints.md) | Real restrictions (source API, secrets, money rules) |
| [contracts.md](contracts.md) | DB schema, data types, function signatures every task must follow |
| [agent-rules.md](agent-rules.md) | How an executor agent takes, does, checks, and hands over a task |
| [sprint-0.md](sprint-0.md) | Skeleton, schema, source probe |
| [sprint-1.md](sprint-1.md) | Pure domain logic |
| [sprint-2.md](sprint-2.md) | Import, demo data, search |
| [source-sample.md](source-sample.md) | Observed real data |
| [sprint-3.md](sprint-3.md) | Web: search, flat page, favorites, comparison |
| [sprint-4.md](sprint-4.md) | Visual interior search (ML) |
| [sprint-5.md](sprint-5.md) | Swipe mode |
| [sprint-6.md](sprint-6.md) | End-to-end checks and usefulness review |
| [state.md](state.md) | Current progress snapshot |

## How the plan works

- Every task has an ID (`S<sprint>-T<n>`), an owner zone (exact files it may create or edit), inputs, steps, and an acceptance check.
- **Inside one sprint no two tasks touch the same file.** Tasks of one sprint can run in parallel.
- A sprint starts only after the previous sprint is merged and its sprint check passes.
- Interfaces between tasks are fixed in [contracts.md](contracts.md). A task never changes a contract; if a contract is wrong, the agent stops and reports it.
- Each task is a GitHub issue, worked in its own branch `s<x>-t<y>-<slug>` and merged into `main` by PR. Tasks of one sprint run in parallel branches.

## Status and remaining work (2026-11-13)

Done: domain logic, repositories, importer, search service, actions API, CLI, search and flat pages, 50 real Metrapi offers (`docs/source-sample.md`).

Remaining, in order:

| Step | Owns | Result |
| --- | --- | --- |
| R1 Favorites + compare | `flats/web/compare.py`, `flats/templates/favorites.html`, `flats/templates/compare.html`, `tests/test_compare.py` | Saved list, side-by-side table of up to 5 offers with questions |
| R2 Visual search | `flats/embed.py`, `flats/similar.py`, `flats/cli.py` (`embed`), sample banner in `search.html`/`_card.html` | Photos -> SigLIP 2 vectors; list re-ordered by the chosen photo; matched photo shown |
| R3 Swipes | `flats/web/swipe.py`, `flats/templates/swipe.html`, `flats/static/swipe.js` | Same search rows, save/skip/undo by buttons, keys and drag |
| R4 Critical checks | `tests/test_critical.py` | The "must check" list from `architecture.md` as tests |

## Verified facts used by this plan (2026-11-13)

- `GET https://api.metrapi.ru/v1/cities` (no key) lists `{"code":"vladivostok","name":"Владивосток"}`.
- `GET https://api.metrapi.ru/v1/facets?city=vladivostok` (no key) returns Vladivostok districts (`Центр`, `Вторая речка`, `Чуркин`, ...).
- Verified with a trial key: 50 real offers, see `source-sample.md`. Not yet verified: SigLIP 2 quality on these photos.
