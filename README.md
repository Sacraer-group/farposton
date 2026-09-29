# Forecast

This directory is an independent local Git repository. `main` contains the integrated neutral demo; `interface-polish` records the UI work. No remote is configured.

- Application: `web/`; start with `python -m web.app` after installing `web/requirements.txt` and configuring a private `.env`.
- Existing demo runtime: `/opt/product/forecast` on the local demo VM. Data remain separately under `/opt/product/data/forecast`.
- Local synthetic fixture archive and generators: `../demo-data/forecast/`. Shared product guides: `../docs/README.md`.
- Secrets, virtual environments and runtime data are excluded from Git.

## UI maintenance, 2026-09-29

Chart tick bounds, responsive dates/header, demo navigation, request races and dark-theme contrast were corrected. Forecast calculations, inventory rules and synthetic source data were not changed.

Run `node tests/test_dom_helper.cjs` for the shared DOM regression check. Chart checks are in `tests/test_web_chart_ux.py`.

Verification used a local, read-only copy of captured synthetic API responses in macOS WebKit at 360px and 1280px, both themes, plus focused source checks. This is not a full Safari or cross-browser certification. Live HTTPS metadata and populated data endpoints were checked after deployment. Analytics feature expansion and additional synthetic scenarios remain a separate next step.
