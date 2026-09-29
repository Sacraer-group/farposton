# -*- coding: utf-8 -*-
"""Отчёт «ДО»: состояние маркетов «сети» за 01.08.2025–31.07.2026.

Смысл документа — зафиксировать точку отсчёта ДО внедрения прогноза, чтобы через полгода
было с чем сравнивать и из чего собирать кейс. Поэтому здесь важнее честность, чем красота:
у каждого показателя рядом стоит оговорка о качестве данных, а то, что посчитать нельзя,
названо прямо, а не умолчано.

Три вещи, которые надо понимать про эти числа:

1. СЕБЕСТОИМОСТИ В ДАННЫХ НЕТ. Поле «Себестоимость» пустое и в перемещениях, и в передачах
   между организациями; в актах списания суммы заполнены в 5% строк. Поэтому все деньги —
   в ценах реализации. Колонки «потери по себестоимости» в отчёте нет намеренно:
   коэффициент 0,35 был нашей гипотезой, а не замером.

2. УЦЕНКА ДАЁТСЯ ИНТЕРВАЛОМ. Каждый день на каждой точке стартует распродажа −40%, но
   параллельно работает программа −10%, а различить их построчно нельзя: поле вида скидки
   всегда пустое. Верхняя оценка считает всю скидку как −40%, нижняя — как «остальное −10%».

3. СЕТЬ МЕНЯЛАСЬ. Точки 5 открылась 26.06.2026, у неё в году лишь месяц. Сетевые итоги
   даются в двух видах: «как есть» и «сопоставимые точки» (работавшие весь период).

Запуск: PYTHONUTF8=1 py prognoz/src/prognoz/report/before_report.py
Выход:  analysis/output/Показатели_маркетов_ДО.xlsx + docs/Показатели_маркетов_ДО.md
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, PROGNOZ, connect_reader     # noqa: E402

sys.path.insert(0, str(SRC))
from prognoz.assortment import sql_exclusion_predicate    # noqa: E402
from prognoz.scope import GROUP_LIKE, GROUP_NOT_LIKE      # noqa: E402

SINCE, UNTIL = date(2025, 8, 1), date(2026, 7, 31)
COMPARABLE = ("Точка 1", "Точка 2")   # работали весь период целиком
CANON = """
    WITH c AS (
        SELECT point_key, coalesce(nullif(merged_into, ''), point_key) AS pt
        FROM mart.dim_point WHERE role IN ('market', 'market_hub', 'merged')
    ),
    nm AS (SELECT canon_name, point_key AS pt FROM mart.dim_point
           WHERE role IN ('market', 'market_hub')),
    food AS (SELECT sku_key, name, group_path FROM mart.dim_sku
             WHERE group_path LIKE '{inc}' AND group_path NOT LIKE '{exc}')
