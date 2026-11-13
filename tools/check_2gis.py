"""Проверка доступности полей загруженности 2ГИС без сохранения ответов."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

KEY_PATH = Path.home() / ".config" / "farposton" / "2gis_api_key"
ENDPOINT = "https://catalog.api.2gis.com/3.0/items"


def summarise(payload: dict) -> dict:
    meta = payload.get("meta", {})
    if meta.get("code") != 200:
        return {"ok": False, "api_code": meta.get("code"),
                "error_type": (meta.get("error") or {}).get("type", "unknown")}
    items = (payload.get("result") or {}).get("items") or []
    present = [item for item in items if item.get("congestion") is not None]
    dynamic = [item for item in items if item.get("has_dynamic_congestion") is True]
    profiles = [item["congestion"] for item in present if isinstance(item["congestion"], dict)]
    buckets = {str(bucket) for profile in profiles for bucket, days in profile.items()
               if str(bucket).isdigit() and isinstance(days, dict)}
    days = {day for profile in profiles for bucket, value in profile.items()
            if str(bucket).isdigit() and isinstance(value, dict)
            for day, hours in value.items() if isinstance(hours, list)}
    return {
        "ok": True, "returned": len(items),
        "congestion_non_null": len(present),
        "congestion_types": dict(Counter(type(item["congestion"]).__name__ for item in present)),
        "dynamic_true": len(dynamic),
        "dynamic_false": sum(item.get("has_dynamic_congestion") is False for item in items),
        "dynamic_missing_or_null": sum(item.get("has_dynamic_congestion") is None for item in items),
        "observed_bucket_labels": sorted(buckets, key=int),
        "observed_day_codes": sorted(days),
        "sample": [{"name": item.get("name"),
                    "congestion_present": item.get("congestion") is not None,
                    "dynamic": item.get("has_dynamic_congestion")} for item in items],
    }


def main() -> int:
    key = os.environ.get("TWOGIS_API_KEY", "").strip()
    if not key and KEY_PATH.is_file():
        key = KEY_PATH.read_text(encoding="utf-8").strip()
    if not key:
        print("Ключ 2ГИС не найден. Добавьте его в защищённый файл ~/.config/farposton/2gis_api_key.", file=sys.stderr)
        return 2
    query = urlencode({"q": "кафе", "point": "131.8855,43.1155", "radius": 3000,
                       "page_size": 10, "fields": "items.congestion,items.has_dynamic_congestion",
                       "key": key})
    try:
        with urlopen(f"{ENDPOINT}?{query}", timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        print(json.dumps({"ok": False, "http_status": exc.code}), file=sys.stderr)
        return 1
    except (URLError, TimeoutError, ValueError, OSError):
        print("Ошибка сети или некорректный ответ 2ГИС; ключ не выведен.", file=sys.stderr)
        return 1
    result = summarise(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
