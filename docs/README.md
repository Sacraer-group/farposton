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
| [sprint-1.md](sprint-1.md) | Pure domain logic and source client |
| [sprint-2.md](sprint-2.md) | Import pipeline and repositories |
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

## Sprint order

```
S0 skeleton+probe -> S1 pure logic -> S2 import+repos -> S3 web core -> S4 visual search -> S5 swipes -> S6 checks
```

First useful end-to-end result: end of S3 (real listing -> clear card -> correct costs -> save -> compare).

## Verified facts used by this plan (2026-11-13)

- `GET https://api.metrapi.ru/v1/cities` (no key) lists `{"code":"vladivostok","name":"Владивосток"}`.
- `GET https://api.metrapi.ru/v1/facets?city=vladivostok` (no key) returns Vladivostok districts (`Центр`, `Вторая речка`, `Чуркин`, ...).
- Not yet verified: real `/v1/items` response for long-term rent in Vladivostok (needs an API key), field fill rates, photo URLs lifetime, SigLIP 2 quality on these photos. Task S0-T3 verifies the source.