""".format(inc=GROUP_LIKE, exc=GROUP_NOT_LIKE)
SALES_ASSORTMENT = sql_exclusion_predicate(
    "metric_scope", point_expr="c.pt", sku_expr="s.sku_key",
    date_expr="s.sale_date")
WRITEOFF_ASSORTMENT = sql_exclusion_predicate(
    "metric_scope", point_expr="c.pt", sku_expr="w.sku_key",
    date_expr="w.wo_date")
REGIME_ASSORTMENT = sql_exclusion_predicate(
    "metric_scope", point_expr="c.pt", sku_expr="r.sku_key",
    date_expr="r.trade_date")


def q(con, sql, params=None):
    return con.execute(CANON + sql, params or [SINCE, UNTIL]).fetchdf()


def by_point(con):
    return q(con, f"""
        SELECT nm.canon_name AS "Точка",
               min(s.sale_date)                               AS "Первая продажа",
               count(DISTINCT s.sale_date)                    AS "Дней работы",
               round(sum(s.revenue_gross) / 1e6, 2)           AS "Выручка до скидок, млн ₽",
               round(sum(s.revenue) / 1e6, 2)                 AS "Выручка факт, млн ₽",
               round(sum(s.disc_sum) / 1e6, 2)                AS "Скидки, млн ₽",
               round(100 * sum(s.disc_sum) / nullif(sum(s.revenue_gross), 0), 1) AS "Скидка, %",
               round(sum(s.qty_sold - s.qty_returned))        AS "Продано, шт",
               round(sum(s.u40_strict))                       AS "Уценено, шт (нижн.)",
               round(sum(s.u40))                              AS "Уценено, шт (верх.)",
               round(100 * sum(s.u40) / nullif(sum(s.qty_sold - s.qty_returned), 0), 1)
                                                              AS "Доля уценки, % (верх.)",
               count(DISTINCT s.sku_key)                      AS "Позиций в продаже",
               round(sum(s.qty_sold - s.qty_returned) / nullif(count(DISTINCT s.sale_date), 0), 1)
                                                              AS "Штук в день"
        FROM mart.fact_sales_daily s
        JOIN c ON c.point_key = s.point_key
        JOIN nm ON nm.pt = c.pt
        JOIN food ON food.sku_key = s.sku_key
        WHERE s.sale_date BETWEEN ? AND ? AND {SALES_ASSORTMENT}
        GROUP BY 1 ORDER BY 3 DESC
    """)


def by_month(con):
    return q(con, f"""
        SELECT strftime(s.sale_date, '%Y-%m')                 AS "Месяц",
               nm.canon_name                                  AS "Точка",
               round(sum(s.revenue) / 1e6, 2)                 AS "Выручка, млн ₽",
               round(100 * sum(s.disc_sum) / nullif(sum(s.revenue_gross), 0), 1) AS "Скидка, %",
               round(sum(s.qty_sold - s.qty_returned))        AS "Продано, шт",
               round(100 * sum(s.u40) / nullif(sum(s.qty_sold - s.qty_returned), 0), 1)
                                                              AS "Доля уценки, %"
        FROM mart.fact_sales_daily s
        JOIN c ON c.point_key = s.point_key
        JOIN nm ON nm.pt = c.pt
        JOIN food ON food.sku_key = s.sku_key
        WHERE s.sale_date BETWEEN ? AND ? AND {SALES_ASSORTMENT}
        GROUP BY 1, 2 ORDER BY 1, 2
    """)


def by_category(con):
    return q(con, f"""
        SELECT regexp_extract(food.group_path, '([^/]+)$')    AS "Группа",
               round(sum(s.revenue) / 1e6, 2)                 AS "Выручка, млн ₽",
               round(sum(s.qty_sold - s.qty_returned))        AS "Продано, шт",
               round(100 * sum(s.u40) / nullif(sum(s.qty_sold - s.qty_returned), 0), 1)
                                                              AS "Доля уценки, %",
               count(DISTINCT s.sku_key)                      AS "Позиций",
               round(sum(s.revenue) / nullif(sum(s.qty_sold - s.qty_returned), 0)) AS "Средняя цена, ₽"
        FROM mart.fact_sales_daily s
        JOIN c ON c.point_key = s.point_key
        JOIN food ON food.sku_key = s.sku_key
        WHERE s.sale_date BETWEEN ? AND ? AND {SALES_ASSORTMENT}
        GROUP BY 1 ORDER BY 2 DESC
    """)


def weekday_profile(con):
    return q(con, f"""
        SELECT nm.canon_name AS "Точка",
               CASE dayofweek(s.sale_date) WHEN 1 THEN 'пн' WHEN 2 THEN 'вт' WHEN 3 THEN 'ср'
                    WHEN 4 THEN 'чт' WHEN 5 THEN 'пт' WHEN 6 THEN 'сб' ELSE 'вс' END AS "День",
               dayofweek(s.sale_date) AS "_ord",
               round(sum(s.qty_sold - s.qty_returned) / nullif(count(DISTINCT s.sale_date), 0), 1)
                                                              AS "Штук в день",
               round(sum(s.revenue) / nullif(count(DISTINCT s.sale_date), 0) / 1000, 1)
                                                              AS "Выручка в день, тыс ₽"
        FROM mart.fact_sales_daily s
        JOIN c ON c.point_key = s.point_key
        JOIN nm ON nm.pt = c.pt
        JOIN food ON food.sku_key = s.sku_key
        WHERE s.sale_date BETWEEN ? AND ? AND {SALES_ASSORTMENT}
        GROUP BY 1, 2, 3 ORDER BY 1, 3
    """)


def writeoffs(con):
    """Списания готовой продукции. Период указывается фактический — по покрытию данных."""
    return q(con, f"""
        SELECT nm.canon_name AS "Точка",
               strftime(w.wo_date, '%Y-%m') AS "Месяц",
               round(sum(w.qty_writeoff)) AS "Списано, шт",
               count(DISTINCT w.sku_key)  AS "Позиций"
        FROM mart.fact_writeoff_daily w
        JOIN c ON c.point_key = w.point_key
        JOIN nm ON nm.pt = c.pt
        JOIN food ON food.sku_key = w.sku_key
        WHERE w.wo_date BETWEEN ? AND ? AND {WRITEOFF_ASSORTMENT}
        GROUP BY 1, 2 ORDER BY 1, 2
    """)


def worst_sku(con):
    # CANON уже открыл WITH — здесь продолжаем список CTE, а не начинаем новый
    return q(con, f"""
        , sales AS (
            SELECT s.sku_key, sum(s.qty_sold - s.qty_returned) AS sold,
                   sum(s.u40) AS u40, sum(s.revenue) AS rev
            FROM mart.fact_sales_daily s
            JOIN c ON c.point_key = s.point_key
            JOIN food ON food.sku_key = s.sku_key
            WHERE s.sale_date BETWEEN ? AND ? AND {SALES_ASSORTMENT}
            GROUP BY 1
        )
        SELECT food.name AS "Позиция",
               regexp_extract(food.group_path, '([^/]+)$') AS "Группа",
               round(sales.sold) AS "Продано, шт",
               round(sales.u40)  AS "Уценено, шт",
               round(100 * sales.u40 / nullif(sales.sold, 0), 1) AS "Доля уценки, %",
               round(sales.rev / 1000) AS "Выручка, тыс ₽",
               round(0.4 * sales.u40 * sales.rev / nullif(sales.sold, 0) / 1000)
                                       AS "Недополучено на уценке, тыс ₽"
        FROM sales JOIN food ON food.sku_key = sales.sku_key
        WHERE sales.sold >= 200
        ORDER BY "Доля уценки, %" DESC NULLS LAST
        LIMIT 30
    """)


def regimes(con):
    """Режимы наблюдения спроса — считаются только там, где известен факт привоза."""
    return q(con, f"""
        SELECT nm.canon_name AS "Точка", r.regime AS "Режим",
               count(*) AS "Позиций-дней",
               round(sum(r.sold)) AS "Продано, шт"
        FROM mart.fact_regime_daily r
        JOIN c ON c.point_key = r.point_key
        JOIN nm ON nm.pt = c.pt
        JOIN food ON food.sku_key = r.sku_key
        WHERE r.trade_date BETWEEN ? AND ? AND r.has_supply
          AND {REGIME_ASSORTMENT}
        GROUP BY 1, 2 ORDER BY 1, 2
    """)


def main():
    con = connect_reader()
    tables = {
        "Сводка по точкам": by_point(con),
        "По месяцам": by_month(con),
        "По группам": by_category(con),
        "Профиль дня недели": weekday_profile(con),
        "Худшие по уценке": worst_sku(con),
    }
    try:
        tables["Списания"] = writeoffs(con)
    except Exception as e:                      # витрина списаний может быть ещё не собрана
        print(f"⚠ списания пропущены: {e}")
    try:
        r = regimes(con)
        if not r.empty:
            tables["Дефицит и остаток"] = r
    except Exception as e:
        print(f"⚠ режимы пропущены: {e}")

    pt = tables["Сводка по точкам"]
    con.close()

    tables["Профиль дня недели"] = tables["Профиль дня недели"].drop(columns=["_ord"])

    meta = pd.DataFrame({"Замер состояния сети до внедрения прогноза": [
        f"Период: {SINCE:%d.%m.%Y} — {UNTIL:%d.%m.%Y}",
        "Скоуп: меню маркета «Меню» без расходников к блюдам. ⚠ БАР И НАПИТКИ",
        "ЗДЕСЬ ВНУТРИ (7% объёма): так был снят замер 03.08, и снимок не переписывается.",
        "С 04.08 бар выведен из скоупа прогноза (решение базовой формулы) — сопоставление",
        "с баром/без бара смотреть в Панели показателей (факты before.* и before.with_bar.*).",
        "",
        "ЧТО СЧИТАЕТСЯ ТОЧНО:",
        "  выручка до скидок и фактическая, суммы и доли скидок, продано штук,",
        "  ассортимент, профиль дня недели, средний дневной объём.",
        "",
        "ЧТО ДАЁТСЯ ИНТЕРВАЛОМ:",
        "  уценённые единицы. Каждый день стартует распродажа −40%, но параллельно",
        "  работает программа −10%, а вид скидки в 1С не заполняется. Нижняя оценка",
        "  считает, что все прочие единицы получили −10%; верхняя — что вся скидка −40%.",
        "",
        "ЧЕГО В ДАННЫХ НЕТ (и потому нет в отчёте):",
        "  • Себестоимость и маржа. Поле пустое во всех документах движения,",
        "    в актах списания суммы заполнены в 5% строк. Все деньги — в ценах реализации.",
        "  • Чеки, средний чек, число покупателей. Из кассы в 1С приходит один сводный",
        "    документ за сутки, почековых данных в базе нет.",
        "  • Время продажи и час, когда позиция закончилась.",
        "  • Упущенный спрос в штуках — только косвенная оценка по признаку дефицита.",
        "",
        "ОГРАНИЧЕНИЯ ПЕРИОДА:",
        "  • Точки 5 открылась 26.06.2026 — в году у неё лишь месяц.",
        "  • Точки 4 начал работу в октябре 2025, Точка 3 — в феврале 2026.",
        "  • Сопоставимые весь период точки: " + ", ".join(COMPARABLE) + ".",
        "  • Факт привоза документально прослеживается с марта 2026, поэтому",
        "    раздел «Дефицит и остаток» считается только с этой даты.",
        "",
        f"Отчёт подготовлен: {date.today():%d.%m.%Y}",
    ]})

    OUTPUT.mkdir(parents=True, exist_ok=True)
    xls = OUTPUT / "Показатели_маркетов_ДО.xlsx"
    with pd.ExcelWriter(xls, engine="openpyxl") as xl:
        meta.to_excel(xl, sheet_name="О замере", index=False)
        for name, df in tables.items():
            df.to_excel(xl, sheet_name=name[:31], index=False)
            xl.sheets[name[:31]].freeze_panes = "A2"

    md = markdown_summary(tables, meta)
    md_path = PROGNOZ / "docs" / "Показатели_маркетов_ДО.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"Отчёт «ДО» за {SINCE:%d.%m.%Y}–{UNTIL:%d.%m.%Y}")
    print(pt.to_string(index=False))
    print(f"\n→ {xls}\n→ {md_path}")
    return tables


def to_md(df: pd.DataFrame) -> str:
    """Markdown-таблица без внешних зависимостей (pandas.to_markdown требует tabulate,
    а ставить лишний пакет на сервер клиента ради трёх таблиц не стоит)."""
    cols = [str(c) for c in df.columns]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    lines = [head, sep]
    for row in df.itertuples(index=False):
        cells = []
        for v in row:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                cells.append("—")
            elif isinstance(v, float):
                cells.append(f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", " "))
            elif isinstance(v, (int,)):
                cells.append(f"{v:,}".replace(",", " "))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def markdown_summary(tables: dict, meta: pd.DataFrame) -> str:
    """Короткая читаемая версия — Excel открывают не все, а прочитать надо всем."""
    pt = tables["Сводка по точкам"]
    tot = {c: pt[c].sum() for c in ("Выручка до скидок, млн ₽", "Выручка факт, млн ₽",
                                    "Скидки, млн ₽", "Продано, шт",
                                    "Уценено, шт (нижн.)", "Уценено, шт (верх.)")}
    lo = 100 * tot["Уценено, шт (нижн.)"] / tot["Продано, шт"]
    hi = 100 * tot["Уценено, шт (верх.)"] / tot["Продано, шт"]
    parts = [
        f"# Показатели маркетов «сети» до внедрения прогноза",
        f"\nПериод: **{SINCE:%d.%m.%Y} — {UNTIL:%d.%m.%Y}**. Скоуп: меню маркета "
        f"(без расходников и ингредиентов). Все суммы — в ценах реализации: "
        f"себестоимости в 1С нет ни в одном документе движения.\n",
        "## Итог по сети\n",
        f"- Продано **{tot['Продано, шт']:,.0f} единиц** готовой еды".replace(",", " "),
        f"- Выручка **{tot['Выручка факт, млн ₽']:.1f} млн ₽** "
        f"(до скидок {tot['Выручка до скидок, млн ₽']:.1f} млн ₽)",
        f"- Скидок дано на **{tot['Скидки, млн ₽']:.1f} млн ₽** — это "
        f"{100 * tot['Скидки, млн ₽'] / tot['Выручка до скидок, млн ₽']:.1f}% выручки до скидок",
        f"- По вечерней уценке ушло от **{tot['Уценено, шт (нижн.)']:,.0f}** до "
        f"**{tot['Уценено, шт (верх.)']:,.0f} единиц** — от {lo:.0f}% до {hi:.0f}% всего проданного".replace(",", " "),
        "\nИнтервал по уценке — потому что параллельно с распродажей −40% работает программа −10%,",
        "а вид скидки в 1С не заполняется: различить их построчно нельзя.\n",
        "## По точкам\n",
        "> ⚠️ **Полный год отработали только Точки 1 и Точка 2.** Точки 4 открылся",
        "> 06.10.2025, Точка 3 — 27.02.2026, Точки 5 — 26.06.2026 (у неё в этом отчёте всего",
        "> 35 дней). Сравнивать их показатели между собой напрямую нельзя: у молодой точки",
        "> и ассортимент ещё не устоялся, и заказы делаются вслепую, без своей истории продаж.",
        "> Смотрите на колонку «Дней работы» рядом с каждой строкой.\n",
        to_md(pt),
    ]
    for name in ("По группам", "Худшие по уценке", "Дефицит и остаток", "Списания"):
        if name in tables and not tables[name].empty:
            df = tables[name]
            parts += [f"\n## {name}\n", to_md(df.head(25))]
    parts += ["\n## Оговорки\n"]
    parts += [f"- {s}" for s in meta.iloc[:, 0].tolist() if s and not s.endswith(":")]
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    main()
