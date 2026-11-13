# -*- coding: utf-8 -*-
"""Экономика ошибки заказа: цена промаха в каждую сторону и потери в деньгах.

До 03.08 вся эта арифметика жила только в markdown — и в первой редакции ушла
клиенту с двойным счётом (Co = 0,379P: единице, ушедшей по вечерней скидке,
засчитывалась «недополученная маржа», как будто она продалась бы по полной цене).
Ошибку поймал только адверсарный пересчёт. Здесь она закрыта конструкцией:

  ЛИШНЯЯ единица = не проданная ВООБЩЕ = списанная. Стоит себестоимости: Co = c·P.
  Единицы, ушедшие по скидке, лишними не были — они проданы, просто дешевле.
  НЕДОСТАЮЩАЯ единица = потерянная маржа: Cu = (1 − c)·P.

Инвариант, который ловит двойной счёт (см. test_economics):
  (заказано − продано) за окно ≈ документальные списания 1С за то же окно.
По старой формуле списаний «должно было быть» вдвое меньше факта.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from prognoz.assortment import sql_exclusion_predicate
from prognoz.facts.registry import block
from prognoz.scope import GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, canon_cte

COST_RATIO = 0.33     # себестоимость в долях цены — демонстрационное допущение (базовая формула, 03.08)
SRC = "facts.blocks.economics:compute"


def unit_costs(cost_ratio: float = COST_RATIO) -> tuple[float, float]:
    """(Co, Cu) в долях цены P: лишняя единица и недостающая."""
    return cost_ratio, 1.0 - cost_ratio


def optimal_quantile(co: float, cu: float) -> float:
    """Критический квантиль задачи газетчика: заказ должен покрывать спрос
    с этой вероятностью."""
    return cu / (cu + co)


def losses(rows: pd.DataFrame, order_col: str, *, avg_price: float,
           cost_ratio: float = COST_RATIO) -> dict:
    """Потери политики заказа на строках сухого прогона.

    Перезаказ и недозаказ считаются ПО СТРОКАМ (позиция × точка × день) и не
    сальдируются между позициями: излишек шаурмы не закрывает дефицит шаурмы.
    """
    co, cu = unit_costs(cost_ratio)
    e = rows[order_col] - rows["actual"]
    over = float(e[e > 0].sum())
    under = float(-e[e < 0].sum())
    days = int(rows["date"].nunique())
    loss_p = over * co + under * cu                     # в долях цены P
    rub_day = loss_p * avg_price / days if days else float("nan")
    return {
        "over_units": round(over), "under_units": round(under),
        "net_units": round(over - under),
        "bias_pct": round(100 * (over - under) / rows["actual"].sum(), 1),
        # два знака, не целое: на игрушечных примерах (и в тестах) целое округление
        # прятало бы саму величину, которую проверяем
        "loss_p": round(loss_p, 2), "rub_per_day": round(rub_day),
        "rub_per_year_m": round(rub_day * 365 / 1e6, 1),
    }


def multiplier_scan(rows: pd.DataFrame, *, avg_price: float,
                    mults=(1.0, 1.1, 1.15, 1.2, 1.5)) -> pd.DataFrame:
    """Что даёт умножение прогноза на коэффициент. Спойлер по данным: ничего —
    модель промахивается адресом, а не масштабом, и буфер лишь наращивает излишек."""
    out = []
    for m in mults:
        r = rows.assign(_scaled=(rows["Прогноз, шт"] * m).round())
        d = losses(r, "_scaled", avg_price=avg_price)
        out.append({"множитель": m, **d})
    return pd.DataFrame(out)


def order_sales_writeoff_gap(con, rows: pd.DataFrame,
                             since: date, until: date) -> dict:
    """Инвариант: (заказано − продано) на парных строках против списаний 1С.

    Смысл: раз остатка нет (готовая еда живёт день), всё, что заказали и не
    продали, обязано лежать в списаниях. Расхождение в разы — признак двойного
    счёта в экономике или дыры в данных.

    ⚠️ Считать «заказано» и «продано» по всей витрине нельзя: заявки покрывают
    не весь поток (за окно протокола продано 47 063 при заказанных 44 027 —
    часть товара приходит на точки вне заявок на перемещение). Поэтому берём
    ПАРНЫЕ строки сухого прогона: позиции, которые сотрудник заказывал.

    Замечание панели проверки 04.08: даже так множества не тождественны — часть
    списаний лежит на позициях-днях без заявки. Поэтому списания считаются
    ДВАЖДЫ: по всему скоупу (справочно) и только по парным ключам точка×SKU×день
    с лагом 0–1 день (честное сравнение). Инвариант — порядок величин, а не
    тождество: точная построчная привязка невозможна, списание не ссылается
    на заявку.
    """
    p = rows[rows["human"] > 0]
    ordered = float(p["human"].sum())
    sold = float(p["actual"].sum())

    wo_all = float(con.execute(f"""
        WITH {canon_cte()}
        SELECT sum(w.qty_writeoff) FROM mart.fact_writeoff_daily w
        JOIN c ON c.point_key=w.point_key
        JOIN mart.dim_sku s ON s.sku_key=w.sku_key
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
          AND s.group_path NOT LIKE ?
          AND w.wo_date BETWEEN ? AND ?
          AND {sql_exclusion_predicate(
              "metric_scope", point_expr="c.pt", sku_expr="w.sku_key",
              date_expr="w.wo_date")}
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,
          since, until]).fetchone()[0] or 0)

    # списания только на парных ключах: (точка, SKU, день заявки или следующий)
    keys = p[["_point_key", "_sku_key", "date"]].drop_duplicates()
    wo_df = con.execute(f"""
        WITH {canon_cte()}
        SELECT c.pt AS _point_key, w.sku_key AS _sku_key, w.wo_date,
               sum(w.qty_writeoff) AS qty
        FROM mart.fact_writeoff_daily w
        JOIN c ON c.point_key=w.point_key
        JOIN mart.dim_sku s ON s.sku_key=w.sku_key
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
          AND s.group_path NOT LIKE ?
          AND w.wo_date BETWEEN ? AND ?
          AND {sql_exclusion_predicate(
              "metric_scope", point_expr="c.pt", sku_expr="w.sku_key",
              date_expr="w.wo_date")}
        GROUP BY 1, 2, 3
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,
          since, until]).fetchdf()
    wo_df["wo_date"] = pd.to_datetime(wo_df["wo_date"]).dt.date
    key_set = set()
    for r in keys.itertuples():
        key_set.add((r._1, r._2, r.date))                       # день заявки
    from datetime import timedelta                              # noqa: PLC0415
    paired_mask = wo_df.apply(
        lambda r: (r["_point_key"], r["_sku_key"], r["wo_date"]) in key_set
        or (r["_point_key"], r["_sku_key"], r["wo_date"] - timedelta(days=1)) in key_set,
        axis=1) if len(wo_df) else pd.Series(dtype=bool)
    wo_paired = float(wo_df.loc[paired_mask, "qty"].sum()) if len(wo_df) else 0.0

    leftover = ordered - sold
    gap = abs(leftover - wo_paired) / wo_paired if wo_paired else float("nan")
    return {"ordered": round(ordered), "sold": round(sold),
            "leftover": round(leftover),
            "written_off": round(wo_paired),
            "written_off_scope": round(wo_all),
            "gap_pct": round(100 * gap, 1)}


def avg_price_gross(con, since: date, until: date) -> float:
    """Средняя цена до скидок по скоупу — знаменатель для перевода потерь в рубли.

    Окно ЗАКРЫТО с обеих сторон: без верхней границы цена считалась бы «до сегодня»
    и замороженные факты eco.* дрейфовали бы с каждым ночным ETL при неизменном
    прогоне — ровно тот тихий дрейф, против которого реестр строился.
    """
    return float(con.execute(f"""
        WITH {canon_cte()}
        SELECT sum(f.revenue_gross)/nullif(sum(f.qty_sold - f.qty_returned),0)
        FROM mart.fact_sales_daily f
        JOIN c ON c.point_key=f.point_key
        JOIN mart.dim_sku s ON s.sku_key=f.sku_key
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ?
          AND s.group_path NOT LIKE ? AND f.sale_date BETWEEN ? AND ?
          AND {sql_exclusion_predicate(
              "metric_scope", point_expr="c.pt", sku_expr="f.sku_key",
              date_expr="f.sale_date")}
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, since, until]).fetchone()[0])


