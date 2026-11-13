"""Check the completeness and basic business consistency of weekly demo CSVs."""
from __future__ import annotations

import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def main() -> None:
    history, planning = read("history.csv"), read("planning.csv")
    pairs = {(row["point_key"], row["sku_key"]) for row in planning}
    assert len(pairs) == len(planning) == 18
    assert len({point for point, _ in pairs}) == 3
    assert len({sku for _, sku in pairs}) == 6
    dates = [date(2026, 7, 7) + timedelta(days=day) for day in range(84)]
    assert dates[-1] == date(2026, 9, 28)
    assert len(history) == len(dates) * len(pairs)
    counts = Counter()
    names: dict[str, str] = {}
    lost_sales = 0
    for row in history:
        day = date.fromisoformat(row["date"])
        pair = row["point_key"], row["sku_key"]
        assert day in dates and pair in pairs
        sold, demand = int(row["qty_sold"]), int(row["qty_demand"])
        assert 0 <= sold <= demand
        assert row["sku_key"] not in names or names[row["sku_key"]] == row["sku_name"]
        names[row["sku_key"]] = row["sku_name"]
        lost_sales += demand - sold
        counts[day, pair] += 1
    assert all(counts[day, pair] == 1 for day in dates for pair in pairs)
    for row in planning:
        values = {field: int(row[field]) for field in
                  ("stock_on_hand", "incoming_qty", "safety_stock", "pack_size",
                   "min_order", "shelf_life_days", "lead_time_days")}
        assert min(values.values()) >= 0
        assert values["pack_size"] > 0 and values["shelf_life_days"] > 0
        assert values["lead_time_days"] <= values["shelf_life_days"]
        assert values["min_order"] % values["pack_size"] == 0
        assert values["incoming_qty"] % values["pack_size"] == 0
    assert lost_sales > 0
    print(f"ok: {len(dates)} days, {len(pairs)} point/SKU pairs, {len(history)} history rows, {lost_sales} simulated lost units")


if __name__ == "__main__":
    main()
