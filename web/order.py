# -*- coding: utf-8 -*-
"""Данные страницы «Заявка»: выпущенный прогноз + правки + справочный пересчёт.

Источник текущего выпуска — immutable releases/<issue>/forecast_log; плоский
analysis/output/forecast_log остаётся совместимой проекцией и историческим
fallback. Правки накладываются из журнала web.edits при каждом чтении.
Колонки лога читаются терпимо: у файлов до 12.08 нет engine/_mu_ml/_floor —
страница обязана жить и с ними.

Справочный пересчёт (get_reference) — НЕ основной прогноз: он гоняет донастроенную
формулу базовой формулы по 4/3/2 неделям из строк «Продажи/Заявки по неделям» того же
лога. Нужен менеджеру как ориентир «а что сказала бы формула по короткому окну».
"""
from __future__ import annotations

from . import paths  # noqa: F401  — prognoz/src в sys.path ДО импорта ядра

import hashlib
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import data, edits
from .config import ROOT
from prognoz.assortment import filter_frame

WEEK_LAGS = (7, 14, 21, 28)     # лаги одноимённых дней, как в predict/daily.py
_config_root = os.getenv("PROGNOZ_CONFIG_DIR", "").strip()
CONFIG_ROOT = (Path(_config_root).resolve()
               if _config_root else ROOT / "analysis" / "config")
EXCLUDED_FILE = CONFIG_ROOT / "excluded_days.json"


# --- терпимое чтение значений лога -------------------------------------------

