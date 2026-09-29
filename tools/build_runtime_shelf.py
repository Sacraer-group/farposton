"""Build the causal rolling shelf used by nightly inference and model refit."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "analysis" / "etl"))

from _db import connect_reader  # noqa: E402
from prognoz.predict.demand import ROLLING_CACHE, load_shelf  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    con = connect_reader()
    try:
        frame = load_shelf(con, args.as_of)
    finally:
        con.close()
    if frame.empty:
        raise RuntimeError("Rolling shelf is empty")
    maximum = frame["trade_day"].max()
    print(json.dumps({
        "status": "ok",
        "path": str(ROLLING_CACHE.resolve()),
        "rows": int(len(frame)),
        "max_trade_day": str(maximum)[:10],
        "as_of": args.as_of.isoformat(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
