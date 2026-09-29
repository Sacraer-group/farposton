# -*- coding: utf-8 -*-
"""Жизнь позиции на полке по дням: обе стороны промаха в одной строке.

Строка = точка × позиция × торговый день. Для каждой известно, чем день кончился:
позиция кончилась рано (недозаказ), остаток ушёл по вечерней скидке, остаток
списали (перезаказ), или всё сошлось.

ЕДИНСТВЕННАЯ реализация методики: её вызывают и блок фактов, и Excel, и ночной
прогон. Пороги — в params.py, скоуп — в scope.py, деньги — через economics.unit_costs.

ЧТО ЧЕМУ ВЕРИМ (проверено на данных 08.08.2026, дополнено фиксами 09.08.2026):
  · ВРЕМЯ продажи — только из чеков (mart.cheki_sales_net), другого источника нет;
    возвраты-сторно вычтены со знаком минус, но время последней продажи (t_last)
    берётся только по строкам продаж — возврат в 21:00 не значит, что товар
    стоял на полке;
  · КОЛИЧЕСТВО проданного и уценённого — из mart.fact_sales_daily: реконструкция
    уценки из чековых скидок покрывает лишь 75–85% витринного u40 с апреля,
    поэтому чеки для количества уценки не годятся;
  · КАРТОЧКИ-ПРИЗРАКИ склеены с живыми двойниками (scope.sku_twins) — заявка по
    старой карточке и продажи по новой считаются одной позицией;
  · СПИСАНИЯ — из mart.fact_writeoff_daily, но акты приходят с лагом ~7 дней:
    дни свежее max(wo_date) помечаются wo_settled=False и в годовые числа не идут;
  · ПРИВОЗ (fact_supply_daily) в расчёте НЕ участвует: он покрыт частично
    (в июле 8 943 из 11 565 строк «нет данных о привозе»), и классификация,
    зависящая от него, развалилась бы на половине года.

Деньги трёх видов, складывать вслепую нельзя:
  lost_rub_margin — недополученная маржа (0,67 цены) за то, чего не хватило;
  wo_rub_cost     — сгоревшая себестоимость (0,33 цены) за то, что выбросили;
  u40_rub_gap     — СПРАВОЧНО: разница между полной ценой и вечерней скидкой.
«Цена промаха» = первые два. Уценённая единица ПРОДАНА и принесла прибыль
(60 − 33 = +27 цены против −33 у списанной) — приплюсовать её к потерям значило бы
повторить двойной счёт Co, который закрыт в economics.py 03.08.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT                                       # noqa: E402
from prognoz.assortment import (filter_frame,                # noqa: E402
                                provenance as assortment_provenance)
from prognoz.facts.blocks.economics import unit_costs        # noqa: E402
from prognoz.scope import canon_cte, sku_filter, sku_twins_cte  # noqa: E402
from prognoz.shelf.params import OUTCOMES, Params            # noqa: E402

CACHE = OUTPUT / "shelf_daily.parquet"
H = "extract(hour FROM {t}) + extract(minute FROM {t}) / 60.0"

# Версия МЕТОДИКИ (не данных): входит в штамп кэша, чтобы правка логики сама
# инвалидировала кэш — иначе изменение кода при тех же параметрах вернёт старые
# числа молча. Поднимать при каждом содержательном изменении расчёта.
VERSION = "shelf-demo-v1"


def close_hours(con, p: Params) -> pd.DataFrame:
    """Час закрытия точки по МЕСЯЦАМ (95-й перцентиль последнего чека дня).

    Не по году: у Точки 1 закрытие дрейфует 23,6 → 22,5 → 23,4, а час
    разницы напрямую двигает границу «замолчала рано». Месяцы короче 10 дней
    падают на годовой перцентиль.
    """
    return con.execute(f"""
        WITH {canon_cte()},
        last_day AS (
            SELECT c.pt AS point_key, h.trade_day,
                   {H.format(t='max(h.ts)')} AS hh
            FROM (SELECT * FROM mart.cheki_headers
                  WHERE trade_day BETWEEN ? AND ?) h
            JOIN c ON c.point_key = h.point_key
            WHERE h.posted AND h.op = 'Продажа'
            GROUP BY 1, 2),
        m AS (
            SELECT point_key, date_trunc('month', trade_day) AS mon,
                   quantile_cont(hh, 0.95) AS close_m, count(*) AS n_days
            FROM last_day GROUP BY 1, 2),
        y AS (
            SELECT point_key, quantile_cont(hh, 0.95) AS close_y
            FROM last_day GROUP BY 1)
        SELECT m.point_key, m.mon,
               CASE WHEN m.n_days >= 10 THEN m.close_m ELSE y.close_y END AS close_h,
               m.n_days >= 10 AS close_monthly
        FROM m JOIN y USING (point_key)
    """, [p.since, p.until]).fetchdf()


def day_rows(con, p: Params) -> pd.DataFrame:
    """Основа: точка × позиция × день со всеми наблюдаемыми фактами дня.

    Ключи — ОБЪЕДИНЕНИЕ источников, а не пересечение: день, где были только
    списания (позиция кончилась вчера, выбросили сегодня), обязан попасть в таблицу.
    """
    wo_max = con.execute(
        "SELECT max(wo_date) FROM mart.fact_writeoff_daily WHERE wo_date <= ?",
        [p.until],
    ).fetchone()[0]
    if wo_max is None:
        wo_max = p.since - timedelta(days=1)
    lag = p.wo_window_days
    return con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()},
        ch AS (
            SELECT c.pt AS point_key, coalesce(tw.live, cs.sku_key) AS sku_key,
                   cs.trade_day AS d,
                   sum(cs.qty) AS sold_chk, sum(cs.amount) AS sold_rub,
                   count(*) FILTER (WHERE cs.sign > 0) AS n_lines,
                   min({H.format(t='cs.ts')}) FILTER (WHERE cs.sign > 0) AS t_first,
                   max({H.format(t='cs.ts')}) FILTER (WHERE cs.sign > 0) AS t_last,
                   min(CASE WHEN cs.sign > 0
                             AND cs.disc_manual > 0.35 * cs.price * cs.qty
                            THEN {H.format(t='cs.ts')} END) AS t_u40_first,
                   count(*) FILTER (WHERE cs.sign > 0
                             AND cs.disc_manual > 0.35 * cs.price * cs.qty)
                       AS u40_strict_lines
            FROM mart.cheki_sales_net cs
            JOIN c ON c.point_key = cs.point_key
            LEFT JOIN tw ON tw.ghost = cs.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, cs.sku_key)
            WHERE {sku_filter('s')}
              AND cs.trade_day BETWEEN ? AND ?
            GROUP BY 1, 2, 3),
        sl AS (
            SELECT c.pt AS point_key, coalesce(tw.live, f.sku_key) AS sku_key,
                   f.sale_date AS d,
                   sum(f.qty_sold - f.qty_returned) AS sold_units,
                   sum(f.u40) AS u40_units, sum(f.revenue) AS revenue
            FROM mart.fact_sales_daily f
            JOIN c ON c.point_key = f.point_key
            LEFT JOIN tw ON tw.ghost = f.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
            WHERE {sku_filter('s')} AND f.sale_date BETWEEN ? AND ?
            GROUP BY 1, 2, 3),
        wo AS (
            SELECT c.pt AS point_key, coalesce(tw.live, w.sku_key) AS sku_key,
                   w.wo_date AS d,
                   sum(w.qty_writeoff) AS wo_units
            FROM mart.fact_writeoff_daily w
            JOIN c ON c.point_key = w.point_key
            LEFT JOIN tw ON tw.ghost = w.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, w.sku_key)
            WHERE {sku_filter('s')} AND w.wo_date BETWEEN ? AND ?
            GROUP BY 1, 2, 3),
        od AS (
            SELECT c.pt AS point_key, coalesce(tw.live, o.sku_key) AS sku_key,
                   o.trade_date AS d,
                   sum(o.qty_ordered) AS ordered_human
            FROM mart.fact_orders_daily o
            JOIN c ON c.point_key = o.point_key
            LEFT JOIN tw ON tw.ghost = o.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, o.sku_key)
            WHERE {sku_filter('s')} AND o.channel = 'transfer'
              AND o.trade_date BETWEEN ? AND ?
            GROUP BY 1, 2, 3),
        keys AS (
            SELECT point_key, sku_key, d FROM ch
            UNION SELECT point_key, sku_key, d FROM sl
            UNION SELECT point_key, sku_key, d FROM wo
            UNION SELECT point_key, sku_key, d FROM od)
        SELECT k.point_key, k.sku_key, k.d AS trade_day,
               p.canon_name, s.name AS sku_name,
               regexp_replace(s.group_path, '^Меню / ', '') AS grp,
               coalesce(sl.sold_units, ch.sold_chk, 0) AS sold_units,
               coalesce(ch.sold_rub, sl.revenue, 0)    AS sold_rub,
               coalesce(sl.u40_units, 0)               AS u40_units,
               coalesce(wo.wo_units, 0)                AS wo_units,
               coalesce(od.ordered_human, 0)           AS ordered_human,
               ch.t_first, ch.t_last, ch.t_u40_first,
               coalesce(ch.n_lines, 0)                 AS n_lines,
               coalesce(ch.u40_strict_lines, 0)        AS u40_strict_lines,
               k.d <= DATE '{wo_max}' - {lag}          AS wo_settled
        FROM keys k
        LEFT JOIN ch USING (point_key, sku_key, d)
        LEFT JOIN sl USING (point_key, sku_key, d)
        LEFT JOIN wo USING (point_key, sku_key, d)
        LEFT JOIN od USING (point_key, sku_key, d)
        JOIN mart.dim_point p ON p.point_key = k.point_key
        JOIN mart.dim_sku   s ON s.sku_key   = k.sku_key
        WHERE p.canon_name IS NOT NULL
    """, [p.since, p.until] * 4).fetchdf()


