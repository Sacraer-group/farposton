"""Extend a synthetic history backwards and verify the original overlap exactly."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from urllib.parse import quote

from web.places import catalogue
from web.places_history import create_history


def readonly(path: Path) -> str:
    return "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"


def extend(source: Path, output: Path, start: date, places: dict) -> dict:
    with closing(sqlite3.connect(readonly(source), uri=True)) as conn:
        meta = {key: json.loads(value) for key, value in conn.execute("SELECT key, value FROM metadata")}
    if meta.get("synthetic") is not True or meta.get("unit") != "synthetic_index_0_100":
        raise ValueError("Исходная история должна быть явно синтетической")
    old_start, end = date.fromisoformat(meta["start_date"]), date.fromisoformat(meta["end_date"])
    if start >= old_start:
        raise ValueError("Начало расширенной истории должно быть раньше исходной")
    generated = create_history(output, places, start, end)
    with closing(sqlite3.connect(readonly(output), uri=True)) as conn:
        conn.execute("ATTACH DATABASE ? AS original", (readonly(source),))
        original_rows = conn.execute("SELECT COUNT(*) FROM original.daily").fetchone()[0]
        matched = conn.execute("SELECT COUNT(*) FROM original.daily old JOIN main.daily new "
                               "ON new.venue_id=old.venue_id AND new.day=old.day "
                               "WHERE new.hourly=old.hourly").fetchone()[0]
    if generated["venues"] != meta["venues"] or matched != original_rows:
        raise ValueError(f"Исходный месяц изменился: совпало {matched} из {original_rows} строк; "
                         f"проверочная база оставлена отдельно: {output}")
    return {"source": str(source), "output": str(output), "synthetic": True,
            "start": start.isoformat(), "end": end.isoformat(),
            "venues": generated["venues"], "original_rows": original_rows,
            "identical_original_rows": matched}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    print(json.dumps(extend(args.source, args.output, args.start, catalogue()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
