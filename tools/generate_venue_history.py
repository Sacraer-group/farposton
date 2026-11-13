"""Create a separate, reproducible synthetic history for every local venue."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from web.places import catalogue
from web.places_history import DEFAULT_DB, TIMEZONE, calendar_month, create_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DB)
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(ZoneInfo(TIMEZONE)).date())
    args = parser.parse_args()
    start, end = calendar_month(args.as_of)
    print(json.dumps(create_history(args.output, catalogue(), start, end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