def _s(v) -> str:
    """Строка; None/NaN → пустая."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v)


def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _i(v, default: int = 0) -> int:
    return int(round(_f(v, float(default))))


def _excluded_days() -> set[str]:
    """Даты из analysis/config/excluded_days.json (нетипичные дни, ТЗ Блок 2)."""
    try:
        raw = json.loads(EXCLUDED_FILE.read_text(encoding="utf-8"))
        return {str(d.get("date", "")) for d in raw.get("days", []) if d.get("date")}
    except (OSError, ValueError):
        return set()


def release_fingerprint(target_date: str) -> str:
    """SHA-256 конкретного parquet-выпуска, устойчиво прочитанного с диска."""
    path = data.forecast_path(target_date)
    if not path.exists():
        raise FileNotFoundError(f"Нет файла прогноза на {target_date}")
    for _ in range(3):
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) == (
                after.st_size, after.st_mtime_ns):
            return "sha256:" + digest.hexdigest()
    raise RuntimeError(f"Файл прогноза на {target_date} меняется во время чтения")


@data.ttl_cache(seconds=60)
def _load_log_for_release(target_date: str, release_id: str) -> pd.DataFrame:
    """Лог строго указанного выпуска; release_id входит в ключ кэша."""
    if release_fingerprint(target_date) != release_id:
        raise RuntimeError("Выпуск заявки сменился во время чтения")
    path = data.forecast_path(target_date)
    frame = data.read_parquet_stable(path)
    if {"_point_key", "_sku_key"}.issubset(frame.columns):
        frame = frame.copy()
        frame["target_date"] = target_date
        frame = filter_frame(
            frame, "factory_order", point_col="_point_key", sku_col="_sku_key",
            date_col="target_date")
    if release_fingerprint(target_date) != release_id:
        raise RuntimeError("Выпуск заявки сменился во время чтения")
    return frame


def _load_log(target_date: str) -> pd.DataFrame:
    """Лог заявки на дату. Кэш короткий: файл маленький, но read_parquet_stable
    намеренно ждёт 0,2 с на сверку (mtime, size) — не платим её на каждый клик."""
    release_id = release_fingerprint(target_date)
    return _load_log_for_release(target_date, release_id)


def pair_exists(target_date: str, point_key: str, sku_key: str) -> bool:
    """Есть ли строка (точка, позиция) в заявке даты — защита журнала от мусора."""
    df = _load_log(target_date)
    if "_point_key" not in df.columns or "_sku_key" not in df.columns:
        return True  # старый файл без ключей — проверить нечем
    m = (df["_point_key"] == point_key) & (df["_sku_key"] == sku_key)
    return bool(m.any())


def valid_pairs(target_date: str, release_id: str | None = None) -> set[tuple[str, str]]:
    """Все однозначные ключи выпуска для атомарной проверки пакета правок.

    Пакетная запись работает fail-closed: старый выпуск без технических ключей,
    пустой ключ или дубль пары нельзя безопасно сопоставить с одной строкой.
    """
    current_release = release_id or release_fingerprint(target_date)
    df = _load_log_for_release(target_date, current_release)
    if "_point_key" not in df.columns or "_sku_key" not in df.columns:
        raise ValueError("В выпуске нет технических ключей строк")
    pairs = list(zip(df["_point_key"].astype(str), df["_sku_key"].astype(str)))
    if any(not point_key.strip() or not sku_key.strip()
           for point_key, sku_key in pairs):
        raise ValueError("В выпуске есть пустые технические ключи")
    unique = set(pairs)
    if len(unique) != len(pairs):
        raise ValueError("В выпуске есть неоднозначные дубли строк")
    return unique


def point_name_for_key(target_date: str, point_key: str) -> str:
    df = _load_log(target_date)
    if '_point_key' not in df.columns or 'Точка' not in df.columns:
        raise ValueError('В заявке нет ключей точек')
    names = df.loc[df['_point_key'].astype(str) == point_key, 'Точка'].dropna().unique().tolist()
    if len(names) != 1:
        raise ValueError('Точка не найдена или ключ неоднозначен')
    return str(names[0])


def _calendar(target: date) -> tuple[float, str]:
    """Календарная поправка дня относительно окна лагов (как в проде).

    Пользователь техошибок не видит НИКОГДА (фидбек 12.08: на страницу утекло
    «ModuleNotFoundError»): любая проблема — тихий множитель 1.0 + строка в лог.
    UI показывает календарь только при реальной поправке (mult ≠ 1)."""
    try:
        from prognoz.external import calendar_effects  # noqa: PLC0415
        return calendar_effects.relative_factor(
            target, [target - timedelta(days=lag) for lag in WEEK_LAGS])
    except Exception:  # noqa: BLE001 — календарь не должен ронять заявку
        from loguru import logger  # noqa: PLC0415
        logger.exception("календарная поправка недоступна — отдаю 1.0")
        return 1.0, ""


# --- заявка дня ---------------------------------------------------------------

def get_order(target_date: str, point: str | None = None) -> dict:
    """Строки заявки на дату (+ наложенные правки) и мета для шапки страницы."""
    release_id = release_fingerprint(target_date)
    df = _load_log_for_release(target_date, release_id)
    edit_state = edits.get_effective_state(target_date, release_id)
    target = date.fromisoformat(target_date)

    points = (sorted(df["Точка"].dropna().unique().tolist())
              if "Точка" in df.columns else [])
    view = df if not point else (df[df["Точка"] == point]
                                 if "Точка" in df.columns else df)
    if point:
        points = [point]
        if '_point_key' not in view.columns:
            raise ValueError('В заявке нет ключей точек')
        visible_keys = set(view['_point_key'].astype(str))
        edit_state = {k: v for k, v in edit_state.items() if k[0] in visible_keys}
    df = view

    eff = {
        key: qty_user for key, (qty_user, _revision) in edit_state.items()
        if qty_user is not None
    }

    rows = []
    for _, r in view.iterrows():
        pk, sk = _s(r.get("_point_key")), _s(r.get("_sku_key"))
        qty_user, edit_revision = edit_state.get((pk, sk), (None, 0))
        attention = _s(r.get("Внимание"))
        # Новинка: у свежих файлов пол «уровень сети» (_floor > 0), у любых —
        # флаг «холодный старт» в колонке «Внимание».
        novelty = _f(r.get("_floor"), 0.0) > 0 or "холодный старт" in attention
        rows.append({
            "point": _s(r.get("Точка")),
            "point_key": pk,
            "sku_key": sk,
            "name": _s(r.get("Позиция")),
            "group": _s(r.get("Группа")),
            "qty": _i(r.get("Прогноз, шт")),
            # Второй расчёт той же строки — базовая формула. В журнале лежит
            # рядом с ML-числом (файлы с 14.08); у старых файлов колонки нет,
            # и переключатель на странице просто не показывается.
            "qty_baseline": (int(round(_f(r.get("_mu_baseline"))))
                             if "_mu_baseline" in view.columns
                             and pd.notna(r.get("_mu_baseline")) else None),
            "qty_user": qty_user,                  # действующая правка или None
            "edit_revision": int(edit_revision),   # защита пакетного сохранения
            "sales_weeks": _s(r.get("Продажи по неделям")),
            "orders_weeks": _s(r.get("Заявки по неделям")),
            # «Продано за 28 дн.» — целое для колонки на странице; старые файлы
            # без колонки отдают None, и страница показывает «—».
            "sold28": (int(round(_f(r.get("Продано за 28 дн"))))
                       if "Продано за 28 дн" in view.columns
                       and pd.notna(r.get("Продано за 28 дн")) else None),
            "u40_pct": _f(r.get("Уценено за 28 дн, %")),
            "note": _s(r.get("Пояснение")),
            "attention": attention,
            "novelty": bool(novelty),
        })

    cal_mult, cal_why = _calendar(target)
    excl = _excluded_days()
    excluded_in_window = [
        (target - timedelta(days=lag)).isoformat() for lag in WEEK_LAGS
        if (target - timedelta(days=lag)).isoformat() in excl
    ]

    engine = (_s(df["engine"].iloc[0]) if "engine" in df.columns and len(df)
              else "baseline")
    meta = {
        "target_date": target_date,
        "release_id": release_id,
        "engine": engine or "baseline",
        "version": _s(df["version"].iloc[0]) if "version" in df.columns and len(df) else "",
        "max_sale_date": (_s(df["max_sale_date"].iloc[0])[:10]
                          if "max_sale_date" in df.columns and len(df) else ""),
        "cal": {"mult": round(float(cal_mult), 3), "why": cal_why},
        "excluded_in_window": excluded_in_window,
        "points": points,
        "n_rows": int(len(df)),
        "total_qty": _i(df["Прогноз, шт"].sum()) if "Прогноз, шт" in df.columns else 0,
        # Итог второго расчёта по той же выборке — для строки «а формула просила бы»
        "has_baseline": "_mu_baseline" in df.columns,
        "total_qty_baseline": (int(round(float(view["_mu_baseline"].fillna(0).round().sum())))
                               if "_mu_baseline" in view.columns else None),
        "edited_count": len(eff),
    }
    return {"rows": rows, "meta": meta}


# --- справочный пересчёт по 4/3/2 неделям ------------------------------------

def _parse_weeks(raw) -> list[float]:
    """«1 / 1.963 / 0 / 1» → [1.0, 1.963, 0.0, 1.0] (лаги 7/14/21/28)."""
    s = _s(raw)
    if not s:
        return []
    out = []
    for part in s.split("/"):
        part = part.strip().replace(",", ".")
        try:
            out.append(float(part))
        except ValueError:
            out.append(0.0)
    return out


def get_reference(target_date: str, weeks: int = 4, point_key: str | None = None) -> dict:
    """Справочный расчёт формулой по первым `weeks` неделям истории строки.

    Лаг, чья дата попала в excluded_days.json, выкидывается из окна. Меньше двух
    недель осталось — честный None (формуле нужно 2–4 значения). kvp_locked НЕ
    передаётся: это справка, предохранители прод-движка здесь не воспроизводим.
    """
    if weeks not in (2, 3, 4):
        raise ValueError("weeks должен быть 2, 3 или 4")
    from prognoz.engine.improved import forecast as forecast_v2  # noqa: PLC0415

    df = _load_log(target_date)
    if point_key is not None:
        if '_point_key' not in df.columns:
            raise ValueError('В заявке нет ключей точек')
        df = df[df['_point_key'].astype(str) == point_key]
    target = date.fromisoformat(target_date)
    excl = _excluded_days()
    lag_dates = [target - timedelta(days=lag) for lag in WEEK_LAGS[:weeks]]
    keep = [i for i, d in enumerate(lag_dates) if d.isoformat() not in excl]
    dropped = [d.isoformat() for d in lag_dates if d.isoformat() in excl]

    rows = []
    for _, r in df.iterrows():
        sh = _parse_weeks(r.get("Продажи по неделям"))
        oh = _parse_weeks(r.get("Заявки по неделям"))
        sh_w = [sh[i] for i in keep if i < len(sh)]
        oh_w = [oh[i] for i in keep if i < len(oh)]
        ref_qty = None
        if len(sh_w) >= 2:
            # как в daily.py:255, только без locked: заявок нет — КВП-шаг пропускается
            oh_arg = oh_w if (len(oh_w) == len(sh_w) and any(oh_w)) else None
            fc = forecast_v2(sh_w, oh_arg, kvp_locked=False)
            ref_qty = int(fc.qty)
        rows.append({
            "point_key": _s(r.get("_point_key")),
            "sku_key": _s(r.get("_sku_key")),
            "ref_qty": ref_qty,
        })
    return {"rows": rows, "weeks": weeks, "excluded": dropped}