@block("economics")
def compute(ctx, reg) -> None:
    co, cu = unit_costs()
    q_star = optimal_quantile(co, cu)
    base = [
        {"id": "eco.cost_ratio", "value": COST_RATIO, "unit": "доля цены",
         "title": "себестоимость готовых блюд",
         "method": "со слов демонстрационного допущения («ориентир грубо 33%»); в 1С не проверяемо"},
        {"id": "eco.co", "value": co, "unit": "доля цены",
         "title": "цена лишней единицы (= списанная себестоимость)"},
        {"id": "eco.cu", "value": cu, "unit": "доля цены",
         "title": "цена недостающей единицы (= потерянная маржа)"},
        {"id": "eco.quantile", "value": round(q_star, 3), "unit": "",
         "title": "оптимальный квантиль заказа Cu/(Cu+Co)"},
        {"id": "eco.cu_over_co", "value": round(cu / co, 2), "unit": "×",
         "title": "во сколько раз недозаказ дороже перезаказа"},
    ]

    rows = ctx.dryrun_frozen if ctx.dryrun_frozen is not None else ctx.dryrun
    if rows is not None:
        period = (ctx.data_stamp.get("frozen_period")
                  or ctx.data_stamp.get("dryrun_period", ""))
        d0, d1 = min(rows["date"]), max(rows["date"])
        price = avg_price_gross(ctx.con, d0, d1)
        p = rows[rows["human"] > 0]

        for who, col in (("human", "human"), ("model", "Прогноз, шт")):
            d = losses(p, col, avg_price=price)
            base += [{"id": f"eco.{who}.{k}", "value": v,
                      "unit": {"over_units": "шт", "under_units": "шт",
                               "net_units": "шт", "bias_pct": "%", "loss_p": "P",
                               "rub_per_day": "₽/день",
                               "rub_per_year_m": "млн ₽/год"}[k],
                      "title": f"потери ({'сотрудник' if who == 'human' else 'модель'}): {k}",
                      "period": period}
                     for k, v in d.items()]
        h = losses(p, "human", avg_price=price)
        m = losses(p, "Прогноз, шт", avg_price=price)
        base.append({"id": "eco.ratio", "value": round(m["loss_p"] / h["loss_p"], 2),
                     "unit": "×", "title": "потери модели к потерям сотрудника",
                     "period": period})
        base.append({"id": "eco.avg_price", "value": round(price, 1), "unit": "₽",
                     "title": "средняя цена до скидок по скоупу", "period": period})

        scan = multiplier_scan(p, avg_price=price)
        reg.table("eco.mult_scan", scan, title="Скан множителей прогноза")

        gap = order_sales_writeoff_gap(ctx.con, rows, d0, d1)
        base += [{"id": f"eco.gap.{k}", "value": v,
                  "unit": "шт" if k != "gap_pct" else "%",
                  "title": f"инвариант «заказ−продажи≈списания»: {k}",
                  "period": f"{d0} .. {d1}"} for k, v in gap.items()]

    reg.add_many("economics", SRC, base)
