# -*- coding: utf-8 -*-
"""Excel «Заявка_<дата>_с_правками.xlsx»: выпущенная заявка + правки из веб-журнала.

Оформление повторяет src/prognoz/predict/daily.py::to_excel (лист «О расчёте»,
листы по точкам, «Все точки», заморозка шапки, ширины, заливки колонок). Сверху
две колонки: «Правка, шт» (что ввёл сотрудник) и «Итог, шт» (правка, а где её
нет — прогноз). Правленые строки залиты FDECEE (accent-soft бренда).

Файл экспорта собирается во временный каталог (tempfile), маршрут удаляет его
после отдачи; исходный immutable-выпуск при этом не меняется.
"""
from __future__ import annotations

from . import paths  # noqa: F401

import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import edits
from .order import _load_log_for_release, _s, release_fingerprint

# Технические колонки лога: в листы не идут (одно и то же значение 400+ раз),
# их место — на листе «О расчёте».
SERVICE_COLS = ("target_date", "max_sale_date", "version", "run_at",
                "input_hash", "engine", "model_cutoff")
EDIT_FILL_HEX = "FDECEE"   # accent-soft из app.css
ENGINE_LABELS = {
    "ml": "ML-ядро",
    "catboost": "ML-ядро · CatBoost",
    "stacked_guard": "ML-ядро · ансамбль",
    # резервный контур: ML-ядро не прошло ночную проверку либо выпуск до 12.08
    "baseline": "Резерв: базовая формула",
}


