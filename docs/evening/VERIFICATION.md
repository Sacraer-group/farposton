# Evening MVP — verification

## Scope

Implemented in `/home/recara/farposton-evening` on branch `feature/evening-planner` (working tree already had unrelated untracked paths: `.opencode/agents/evening-*.md`, `docs/fable-product-research-2026-11-13/`; they were not modified).

## Artifacts

- Spec/docs: `docs/evening/FABLE-DRAFT.md` (raw extracted from `/home/recara/farposton/.demo/evening-build-20261113/spec-text.jsonl`), `docs/evening/SPEC.md`, `docs/evening/ARCHITECTURE.md`, `docs/evening/ACCEPTANCE.md`, `docs/evening/VERIFICATION.md`.
- Runtime/package: `evening/` (`seed.py`, `models.py`, `recommender.py`, `store.py`, `routes.py`, `timeutil.py`), `evening_app.py`.
- UI: `web/static/evening.html`, `web/static/evening-plan.html`.
- Integration: minimal router include in `web/app.py`.
- Tests: `tests/test_evening_mvp.py` (F1–F7 + timezone/SQL/TTL/cancel/rescheduled/full-window/plan-B/broken-seed stale cases), `tests/test_evening_docker_config_F8.py`.
- Docker: `Dockerfile.evening`, `compose.evening.yaml`, `DOCKER-EVENING.md`, `.dockerignore`.

## Fixes after reviewer P1 findings

1. `GET /api/evening/events`: now excludes events with `starts_at <= now` (Asia/Vladivostok) in addition to `cancelled/past` status; added `test_F1_events_excludes_scheduled_event_in_past`.
2. `GET /api/evening/plans/{id}`: seed load/revalidation wrapped so broken/unavailable seed returns `200` with snapshot and `current.event_status=stale` (not 500); added `test_F7_broken_seed_json_returns_snapshot_stale`, `test_F7_unreadable_seed_returns_snapshot_stale`, `test_F7_rescheduled_current_status`. Valid seed is not rewritten by runtime logic.

## Commands and results

### 1) Demo fixture prepare (required before `make test`)

```sh
cd /home/recara/farposton-evening
PYTHONPATH=/home/recara/farposton-evening /home/recara/farposton/.venv/bin/python demo/local.py prepare
```

Result: `Verified synthetic fixture extracted to /home/recara/farposton-evening/.demo/forecast`.

### 2) Evening-focused tests

```sh
cd /home/recara/farposton-evening
PYTHONPATH=/home/recara/farposton-evening /home/recara/farposton/.venv/bin/python -m pytest -q tests/test_evening_mvp.py tests/test_evening_docker_config_F8.py
```

Result: **43 passed, 1 warning** (third-party DeprecationWarning from `starlette.testclient`/anyio).

### 3) Full project tests

```sh
cd /home/recara/farposton-evening
make test VENV=/home/recara/farposton/.venv/bin/python
```

Result: **438 passed, 6 skipped, 1 warning** in pytest; `node tests/test_dom_helper.cjs` → `DOM helper regression checks passed`. Skips are pre-existing (`facts.json` not built): `test_docs_and_invariants.py` and `test_shelf.py`.

### 4) Docker rebuild and run (after fixes)

```sh
cd /home/recara/farposton-evening
sudo -n docker compose -f compose.evening.yaml up -d --build
sleep 6
sudo -n docker compose -f compose.evening.yaml ps
```

Result: image rebuilt (`farposton-evening-evening:latest`) and container `farposton-evening-evening-1` healthy on `127.0.0.1:18447->18447/tcp`.

### 5) Real HTTP e2e (POST/GET, after fixes)

```sh
curl -fsS http://127.0.0.1:18447/api/evening/events
curl -fsS -X POST http://127.0.0.1:18447/api/evening/plans \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"04728bad-edd6-595b-98c2-35e47ebe7ae4","date":"2026-11-14","meal_start":"17:00","meal_end":"18:00","required_features":[],"preferred_features":["dog"],"max_distance_m":3000}'
curl -fsS http://127.0.0.1:18447/api/evening/plans/<plan_id>
```

Result: events returned synthetic scheduled items; POST returned `201` with plan `dad26d25-0db7-4b78-9b0d-eea9c223d3c4` (snapshot, primary/alternative, sources/checked_at/disclaimer/current); GET returned `200` with snapshot+current.

### 6) Restart with volume persistence (after fixes)

```sh
sudo -n docker compose -f compose.evening.yaml restart
sleep 6
sudo -n docker compose -f compose.evening.yaml ps
curl -fsS http://127.0.0.1:18447/api/evening/plans/dad26d25-0db7-4b78-9b0d-eea9c223d3c4
curl -sS -o /tmp/old.json -w '%{http_code}' http://127.0.0.1:18447/api/evening/plans/b55c496b-ffe1-4e81-b5c0-4d3b5d20611d
```

Result: container healthy after restart; new plan and previously created plan `b55c496b-ffe1-4e81-b5c0-4d3b5d20611d` both returned `200` from `evening_state` volume.

## Limitations / notes

