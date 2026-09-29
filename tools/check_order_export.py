#!/usr/bin/env python3
"""Проверяет, что Excel менеджера содержит ровно одну разрешённую точку."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import load_workbook


def inspect_export(path: Path, expected_point: str) -> dict[str, object]:
    """Возвращает краткую сводку или поднимает ValueError при утечке точки."""
    expected = str(expected_point).strip()
    if not expected:
        raise ValueError("Не задана ожидаемая точка")

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Все точки" not in workbook.sheetnames:
            raise ValueError("В книге нет листа «Все точки»")
        sheet = workbook["Все точки"]
        headers = [str(cell.value or "").strip() for cell in next(sheet.iter_rows())]
        try:
            point_column = headers.index("Точка") + 1
        except ValueError as exc:
            raise ValueError("На листе «Все точки» нет колонки «Точка»") from exc

        points = sorted(
            {
                str(row[0].value).strip()
                for row in sheet.iter_rows(
                    min_row=2,
                    min_col=point_column,
                    max_col=point_column,
                )
                if row[0].value is not None and str(row[0].value).strip()
            }
        )
        if points != [expected]:
            raise ValueError(
                f"В данных Excel точки {points!r}, ожидалась только {expected!r}"
            )

        point_sheets = sorted(
            name for name in workbook.sheetnames if name not in {"О расчёте", "Все точки"}
        )
        expected_sheet = expected[:31]
        if point_sheets != [expected_sheet]:
            raise ValueError(
                f"Листы точек {point_sheets!r}, ожидался только {expected_sheet!r}"
            )
        return {
            "ok": True,
            "point": expected,
            "rows": max(sheet.max_row - 1, 0),
            "point_sheets": point_sheets,
        }
    finally:
        workbook.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", required=True, type=Path)
    parser.add_argument("--expected-point", required=True)
    args = parser.parse_args()
    try:
        result = inspect_export(args.xlsx, args.expected_point)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
