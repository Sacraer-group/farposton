"""Fetch long-term rental flats in Vladivostok from the official Metrapi API.

Usage: METRAPI_KEY=... python scripts/fetch_metrapi.py [--limit 50] [--out flats/demo/metrapi_vladivostok.json]
"""
import argparse
import json
import sys
from pathlib import Path

import requests

from flats import config
from flats.metrapi import API_URL, VLADIVOSTOK_RENT, normalize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--out", type=Path, default=Path("flats/demo/metrapi_vladivostok.json"))
    args = parser.parse_args()

    response = requests.get(
        API_URL,
        params={**VLADIVOSTOK_RENT, "limit": args.limit},
        headers={"X-Api-Key": config.metrapi_key()},
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Metrapi error {response.status_code}: {response.text[:300]}", file=sys.stderr)
        return 1
    rows = [normalize(item) for item in response.json()["items"]]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"saved {len(rows)} offers to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
