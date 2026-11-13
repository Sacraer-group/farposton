# Docker: Evening MVP

Lightweight container for the fictional "Ready Evening" MVP. It serves only the evening routes/UI and uses runtime synthetic seed generated inside the state volume on first start (no external data, no host `.demo`).

## Build and run

```sh
sudo -n docker compose -f compose.evening.yaml up -d --build
```

Service: `http://127.0.0.1:18447` (bound to loopback on host).

- UI: `http://127.0.0.1:18447/evening`
- Plan page: `http://127.0.0.1:18447/evening/p/<id>`
- Health/API: `http://127.0.0.1:18447/api/evening/events`

## Checks

```sh
sudo -n docker compose -f compose.evening.yaml ps
curl -fsS http://127.0.0.1:18447/api/evening/events
```

Create/read plan (local demo, no auth):

```sh
curl -fsS -X POST http://127.0.0.1:18447/api/evening/plans \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"<uuid>","date":"YYYY-MM-DD","meal_start":"17:00","meal_end":"18:00","required_features":[],"preferred_features":[],"max_distance_m":3000}'
curl -fsS http://127.0.0.1:18447/api/evening/plans/<plan_id>
```

Restart keeps state volume (mounted at `/app/.demo/evening`):

```sh
sudo -n docker compose -f compose.evening.yaml restart
```

## Stop

```sh
sudo -n docker compose -f compose.evening.yaml down
```

State volume `evening_state` persists plans/seed unless removed explicitly with `-v`.
