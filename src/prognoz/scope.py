# -*- coding: utf-8 -*-
"""Shared product scope and canonical point mapping."""
import json
from functools import lru_cache
from pathlib import Path

GROUP_LIKE = "Меню / ДЕМО%"
GROUP_NOT_LIKE = "%Товары перепродажа%"

GROUP_NOT_LIKE_BAR = ""


# Готовый кусок SQL: подставляется в WHERE к mart.dim_sku (алиас передаётся параметром)
def sku_filter(alias: str = "s", with_bar: bool = False) -> str:
    sql = (f"{alias}.group_path LIKE '{GROUP_LIKE}' "
           f"AND {alias}.group_path NOT LIKE '{GROUP_NOT_LIKE}'")
    if GROUP_NOT_LIKE_BAR and not with_bar:
        sql += f" AND {alias}.group_path NOT LIKE '{GROUP_NOT_LIKE_BAR}'"
    return sql


# Точки: канонизация merged-подразделений в основную точку
MARKET_ROLES = ("market", "market_hub", "merged")
CANON_POINTS = """
    SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS pt
    FROM mart.dim_point WHERE role IN ('market', 'market_hub', 'merged')
"""


def canon_cte(alias: str = "c") -> str:
    """CTE канонизации точек: `WITH {canon_cte()} SELECT … JOIN c ON …`.

    До 04.08 этот кусок был скопирован сырым SQL в ~13 местах девяти файлов —
    ровно так же, как когда-то скоуп, из-за дублей которого в бэктест попали
    цеха и ингредиенты. Меняется канонизация — меняется ЗДЕСЬ и нигде больше.
    """
    roles = ", ".join(f"'{r}'" for r in MARKET_ROLES)
    return (f"{alias} AS (SELECT point_key, "
            f"coalesce(nullif(merged_into, ''), point_key) AS pt "
            f"FROM mart.dim_point WHERE role IN ({roles}))")

# Optional mapping of superseded product IDs to current product IDs.
SKU_TWINS_FILE = (Path(__file__).resolve().parents[2]
                  / "analysis" / "config" / "sku_twins.json")


@lru_cache(maxsize=1)
def sku_twins() -> dict:
    """ghost sku_key → live sku_key. Пустой словарь, если разметки нет."""
    if not SKU_TWINS_FILE.exists():
        return {}
    data = json.loads(SKU_TWINS_FILE.read_text(encoding="utf-8"))
    return {t["ghost"]: t["live"] for t in data.get("twins", [])}


def sku_twins_cte(alias: str = "tw") -> str:
    """CTE склейки: `WITH {sku_twins_cte()} … LEFT JOIN tw ON tw.ghost = x.sku_key`,
    дальше вместо x.sku_key использовать coalesce(tw.live, x.sku_key).

    CTE всегда непустой (заглушка '-'), чтобы SQL не менял форму от наличия разметки.
    """
    pairs = sku_twins() or {"-": "-"}
    vals = ", ".join(f"('{g}', '{l}')" for g, l in pairs.items())
    return f"{alias} AS (SELECT * FROM (VALUES {vals}) v(ghost, live))"


HEADLINE_POINTS = ("ДЕМО — Вокзальная", "ДЕМО — Университет", "ДЕМО — Набережная")
OUT_OF_HEADLINE = ()
COMPARABLE_FULL_PERIOD = HEADLINE_POINTS
