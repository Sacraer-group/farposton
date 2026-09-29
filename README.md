# Forecast

This directory is an independent private Git repository. `main` contains the integrated neutral demo; `interface-polish` records the UI work.

- Application: `web/`; start with `python -m web.app` after installing `web/requirements.txt` and configuring a private `.env`.
- Existing demo runtime: `/opt/product/forecast` on the local demo VM. Data remain separately under `/opt/product/data/forecast`.
- Portable synthetic fixture and generators: `demo/`. Shared product guides are supplied separately in the desktop bundle’s `passport` folder.
- Secrets, virtual environments and runtime data are excluded from Git.

## UI maintenance, 2026-09-29

Chart tick bounds, responsive dates/header, demo navigation, request races and dark-theme contrast were corrected. Forecast calculations, inventory rules and synthetic source data were not changed.

Run `node tests/test_dom_helper.cjs` for the shared DOM regression check. Chart checks are in `tests/test_web_chart_ux.py`.

Verification used a local, read-only copy of captured synthetic API responses in macOS WebKit at 360px and 1280px, both themes, plus focused source checks. This is not a full Safari or cross-browser certification. Live HTTPS metadata and populated data endpoints were checked after deployment.

## Synthetic demo and analytics audit

`demo/forecast-demo-data.tar.gz` is a fictional shawarma data snapshot (2026-09-29), with three stores and fourteen products. It includes 485 days of source facts, a newer store with 120 days, two new products with 17 days, stockout estimates, markdowns, writeoffs, and a precomputed shelf. No customer export or original brand is used. The archive contains 30 days of **simulated causal engine comparison** (2026-08-30 to 2026-09-28), replayed with `prognoz.predict.duel`: historical ML models are trained in a sandbox and each cutoff is checked before writing a day. The recorded forecast evaluation still spans only seven days, so its 30-day accuracy view is not a 30-day result. Do not copy a recent model backward or relabel simulated rows as production accuracy.

The existing analytics page covers sales, year-over-year trends, hourly and weekday profiles, writeoffs/markdowns, deficit, sellout speed, and SKU leaders. These sections use the snapshot plus `shelf_rolling.parquet`; they do not need new business formulas. `demo/check_analytics.py` checks that the fixture populates each section and reports source/evaluation coverage.

To inspect the portable fixture locally (Python 3.12, `web/requirements.txt` installed):

```sh
mkdir -p /tmp/forecast-demo-check
shasum -a 256 demo/forecast-demo-data.tar.gz
tar -xzf demo/forecast-demo-data.tar.gz -C /tmp/forecast-demo-check
python demo/check_analytics.py /tmp/forecast-demo-check/forecast
```

Expected archive SHA-256: `f3bc8d83b430c0bc44d7d84f35e1d87511d32da04b4fad32dbd03ded1ab15408`.

To create a new synthetic source database, use `python demo/generate.py /absolute/path/prognoz.duckdb --as-of YYYY-MM-DD`; the target must not exist. Build a matching read-only web snapshot and shelf with the existing project tools. `demo/status.py` publishes synthetic source completeness. Keep the selected `--as-of` date, model cutoff, forecast issue date, and display labels together when rebuilding the served demo.

To extend causal comparison on an extracted copy of the fixture, set `PROGNOZ_DB_PATH`, `APP_OUTPUT_ROOT` and `PROGNOZ_CONFIG_DIR` to that copy; then run `PYTHONPATH=src python -m prognoz.predict.duel --since=YYYY-MM-DD --until=YYYY-MM-DD --db=snapshot`. Its `--db=snapshot` mode reads `prognoz_web.duckdb` beside the configured source DB and writes only to the configured output directory. Verify with `demo/check_analytics.py` before packaging or serving it.

For a server refresh, extract into a **new staging directory**, validate there, stop the web service, then replace only the source DB, web snapshot, generated output and demo config after backing up the existing directory. Preserve the server's `.env`, `web_edits.sqlite`, users, logs and any operator changes. Never extract the archive directly over the live data tree. The archive deliberately excludes `web_edits.sqlite`.

## Continuous integration

GitHub Actions runs Python 3.12 regression checks and the dependency-free DOM check on the organization's `self-hosted`, `Linux`, `X64` runners. Actions are pinned; the workflow has read-only repository permissions and does not run code from fork pull requests. Deployment to the local VM is separate from CI. `actions/checkout` v5 is retained because v6's credential include paths do not match the runners' symlinked work directories.