def hourly_profile(con, p: Params) -> pd.DataFrame:
    """Часовой профиль пары: сколько штук в час она продаёт в обычный день.

    ⚠️ Существование профиля проверяется по самой паре, а не по «якорному часу»:
    в прежней версии стоял JOIN … AND pr.h = 12, и пары, ни разу не продавшиеся
    в полдень, молча выпадали из результата целиком (10 пар из 532).
    """
    return con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()},
        b AS (
            SELECT c.pt AS point_key, coalesce(tw.live, cs.sku_key) AS sku_key,
                   extract(hour FROM cs.ts) AS h, cs.trade_day AS d,
                   cs.qty, cs.price, cs.sign
            FROM mart.cheki_sales_net cs
            JOIN c ON c.point_key = cs.point_key
            LEFT JOIN tw ON tw.ghost = cs.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, cs.sku_key)
            WHERE {sku_filter('s')}
              AND cs.trade_day BETWEEN ? AND ?),
        d AS (
            SELECT point_key, sku_key, count(DISTINCT d) AS n_days,
                   avg(price) FILTER (WHERE sign > 0) AS avg_price
            FROM b GROUP BY 1, 2 HAVING count(DISTINCT d) >= {p.min_days})
        SELECT b.point_key, b.sku_key, b.h,
               sum(b.qty) / max(d.n_days) AS rate,
               max(d.n_days) AS n_days, max(d.avg_price) AS avg_price
        FROM b JOIN d USING (point_key, sku_key)
        GROUP BY 1, 2, 3
    """, [p.since, p.until]).fetchdf()


def sku_prices(con, p: Params) -> pd.DataFrame:
    """Фолбэк-цены для пар без часового профиля (реже 30 дней жизни).

    Без них списанные штуки редких позиций публикуются как 0 ₽ — штуки в отчёте
    есть, деньги нет (находка 3 панели проверки). Каскад: средняя базовая цена
    SKU по сети из чеков → цена из витрины продаж (revenue_gross/qty — тоже
    базовая, до скидки).
    """
    chk = con.execute(f"""
        WITH {sku_twins_cte()}
        SELECT coalesce(tw.live, cs.sku_key) AS sku_key,
               avg(cs.price) FILTER (WHERE cs.sign > 0) AS px_sku_net
        FROM mart.cheki_sales_net cs
        LEFT JOIN tw ON tw.ghost = cs.sku_key
        WHERE cs.trade_day BETWEEN ? AND ?
        GROUP BY 1
    """, [p.since, p.until]).fetchdf()
    dly = con.execute(f"""
        WITH {sku_twins_cte()}
        SELECT coalesce(tw.live, f.sku_key) AS sku_key,
               sum(f.revenue_gross) / nullif(sum(f.qty_sold), 0) AS px_daily
        FROM mart.fact_sales_daily f
        LEFT JOIN tw ON tw.ghost = f.sku_key
        WHERE f.sale_date BETWEEN ? AND ?
        GROUP BY 1
    """, [p.since, p.until]).fetchdf()
    return chk.merge(dly, on="sku_key", how="outer")


def classify(df: pd.DataFrame) -> pd.Series:
    """Исход дня. Приоритет фиксирован и объясним словами:

      списали              → «остаток списан» (скидка не спасла, товар выброшен)
      уценили ВЕЧЕРОМ      → «остаток ушёл в уценку» (вечером товар был на полке)
      продаж нет           → «не продавалось»
      замолчала рано       → «кончилось рано» (кандидат в дефицит)
      иначе                → «распродано под закрытие»

    Гейт уценки — только вечерняя С НАБЛЮДАЕМЫМ ВРЕМЕНЕМ (находка 5 панели
    проверки 08.08). u40 — не факт из кассы, а реконструкция-верхняя оценка из
    сумм скидок: дневные дробные «у40» — скидочные программы −10%, а не
    распродажа. Позиция могла кончиться в 15:00, но дневная полпорции со скидкой
    снимала ей исход «кончилось рано». Вечерняя = первая у40-строка чека (скидка
    >35%) не раньше close_h − markdown_evening_h. Если такой строки в чеках нет
    вовсе (u40_strict = 0) — по выводу панели «уценки могло не быть вовсе»
    (59,9% значимых дней): НЕ гейтит. Остаточный риск (чеки недосчитывают
    скидки с апреля) прикрыт порогом значимости и маржой хвоста 0,27 в build().

    День с уценкой И списанием идёт в «списан»: часть остатка всё равно выброшена.
    Флаги had_markdown/had_writeoff остаются в таблице отдельно, чтобы пересечение
    восстанавливалось без пересчёта.
    """
    out = pd.Series("распродано под закрытие", index=df.index, dtype=object)
    early = df["t_last"].notna() & (df["t_last"] < df["close_h"] - df["early_gap_h"])
    out[early] = "кончилось рано"
    out[df["sold_units"] <= 0] = "не продавалось"
    # Порог именно >0, а не >=0.5: даже дробная ВЕЧЕРНЯЯ уценка значит, что вечером
    # товар физически был на полке — день не дефицитный. Порог 0,5 давал +6,6 тыс шт
    # фантомного дефицита на дробных списаниях (проверено при сверке со старым
    # расчётом 08.08).
    evening_u40 = (df["t_u40_first"].notna()
                   & (df["t_u40_first"]
                      >= df["close_h"] - df["markdown_evening_h"]))
    # Дни вовсе без чековых строк (сеты: чеки разузлованы в компоненты) времени
    # не имеют — там уценка гейтит как раньше; на lost это не влияет (без t_last
    # день не бывает «кончилось рано»).
    no_chek = df["t_last"].isna()
    out[(df["u40_units"] > 1e-6) & (evening_u40 | no_chek)] = "остаток ушёл в уценку"
    out[df["wo_units"] > 1e-6] = "остаток списан"
    return out


def flag_supply_gaps(df: pd.DataFrame, min_skus: int = 5) -> pd.Series:
    """Точко-дни массовых нулей: «заявка была, продаж ноль» сразу у ≥N позиций.

    Один-два молчащих SKU — карточка или случайность; пять и больше разом — срыв
    поставки или кассы. Порог 5 воспроизводит панельные «183 кластера за год»
    (при 5 выходит 185 точко-дней, оценка ~3,4 тыс шт — внутри вилки панели
    2,5–5,4 тыс). Такие дни — операционный сигнал клиенту отдельной строкой:
    в деньги промаха не входят (спрос дня неизвестен), из бэктеста исключаются.
    """
    silent = (df["outcome"] == "не продавалось") & (df["ordered_human"] > 0)
    per_day = df[silent].groupby(["point_key", "trade_day"]).size()
    gap_keys = set(per_day[per_day >= min_skus].index)
    idx = pd.MultiIndex.from_frame(df[["point_key", "trade_day"]])
    return pd.Series(idx.isin(gap_keys), index=df.index)


def last_complete_day(df: pd.DataFrame):
    """Последний день, за который по сети есть чековые строки или продажи
    (pd.Timestamp) — граница загруженных данных; None, если данных нет вовсе."""
    if df.empty:
        return None
    has_data = pd.Series(False, index=df.index)
    for col in ("n_lines", "sold_units"):
        if col in df.columns:
            has_data |= pd.to_numeric(df[col], errors="coerce").fillna(0) > 0
    if not has_data.any():
        return None
    return pd.to_datetime(df.loc[has_data, "trade_day"]).max()


def flag_data_missing(df: pd.DataFrame) -> pd.Series:
    """Строки за дни после границы загруженных данных: спрос там НЕИЗВЕСТЕН
    (не ноль). Такие строки дают заявки на перемещение, выписанные на день вперёд."""
    edge = last_complete_day(df)
    if edge is None:
        return pd.Series(False, index=df.index)
    return pd.to_datetime(df["trade_day"]) > edge


def build(con, p: Params | None = None) -> pd.DataFrame:
    """Собрать таблицу «жизнь позиции по дням» за окно параметров."""
    p = p or Params()
    co, cu = unit_costs()
    df = day_rows(con, p)
    prof = hourly_profile(con, p)

    ch = close_hours(con, p)
    df["mon"] = pd.to_datetime(df["trade_day"]).dt.to_period("M").dt.to_timestamp()
    df = df.merge(ch, left_on=["point_key", "mon"], right_on=["point_key", "mon"],
                  how="left")
    df["early_gap_h"] = p.early_gap_h
    df["markdown_evening_h"] = p.markdown_evening_h
    df["outcome"] = classify(df)
    df["supply_gap"] = flag_supply_gaps(df)
    # Дни ПОСЛЕ последнего дня с чеками/продажами — «данных ещё нет», а не «не
    # продавалось»: заявки на перемещение приходят на день вперёд, продажи за него
    # догружаются следующей ночью. Без этой границы каждый такой день считался
    # разрывом поставки у всех точек (≥5 SKU с заявкой и нулём продаж), и ядро
    # теряло lag_recent у 100% пар — фолбэк на формулу 13–17.09.
    df["data_missing"] = flag_data_missing(df)
    df.loc[df["data_missing"], "supply_gap"] = False

    # Недопродажа: ожидаемые продажи за часы после того, как позиция замолчала.
    # Профиль включает и дефицитные дни (их вечер нулевой), поэтому скорость
    # занижена, а оценка — нижняя граница, а не верхняя.
    tail = (prof.merge(df[["point_key", "sku_key", "trade_day", "t_last", "close_h"]],
                       on=["point_key", "sku_key"])
                .query("h > t_last and h <= close_h")
                .groupby(["point_key", "sku_key", "trade_day"])["rate"].sum()
                .rename("lost_raw").reset_index())
    df = df.merge(tail, on=["point_key", "sku_key", "trade_day"], how="left")
    prices = prof.groupby(["point_key", "sku_key"])["avg_price"].max().reset_index()
    df = df.merge(prices, on=["point_key", "sku_key"], how="left")
    # дневная скорость пары (шт/день по профилю) — для оценки срывов поставки
    day_rate = (prof.groupby(["point_key", "sku_key"])["rate"].sum()
                    .rename("day_rate").reset_index())
    df = df.merge(day_rate, on=["point_key", "sku_key"], how="left")

    # Каскад цен: пара без профиля не должна давать 0 ₽ в деньгах списаний
    px = sku_prices(con, p)
    px_sku = px.set_index("sku_key")["px_sku_net"]
    px_dly = px.set_index("sku_key")["px_daily"]
    df["price_source"] = np.select(
        [df["avg_price"].notna(),
         df["sku_key"].map(px_sku).notna(),
         df["sku_key"].map(px_dly).notna()],
        ["пара", "SKU по сети", "витрина продаж"], default="нет")
    df["avg_price"] = (df["avg_price"]
                       .fillna(df["sku_key"].map(px_sku))
                       .fillna(df["sku_key"].map(px_dly)))

    df["lost_raw"] = df["lost_raw"].fillna(0.0)
    deficit = df["outcome"] == "кончилось рано"
    df["lost_units"] = df["lost_raw"].where(deficit, 0.0)
    # Порог значимости: молчание редкой позиции — пуассон, а не пустая полка
    df["lost_zeroed"] = df["lost_units"].where(df["lost_units"] < p.signif_units, 0.0)
    df.loc[df["lost_units"] < p.signif_units, "lost_units"] = 0.0

    # Маржа хвоста: в день дефицита, где ДНЁМ была уценка (гейт её не снял),
    # недопроданные вечером штуки ушли бы со скидкой → 0,27 цены, а не 0,67
    cu_eff = np.where(deficit & (df["u40_units"] > 1e-6), p.markdown_margin, cu)
    df["lost_rub_margin"] = df["lost_units"] * df["avg_price"] * cu_eff
    df["wo_rub_cost"] = df["wo_units"] * df["avg_price"] * co
    df["u40_rub_gap"] = df["u40_units"] * df["avg_price"] * 0.40   # справочно
    df["miss_rub"] = df["lost_rub_margin"] + df["wo_rub_cost"]
    df["had_markdown"] = df["u40_units"] > 1e-6
    df["had_writeoff"] = df["wo_units"] > 1e-6
    return df.drop(columns=["mon"])


def load_or_build(con, p: Params | None = None, *, cache: Path = CACHE) -> pd.DataFrame:
    """Кэш со штампом: параметры + свежесть данных. Разошлось — пересчёт.

    Так Excel и панель не могут показать разные числа: обе читают один кэш,
    а устаревший кэш не отдаётся молча.
    """
    p = p or Params()
    policy = assortment_provenance()
    stamp = "{}|{}|{}|{}|{}".format(
        VERSION, p.fingerprint(),
        con.execute("SELECT max(trade_day) FROM mart.cheki_sales").fetchone()[0],
        con.execute("SELECT max(wo_date) FROM mart.fact_writeoff_daily").fetchone()[0],
        policy["assortment_policy_sha256"])
    meta = cache.with_suffix(".stamp")
    if cache.exists() and meta.exists() and meta.read_text(encoding="utf-8") == stamp:
        return filter_frame(
            pd.read_parquet(cache), "metric_scope", date_col="trade_day")
    df = filter_frame(build(con, p), "metric_scope", date_col="trade_day")
    df.to_parquet(cache, index=False)
    meta.write_text(stamp, encoding="utf-8")
    return df


def summarize(df: pd.DataFrame, p: Params | None = None) -> dict:
    """Годовые итоги. Считаются ТОЛЬКО по дням с пришедшими актами списания."""
    p = p or Params()
    s = df[df["wo_settled"]]
    n = len(s)
    out = {
        "rows": n,
        "pairs": s.groupby(["point_key", "sku_key"]).ngroups,
        "sold_units": s["sold_units"].sum(),
        "sold_rub_m": s["sold_rub"].sum() / 1e6,
        "lost_units": s["lost_units"].sum(),
        "lost_rub_m": s["lost_rub_margin"].sum() / 1e6,
        "wo_units": s["wo_units"].sum(),
        "wo_rub_m": s["wo_rub_cost"].sum() / 1e6,
        "u40_units": s["u40_units"].sum(),
        "u40_rub_m": s["u40_rub_gap"].sum() / 1e6,
        "miss_rub_m": s["miss_rub"].sum() / 1e6,
        "lost_zeroed_units": s["lost_zeroed"].sum(),
        "prelim_days": int((~df["wo_settled"]).sum()),
        # срывы поставки: точко-дни массовых нулей — отдельной строкой, не в деньги
        "supply_gap_days": int(s[s["supply_gap"]]
                               .groupby(["point_key", "trade_day"]).ngroups),
        "supply_gap_units_est": s.loc[
            s["supply_gap"] & (s["outcome"] == "не продавалось")
            & (s["ordered_human"] > 0), "day_rate"].sum(),
    }
    for o in OUTCOMES:
        out[f"share.{o}"] = 100 * (s["outcome"] == o).sum() / n if n else 0.0
    out["early_significant_share"] = 100 * (s["lost_units"] > 0).sum() / n if n else 0.0
    for o in OUTCOMES:
        sub = s[s["outcome"] == o]
        out[f"hour.{o}"] = sub["t_last"].mean() if len(sub) else float("nan")
    out["hour.gap"] = (out["hour.остаток ушёл в уценку"] - out["hour.кончилось рано"])
    return out
