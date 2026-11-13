# -*- coding: utf-8 -*-
"""Контекст прогона калькулятора: подключение к витрине, сухой прогон, штамп данных.

Всё только на чтение (connect_reader) — калькулятор по построению не может
испортить витрину и не конфликтует с ночным писателем ETL.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

SRC = Path(__file__).resolve().parents[2]                  # prognoz/src
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                     # noqa: E402
from prognoz.assortment import (filter_frame,              # noqa: E402
                                provenance as assortment_provenance)

DRYRUN_PARQUET = OUTPUT / "dryrun_rows.parquet"          # последний прогон (скользящий)
# Канонический прогон окна протокола: создаётся явно при пересчёте базы сравнения
# (py .../dryrun.py <даты окна> и копия файла), ежедневными прогонами НЕ трогается.
# Числа в документах заякорены на него — иначе каждый свежий прогон рвал бы якоря.
DRYRUN_FROZEN = OUTPUT / "dryrun_frozen.parquet"

# Колонки, без которых лестница и экономика не считаются. Дрейф схемы parquet
# должен падать громко и здесь, а не тихо в середине блока. «Группа» обязательна:
# без неё уровень group_day молча выродился бы в копию point_day.
DRYRUN_REQUIRED = ("date", "_point_key", "_sku_key", "Прогноз, шт",
                   "actual", "human", "u40", "canon_name", "Группа")


class DryrunSchemaError(RuntimeError):
    pass


def validate_dryrun(df: pd.DataFrame) -> None:
    missing = [c for c in DRYRUN_REQUIRED if c not in df.columns]
    if missing:
        raise DryrunSchemaError(
            f"dryrun_rows.parquet без колонок {missing} — схема сухого прогона "
            f"изменилась, поправь facts/context.py и блоки, которые на неё смотрят")


def _metric_view(df: pd.DataFrame) -> pd.DataFrame:
    """Неизменяемый frozen остаётся на диске; факты получают чистый read-view."""
    validate_dryrun(df)
    return filter_frame(
        df, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="date")


@dataclass
class Context:
    con: duckdb.DuckDBPyConnection
    today: date
    dryrun: pd.DataFrame | None = None          # скользящий прогон
    dryrun_frozen: pd.DataFrame | None = None   # каноническое окно протокола
    data_stamp: dict = field(default_factory=dict)

    def close(self) -> None:
        self.con.close()


def _file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()[:16]


def build_context(need_dryrun: bool = True) -> Context:
    con = connect_reader()
    max_sale = con.execute(
        "SELECT max(sale_date) FROM mart.fact_sales_daily").fetchone()[0]
    stamp: dict = {"max_sale_date": str(max_sale), **assortment_provenance()}

    dry = frozen = None
    if need_dryrun and DRYRUN_PARQUET.exists():
        raw = pd.read_parquet(DRYRUN_PARQUET)
        dry = _metric_view(raw)
        stamp.update({
            "dryrun_hash": _file_hash(DRYRUN_PARQUET),
            "dryrun_rows_raw": len(raw),
            "dryrun_rows": len(dry),
            "dryrun_dates": int(dry["date"].nunique()),
            "dryrun_period": f"{min(dry['date'])} .. {max(dry['date'])}",
        })
    if need_dryrun and DRYRUN_FROZEN.exists():
        raw_frozen = pd.read_parquet(DRYRUN_FROZEN)
        frozen = _metric_view(raw_frozen)
        stamp["frozen_hash"] = _file_hash(DRYRUN_FROZEN)
        stamp["frozen_rows_raw"] = len(raw_frozen)
        stamp["frozen_rows"] = len(frozen)
        stamp["frozen_period"] = f"{min(frozen['date'])} .. {max(frozen['date'])}"
    return Context(con=con, today=date.today(), dryrun=dry,
                   dryrun_frozen=frozen, data_stamp=stamp)