def xlsx_safe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy whose text cells cannot be interpreted as Excel formulas."""
    safe = frame.copy()

    def protect(value):
        if not isinstance(value, str):
            return value
        candidate = value.lstrip(" \t\r\n")
        if candidate.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    for column in safe.columns:
        safe[column] = safe[column].map(protect)
    return safe


def make_export_tempfile(*, prefix: str, suffix: str = ".xlsx") -> tuple[int, str]:
    """Create an export temp file inside the configured client data boundary.

    On the client server ``CLIENT_DATA_ROOT`` is ``D:\\forecast-data``. Keeping
    transient workbooks below it prevents business data from leaking into the
    service account's profile on drive C:. Local development without that
    setting retains the standard ``tempfile`` behaviour.
    """
    deployment_profile = os.getenv("DEPLOYMENT_PROFILE", "local").strip().lower() or "local"
    data_root_raw = os.getenv("CLIENT_DATA_ROOT", "").strip()
    export_root_raw = os.getenv("WEB_EXPORT_TMP_DIR", "").strip()
    data_root = Path(data_root_raw).resolve() if data_root_raw else None
    if deployment_profile == "client":
        expected_data_root = Path(r"D:\forecast-data").resolve()
        if (data_root is None or
                os.path.normcase(str(data_root)) != os.path.normcase(str(expected_data_root))):
            raise RuntimeError(
                "В клиентском профиле CLIENT_DATA_ROOT должен быть D:\\forecast-data")
    if export_root_raw:
        export_root = Path(export_root_raw).resolve()
    elif data_root is not None:
        export_root = (data_root / "tmp" / "exports").resolve()
    else:
        export_root = None
    if data_root is not None and export_root is not None:
        try:
            inside = (os.path.commonpath((str(export_root), str(data_root))).casefold()
                      == str(data_root).casefold())
        except ValueError:
            inside = False
        if not inside:
            raise RuntimeError("WEB_EXPORT_TMP_DIR должен находиться внутри CLIENT_DATA_ROOT")
    if export_root is not None:
        export_root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkstemp(
        prefix=prefix, suffix=suffix,
        dir=str(export_root) if export_root is not None else None)


def _engine_label(engine: str) -> str:
    normalized = str(engine or "").strip().lower()
    if not normalized:
        return ENGINE_LABELS["baseline"]
    return ENGINE_LABELS.get(normalized, f"движок {engine}")


def build_export(target_date: str, point_key: str | None = None) -> Path:
    """Собирает xlsx во временный файл и возвращает путь (удаляет вызывающий)."""
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

    release_id = release_fingerprint(target_date)
    df = _load_log_for_release(target_date, release_id).copy()
    if point_key is not None:
        if '_point_key' not in df.columns:
            raise ValueError('В заявке нет ключей точек')
        df = df[df['_point_key'].astype(str) == point_key].copy()
        if df.empty:
            raise ValueError('Точка не найдена в заявке')
    eff = edits.get_effective(target_date, release_id)

    pk = (df["_point_key"] if "_point_key" in df.columns
          else pd.Series([""] * len(df), index=df.index))
    sk = (df["_sku_key"] if "_sku_key" in df.columns
          else pd.Series([""] * len(df), index=df.index))
    keys = list(zip(pk.astype(str), sk.astype(str)))
    # Менеджерская выгрузка не должна раскрывать даже агрегированное число
    # правок других точек на листе «О расчёте».
    visible_keys = set(keys)
    eff = {key: qty_user for key, qty_user in eff.items() if key in visible_keys}

    qty = (df["Прогноз, шт"] if "Прогноз, шт" in df.columns
           else pd.Series([0] * len(df), index=df.index))
    edit_vals = [eff.get(k) for k in keys]
    total_vals = [e if e is not None else int(q) for e, q in zip(edit_vals, qty)]

    pos = (df.columns.get_loc("Прогноз, шт") + 1
           if "Прогноз, шт" in df.columns else len(df.columns))
    df.insert(pos, "Правка, шт", edit_vals)
    df.insert(pos + 1, "Итог, шт", total_vals)
    df["_edited"] = [k in eff for k in keys]

    cols = [c for c in df.columns
            if not c.startswith("_") and c not in SERVICE_COLS]

    accent = PatternFill("solid", fgColor="FFF2CC")       # как в daily.to_excel
    manual = PatternFill("solid", fgColor="E2EFDA")       # зелёный под ручной ввод
    edited_fill = PatternFill("solid", fgColor=EDIT_FILL_HEX)
    head = Font(bold=True)

    def dress(ws, edited_rows: list[bool]):
        ws.freeze_panes = "A2"
        by_name = {c.value: c.column_letter for c in ws[1]}
        for c in ws[1]:
            c.font = head
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for name, fill in (("Прогноз, шт", accent), ("Итог, шт", accent),
                           ("Правка, шт", manual), ("Ваш заказ, шт", manual),
                           ("Почему разошлось", manual)):
            if name in by_name:
                for r_i in range(2, len(edited_rows) + 2):
                    ws[f"{by_name[name]}{r_i}"].fill = fill
        # правленые строки — поверх колонковых заливок, целой строкой
        for r_i, edited in enumerate(edited_rows, start=2):
            if edited:
                for c_i in range(1, ws.max_column + 1):
                    ws.cell(row=r_i, column=c_i).fill = edited_fill
        widths = {"Позиция": 38, "Группа": 18, "Прогноз, шт": 11,
                  "Правка, шт": 11, "Итог, шт": 11, "Восст. спрос, шт": 11,
                  "Ваш заказ, шт": 11, "Почему разошлось": 24,
                  "Продажи по неделям": 20, "Заявки по неделям": 20,
                  "Продано за 28 дн": 12, "Уценено за 28 дн, %": 12,
                  "Пояснение": 46, "Внимание": 26, "Точка": 22}
        for name, w in widths.items():
            if name in by_name:
                ws.column_dimensions[by_name[name]].width = w

    target_pretty = date.fromisoformat(target_date).strftime("%d.%m.%Y")
    version = _s(df["version"].iloc[0]) if "version" in df.columns and len(df) else "—"
    engine = _s(df["engine"].iloc[0]) if "engine" in df.columns and len(df) else "классический (базовая формула)"
    run_at = _s(df["run_at"].iloc[0])[:16] if "run_at" in df.columns and len(df) else "—"
    max_sale = _s(df["max_sale_date"].iloc[0])[:10] if "max_sale_date" in df.columns and len(df) else "—"
    meta_lines = [
        f"Выгрузка из веб-сервиса с правками: {datetime.now():%d.%m.%Y %H:%M}",
        f"Действующих правок: {len(eff)}",
        "«Правка, шт» — число, введённое сотрудником в веб-сервисе;",
        "«Итог, шт» — правка, а где её нет — прогноз. Правленые строки залиты розовым.",
        "",
        f"Версия расчёта: {version}",
        f"Движок: {_engine_label(engine)}",
        f"Расчёт выполнен: {run_at}",
        f"Данные о продажах — по {max_sale} включительно",
        "",
        "Выпущенная заявка не переписывается: правки хранятся отдельным журналом.",
    ]

    fd, tmp = make_export_tempfile(prefix=f"zayavka_{target_date}_")
    os.close(fd)
    path = Path(tmp)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            info = pd.DataFrame(
                {f"Заявка на {target_pretty} с правками": meta_lines})
            info.to_excel(xl, sheet_name="О расчёте", index=False)
            xl.sheets["О расчёте"].column_dimensions["A"].width = 100
            if "Точка" in df.columns:
                for pt, g in df.groupby("Точка"):
                    sheet = str(pt)[:31]
                    xlsx_safe_frame(g[cols].drop(columns=["Точка"])).to_excel(
                        xl, sheet_name=sheet, index=False)
                    dress(xl.sheets[sheet], g["_edited"].tolist())
            xlsx_safe_frame(df[cols]).to_excel(
                xl, sheet_name="Все точки", index=False)
            dress(xl.sheets["Все точки"], df["_edited"].tolist())
    except Exception:
        path.unlink(missing_ok=True)   # не копим мусор во временном каталоге
        raise
    return path
