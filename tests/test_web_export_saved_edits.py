from __future__ import annotations

from openpyxl import load_workbook
import pandas as pd

from web import export


def test_manager_excel_contains_saved_effective_value_and_no_network_edit_count(monkeypatch):
    release_id = "sha256:" + "a" * 64
    frame = pd.DataFrame([
        {"_point_key": "point-a", "_sku_key": "sku-1", "Точка": "Точка А",
         "Позиция": "Позиция 1", "Прогноз, шт": 8, "engine": "catboost"},
        {"_point_key": "point-b", "_sku_key": "sku-2", "Точка": "Точка Б",
         "Позиция": "Позиция 2", "Прогноз, шт": 9, "engine": "catboost"},
    ])
    monkeypatch.setattr(export, "release_fingerprint", lambda _date: release_id)
    monkeypatch.setattr(
        export, "_load_log_for_release", lambda _date, _release: frame)
    monkeypatch.setattr(export.edits, "get_effective", lambda _date, _release: {
        ("point-a", "sku-1"): 12.0,
        ("point-b", "sku-2"): 15.0,
    })

    path = export.build_export("2099-01-02", "point-a")
    try:
        workbook = load_workbook(path, data_only=True)
        info = [cell.value for cell in workbook["О расчёте"]["A"]]
        assert "Действующих правок: 1" in info
        sheet = workbook["Все точки"]
        headers = [cell.value for cell in sheet[1]]
        values = {headers[index]: sheet.cell(2, index + 1).value
                  for index in range(len(headers))}
        assert sheet.max_row == 2
        assert values["Точка"] == "Точка А"
        assert values["Правка, шт"] == 12
        assert values["Итог, шт"] == 12
    finally:
        path.unlink(missing_ok=True)
