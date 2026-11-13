"""Isolated synthetic fixture, offline local server, and bounded model rehearsal."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tarfile
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".demo" / "forecast"
ARCHIVE = PROJECT / "demo" / "forecast-demo-data.tar.gz"
ARCHIVE_SHA256 = "f3bc8d83b430c0bc44d7d84f35e1d87511d32da04b4fad32dbd03ded1ab15408"
FIXTURE_DATE = date(2026, 9, 29)


def prepare() -> None:
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("Synthetic archive checksum mismatch")
    if ROOT.exists():
        raise FileExistsError(f"Refusing to overwrite {ROOT}; reuse with run/check")
    ROOT.parent.mkdir(exist_ok=True)
    with tarfile.open(ARCHIVE) as archive:
        for member in archive.getmembers():
            inside = member.name == "forecast" or member.name.startswith("forecast/")
            if not inside or not (member.isfile() or member.isdir()):
                raise ValueError(f"Unexpected archive entry: {member.name}")
        archive.extractall(ROOT.parent, filter="data")
    print(f"Verified synthetic fixture extracted to {ROOT}")


def environment(llm_model: str | None = None) -> dict[str, str]:
    if not (ROOT / "prognoz_web.duckdb").is_file():
        raise FileNotFoundError("Run make demo-prepare first")
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": f"{PROJECT}:{PROJECT / 'src'}",
        "DEPLOYMENT_PROFILE": "local", "WEB_DEMO_NO_AUTH": "1",
        "WEB_DEMO_DATE": FIXTURE_DATE.isoformat(),
        "WEB_HOST": "127.0.0.1", "WEB_PORT": "8445",
        "WEB_USERS_JSON": "[]", "WEB_SECRET_KEY": secrets.token_urlsafe(32),
        "SESSION_SECURE_COOKIE": "0", "PUBLIC_HTTPS": "0",
        "TLS_CERT": "", "TLS_KEY": "", "ALLOWED_IPS": "127.0.0.1,::1",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver", "TRUSTED_PROXY_IPS": "",
        "LLM_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": "",
        "LLM_ALLOW_CLIENT_DATA": "0", "LLM_PROXY_URL": "",
        "REPORT_LLM_AGGREGATES_ALLOWED": "0", "TELEGRAM_ENABLED": "0",
        "PROGNOZ_1C_INGEST": "0", "PROGNOZ_1C_WRITE": "0",
        "PROGNOZ_DB_PATH": str(ROOT / "prognoz.duckdb"),
        "WEB_DB_SNAPSHOT": str(ROOT / "prognoz_web.duckdb"),
        "APP_REFERENCE_ROOT": str(PROJECT),
        "APP_OUTPUT_ROOT": str(ROOT / "output"),
        "WEB_OUTPUT_ROOT": str(ROOT / "output"),
        "WEB_FORECAST_LOG_DIR": str(ROOT / "output" / "forecast_log"),
        "WEB_RELEASE_ROOT": str(ROOT / "output" / "releases"),
        "WEB_EDITS_DB": str(ROOT / "web_edits.sqlite"),
        "CLIENT_DATA_ROOT": "", "WEB_EXPORT_TMP_DIR": str(ROOT / "exports"),
        "PROGNOZ_CONFIG_DIR": str(ROOT / "config"),
        "PROGNOZ_ASSORTMENT_POLICY": str(ROOT / "config" / "assortment_policy.json"),
        "PROGNOZ_ML_MODEL_DIR": str(ROOT / "models"),
        "PROGNOZ_STACKED_GUARD_BUNDLE": str(ROOT / "models" / "stacked_guard" / "lag_3"),
        "PROGNOZ_STACKED_GUARD_MANIFEST_SHA256": "",
        "PROGNOZ_ENGINE": "ml", "PROGNOZ_DEMAND_RECOVERY": "0",
        "PROGNOZ_ZERO_RULE": "1", "PROGNOZ_SLOW_RULE": "1",
        "PROGNOZ_DATA_DIR": str(ROOT / "raw-unused"),
        "PROGNOZ_EXTERNAL_DIR": str(ROOT / "external"),
        "WEEKLY_DEMO_DATA_DIR": str(PROJECT / "demo" / "weekly"),
        "LOG_FILE": str(ROOT / "logs" / "web.log"),
        "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
    })
    if llm_model is not None:
        if llm_model not in {"glm-5.3", "qwen3.8-max"}:
            raise ValueError("Unsupported demo model")
        key_file = PROJECT.parent / ".secrets" / "opencode.key"
        key = key_file.read_text().strip()
        if not key:
            raise ValueError("Empty OpenCode key file")
        env.update(LLM_API_KEY=key, LLM_BASE_URL="https://opencode.ai/zen/go/v1",
                   LLM_MODEL=llm_model, LLM_ALLOW_CLIENT_DATA="1")
    return env


def offline_calendar(cache: Path | None = None) -> None:
    """Demo-only weekday calendar: fixed holiday factors remain in core code."""
    import pandas as pd

    cache = cache or ROOT / "external" / "calendar_ru.parquet"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        days = pd.date_range("2024-01-01", "2027-12-31")
        pd.DataFrame({"date": days.date, "weekday": days.weekday,
                      "is_dayoff": days.weekday >= 5,
                      "is_preholiday": False}).to_parquet(cache, index=False)


def model_rehearsal() -> None:
    import joblib
    import pandas as pd
    from prognoz.predict import ml_core, catboost_core, stacked_guard_core

    shelf = pd.read_parquet(ROOT / "output" / "shelf_rolling.parquet")
    stamp_path = ROOT / "models" / "stamp.json"
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    cutoff = date.fromisoformat(stamp["train_cutoff"])
    target = cutoff + timedelta(days=3)
    shelf = shelf[pd.to_datetime(shelf["trade_day"]).dt.date <= cutoff].copy()
    model_path = ROOT / "models" / "model.joblib"
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    model = joblib.load(model_path)
    panel, _ = ml_core.add_features(ml_core.build_panel(shelf, target), 3)
    hgb = ml_core.predict_rows(model, stamp, panel, target)
    if hgb.empty or hgb["mu_ml"].isna().any():
        raise RuntimeError("Packaged HGB inference failed")
    cat_cfg = catboost_core.CatCfg(iterations=100)
    cat, meta = catboost_core.catboost_forecast(None, target, cfg=cat_cfg, shelf_df=shelf)
    if cat.empty or cat["mu_ml"].isna().any() or meta["stamp"]["train_cutoff"] != cutoff.isoformat():
        raise RuntimeError("Synthetic CatBoost inference/cutoff failed")
    from prognoz.predict.daily import build, to_excel
    from prognoz.predict.demand import BuildCfg
    from _db import connect_reader
    causal_path = ROOT / "causal-shelf.parquet"
    shelf.to_parquet(causal_path, index=False)
    con = connect_reader()
    try:
        order = build(target, con, cfg=BuildCfg(engine="ml", shelf_path=causal_path),
                      known_until=cutoff)
    finally:
        con.close()
    if order.empty or set(order["engine"]) != {"ml"}:
        raise RuntimeError("HGB daily order fell back; do not present it as ML")
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != model_hash:
        raise RuntimeError("Packaged HGB unexpectedly refitted")
    to_excel(order, target, ROOT / "hgb-order-demo.xlsx", [
        "СИНТЕТИЧЕСКОЕ ЛОКАЛЬНОЕ ДЕМО — не рабочая заявка и не выпуск ансамбля",
        f"HGB без переобучения; граница данных {cutoff}; цель {target}"])
    cat.to_excel(ROOT / "catboost-model-demo.xlsx", index=False)
    try:
        stacked_guard_core.load_bundle(target=target)
    except stacked_guard_core.BundleError as exc:
        stacked = {"ready": False, "reason": str(exc)}
    else:
        stacked = {"ready": True}
    result = {
        "synthetic_only": True, "target": target.isoformat(),
        "hgb": {"loaded_without_refit": True, "rows": len(hgb),
                "daily_order_rows": len(order), "daily_order_units": int(order["Прогноз, шт"].sum()),
                "model_sha256": model_hash, "stamp": stamp},
        "catboost": {"rows": len(cat), "demo_iterations": 100,
                     "refitted": meta["refitted"], "stamp": meta["stamp"]},
        "stacked_guard": stacked,
    }
    (ROOT / "model-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    hgb.to_parquet(ROOT / "hgb-inference.parquet", index=False)
    cat.to_parquet(ROOT / "catboost-inference.parquet", index=False)
    print(json.dumps({"target": target.isoformat(), "hgb_rows": len(hgb),
                      "hgb_daily_order_rows": len(order),
                      "catboost_rows": len(cat), "catboost_refitted": meta["refitted"],
                      "stacked_guard": stacked}, ensure_ascii=False))


def smoke(llm_enabled: bool = False) -> None:
    """Check the running loopback app, restoring the edit after exporting it."""
    import io
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from openpyxl import load_workbook

    def request(path, body=None, expected=200):
        encoded = json.dumps(body).encode() if body is not None else None
        req = Request("http://127.0.0.1:8445" + path, encoded,
                      {"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=60) as response:
                status, content = response.status, response.read()
        except HTTPError as exc:
            status, content = exc.code, exc.read()
        assert status == expected, (path, status, content[:200])
        return content

    def payload(path, body=None, expected=200):
        return json.loads(request(path, body, expected))

    pages = ["/", "/accuracy", "/analytics", "/comparison", "/reports", "/chat", "/weekly-demo"]
    for page in pages:
        assert b"<html" in request(page).lower(), page
    meta = payload("/api/meta")
    assert meta["demo_date"] == FIXTURE_DATE.isoformat() and len(meta["points"]) == 3
    target = meta["operational_date"]
    order_path = "/api/order?date=" + target
    order = payload(order_path)
    assert order["meta"]["editable"] and order["rows"]
    row = order["rows"][0]
    body = {"target_date": target, "release_id": order["meta"]["release_id"],
            "batch_id": "demo-smoke:" + secrets.token_hex(12), "edits": [{
                "point_key": row["point_key"], "sku_key": row["sku_key"],
                "qty_user": row["qty"] + 1, "expected_revision": row["edit_revision"]}]}
    saved = payload("/api/edits/bulk", body)
    assert saved["ok"] and payload("/api/edits/bulk", body)["replayed"]
    try:
        edited = payload(order_path)
        actual = next(r for r in edited["rows"] if (r["point_key"], r["sku_key"]) ==
                      (row["point_key"], row["sku_key"]))
        assert actual["qty_user"] == row["qty"] + 1
        excel = request("/api/order/export?date=" + target)
        book = load_workbook(io.BytesIO(excel), data_only=True)
        table = list(book["Все точки"].values)
        columns = {name: i for i, name in enumerate(table[0])}
        match = next(r for r in table[1:] if r[columns["Точка"]] == row["point"] and
                     r[columns["Позиция"]] == row["name"])
        assert match[columns["Итог, шт"]] == row["qty"] + 1
        (ROOT / "smoke-export.xlsx").write_bytes(excel)
        stale = {**body, "batch_id": "demo-stale:" + secrets.token_hex(12)}
        payload("/api/edits/bulk", stale, expected=409)
    finally:
        current = payload(order_path)
        revision = next(r["edit_revision"] for r in current["rows"] if
                        (r["point_key"], r["sku_key"]) == (row["point_key"], row["sku_key"]))
        body["batch_id"] = "demo-restore:" + secrets.token_hex(12)
        body["edits"][0].update(qty_user=row["qty_user"], expected_revision=revision)
        assert payload("/api/edits/bulk", body)["ok"]
    assert payload(order_path)["meta"]["release_id"] == order["meta"]["release_id"]
    assert not payload("/api/order/push1c/status")["enabled"]
    assert payload("/api/chat/status")["llm_ok"] == llm_enabled
    weekly = payload("/api/weekly-demo")
    assert weekly["backtest_weeks"] == 4 and len(weekly["rows"]) == 18
    for item in weekly["rows"]:
        qty = item["draft_qty"]
        assert qty >= 0 and (not qty or qty >= item["min_order"] and qty % item["pack_size"] == 0)
    for path in ["/api/analytics/status", "/api/analytics/sales", "/api/accuracy",
                 "/api/comparison", "/api/reports"]:
        payload(path)
    result = {"pages": pages, "order_rows": len(order["rows"]),
              "edit_export_replay_conflict_restore": True, "llm_enabled": llm_enabled, "one_c_disabled": True,
              "weekly_rows": len(weekly["rows"]), "weekly_wape_synthetic": weekly["wape"],
              "health": payload("/health")}
    (ROOT / "smoke-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check", "run", "ml", "smoke", "test"])
    parser.add_argument("--llm", action="store_true", help="Enable OpenCode on synthetic demo data only")
    parser.add_argument("--model", choices=["glm-5.3", "qwen3.8-max"], default="glm-5.3")
    args = parser.parse_args()
    if args.llm and args.command not in {"run", "smoke"}:
        parser.error("--llm is only available for run/smoke")
    os.chdir(PROJECT)
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12: .venv/bin/python demo/local.py")
    if args.command == "prepare":
        prepare()
        return
    if args.command == "test":
        calendar_dir = PROJECT / ".demo" / "test-external"
        os.environ.update(PYTHONPATH=f"{PROJECT}:{PROJECT / 'src'}",
                          PROGNOZ_EXTERNAL_DIR=str(calendar_dir), OMP_NUM_THREADS="2")
        offline_calendar(calendar_dir / "calendar_ru.parquet")
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "tests"], check=True)
        return
    os.environ.update(environment(args.model if args.llm else None))
    sys.path.insert(0, str(PROJECT))
    sys.path.insert(0, str(PROJECT / "src"))
    offline_calendar()
    if args.command == "check":
        subprocess.run([sys.executable, "tools/check_env.py"], check=True)
        subprocess.run([sys.executable, "demo/check_analytics.py", str(ROOT)], check=True)
        subprocess.run([sys.executable, "demo/weekly/validate.py"], check=True)
        from demo.status import publish
        publish(ROOT / "prognoz_web.duckdb", ROOT / "completeness-check",
                FIXTURE_DATE - timedelta(days=1))
        import duckdb
        with duckdb.connect(str(ROOT / "prognoz_web.duckdb"), read_only=True) as con:
            points = con.execute("SELECT count(*) FROM mart.dim_point").fetchone()[0]
            skus = con.execute("SELECT count(*) FROM mart.dim_sku").fetchone()[0]
            dates = con.execute("SELECT min(sale_date), max(sale_date), count(DISTINCT sale_date) FROM mart.fact_sales_daily").fetchone()
        assert (points, skus, dates[2]) == (3, 14, 485), (points, skus, dates)
        print(f"Synthetic source: {points} points, {skus} SKUs, {dates[2]} sales days ({dates[0]} … {dates[1]})")
    elif args.command == "ml":
        model_rehearsal()
    elif args.command == "smoke":
        smoke(args.llm)
    else:
        print(f"Synthetic fixture clock: {FIXTURE_DATE}; LLM: {args.model if args.llm else 'off'}; http://127.0.0.1:8445", flush=True)
        from web.app import main as serve
        serve()


if __name__ == "__main__":
    main()
