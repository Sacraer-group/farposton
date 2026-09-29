"""Check that a synthetic forecast data tree populates the existing analytics.

Usage: python demo/check_analytics.py /absolute/path/to/forecast
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main(root: Path) -> None:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    os.environ.update({
        "WEB_DB_SNAPSHOT": str(root / "prognoz_web.duckdb"),
        "APP_OUTPUT_ROOT": str(root / "output"),
        "WEB_OUTPUT_ROOT": str(root / "output"),
        "PROGNOZ_CONFIG_DIR": str(root / "config"),
    })
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import duckdb
    import pandas as pd
    from web import analytics  # imports environment-backed paths

    with duckdb.connect(str(root / "prognoz_web.duckdb"), read_only=True) as con:
        for table in ("dim_point", "dim_sku"):
            bad = con.execute(
                f"SELECT count(*) FROM mart.{table} "
                "WHERE NOT starts_with(name, 'ДЕМО — ')").fetchone()[0]
            if bad:
                raise RuntimeError(f"Non-demo names in mart.{table}: {bad}")

    checks = {
        "sales": analytics.sales_series()["rows"],
        "hours": analytics.hours_profile()["rows"],
        "weekdays": analytics.weekday_profile()["rows"],
        "writeoffs": analytics.writeoffs()["days"],
        "deficit": analytics.shelf_top()["deficit"],
        "sellout": analytics.sellout_speed()["top"],
        "sku leaders": analytics.top_sku()["rows"],
    }
    missing = [name for name, rows in checks.items() if not rows]
    if not analytics.yoy(preset="month")["available"]:
        missing.append("year over year")
    if missing:
        raise RuntimeError("Empty analytics: " + ", ".join(missing))
    print("Analytics populated: " + ", ".join(checks) + ", year over year")

    duel = sorted((root / "output" / "duel").glob("*.parquet"))
    shadow = sorted((root / "output" / "shadow").glob("*.parquet"))
    print(f"Simulated causal engine comparison: {len(duel)} days; "
          f"recorded forecast evaluation: {len(shadow)} days")
    if len(duel) < 30:
        raise RuntimeError("Fewer than 30 causal comparison days")
    for path in duel:
        frame = pd.read_parquet(path, columns=["date", "model_cutoff"])
        if frame.empty or (pd.to_datetime(frame["model_cutoff"]).dt.date
                           > pd.to_datetime(frame["date"]).dt.date
                           - pd.Timedelta(days=3)).any():
            raise RuntimeError(f"Future-looking comparison: {path.name}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
        raise SystemExit("Usage: python demo/check_analytics.py /absolute/path/to/forecast")
    main(Path(sys.argv[1]))