- Seed dates/statuses/UUIDs are generated relative to runtime "today" in `Asia/Vladivostok`; recorded IDs reflect the container seed at verification time.
- `/api/evening/events` lists non-cancelled/non-past and not-yet-started events (including `rescheduled` status entries if their start is in future); plan creation for rescheduled/cancelled/past/unavailable events is rejected with `409 event_unavailable`. *(Historical note from earlier run; superseded by contract clarification below.)*
- Docker image includes project `web/` and `src/` trees (allowlist copy) and installs from existing `web/requirements.txt`; heavier than minimal but uses only the existing dependency list. Volume mounted at `/app/.demo/evening` for nonroot writability.
- No 2ГИС/external API calls, no LLM, no raw text/contacts stored. Synthetic demo only.
- Unrelated pre-existing untracked worktree entries were not modified. No commit/push performed.

## Update: contract clarification + rerun (2026-11-13, this pass)

Scope of this pass (allowed files only): `evening/routes.py`, `tests/test_evening_mvp.py`, `docs/evening/SPEC.md`, `docs/evening/ACCEPTANCE.md`, `docs/evening/VERIFICATION.md`. No `BROWSER.md` present; browser-agent edits not applicable.

### Contract changes applied
- `GET /api/evening/plans/{id}`: invalid UUID → `422 validation_error` (`{"detail":{"error":"validation_error","message":"Invalid plan id"}}`); valid unknown UUID → `404 plan_not_found`.
- `GET /api/evening/events`: returns only `status == "scheduled"` and future (`starts_at > now` in Asia/Vladivostok) events available for `POST /api/evening/plans`; `rescheduled/cancelled/past` excluded. Saved plans remain readable and `current` is still revalidated (including `rescheduled`) without changing snapshot.
- Regression tests added (2): `test_F1_events_excludes_rescheduled_future_event`, `test_F2_get_plan_invalid_uuid_returns_422_validation_error`. `test_sql_parameterized_safety` adjusted to expect `422 validation_error` for invalid-UUID injection-like plan id.

### Commands and exact results

1. Demo fixture prepare:
```sh
cd /home/recara/farposton-evening
PYTHONPATH=/home/recara/farposton-evening /home/recara/farposton/.venv/bin/python demo/local.py prepare
```
Result: `FileExistsError: Refusing to overwrite /home/recara/farposton-evening/.demo/forecast; reuse with run/check` (existing `.demo/forecast` preserved; no overwrite).

2. Evening-focused tests:
```sh
cd /home/recara/farposton-evening
PYTHONPATH=/home/recara/farposton-evening /home/recara/farposton/.venv/bin/python -m pytest -q tests/test_evening_mvp.py tests/test_evening_docker_config_F8.py
```
Result: **45 passed, 1 warning in 0.83s** (`starlette.testclient`/anyio `DeprecationWarning`).

3. Full project tests:
```sh
cd /home/recara/farposton-evening
make test VENV=/home/recara/farposton/.venv/bin/python
```
Result: pytest **440 passed, 6 skipped, 1 warning in 23.28s**; `node tests/test_dom_helper.cjs` → `DOM helper regression checks passed`. Skips unchanged/pre-existing: `test_docs_and_invariants.py` (3, `facts.json` not built) and `test_shelf.py` (3, `facts.json` not built).

4. Docker rebuild/restart (current compose):
```sh
cd /home/recara/farposton-evening
sudo -n docker compose -f compose.evening.yaml up -d --build
```
Result: image rebuilt (`farposton-evening-evening:latest`) and container `farposton-evening-evening-1` recreated/started on `127.0.0.1:18447->18447/tcp`; health became `healthy`.

5. Real HTTP checks after rebuild:
- `GET /api/evening/events` → 3 items, all `status=scheduled`:
  - `04728bad-edd6-595b-98c2-35e47ebe7ae4` `2026-11-14T19:30:00+10:00`
  - `d60966dc-274b-5703-ab04-557ec87aba27` `2026-11-15T18:45:00+10:00`
  - `0df439d7-64b5-5a0c-98b5-16781244c6ba` `2026-11-16T20:00:00+10:00`
- `POST /api/evening/plans` (`event_id=04728bad-edd6-595b-98c2-35e47ebe7ae4`, `date=2026-11-14`, `17:00-18:00`, `preferred_features=["dog"]`, `max_distance_m=3000`) → `201`, plan `feceb528-b0f0-4f77-ace3-ec45de1a1480`, `primary=place-01`, `alternative=place-03`, `current.event_status=scheduled`.
- `GET /api/evening/plans/feceb528-b0f0-4f77-ace3-ec45de1a1480` → `200`, snapshot+`current` present, `event_status=scheduled`.
- `GET /api/evening/plans/not-a-uuid` → `422`, `{"detail":{"error":"validation_error","message":"Invalid plan id"}}`.
- `GET /api/evening/plans/00000000-0000-4000-8000-000000000000` → `404`, `{"detail":{"error":"plan_not_found","message":"План не найден"}}`.

6. Restart with volume persistence:
```sh
sudo -n docker compose -f compose.evening.yaml restart
```
Result: container healthy after restart; `GET /api/evening/plans/feceb528-b0f0-4f77-ace3-ec45de1a1480` → `200` (`scheduled`, `place-01`); events still all scheduled (3); invalid UUID still `422`, valid unknown still `404`.

### Notes
- Container remains running on `127.0.0.1:18447` after verification (not stopped/torn down). No commit/push performed. No 2ГИС/external API calls. Pre-existing unrelated dirty/untracked worktree items were not modified.
