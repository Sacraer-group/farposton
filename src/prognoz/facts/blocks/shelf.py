# -*- coding: utf-8 -*-
"""Блок «полка»: жизнь позиции по дням — обе стороны промаха заказа.

Числа рождаются в prognoz.shelf.core (единственная реализация методики) и здесь
только оформляются в факты и таблицы. Панель и документы читают их из реестра.
"""
from __future__ import annotations

import pandas as pd

from prognoz.assortment import filter_frame
from prognoz.facts.registry import block
# ⚠️ shelf.core импортируется ЛЕНИВО внутри compute(): core сам использует
# economics.unit_costs, а импорт economics исполняет blocks/__init__, который
# импортирует этот файл — на уровне модуля получилась бы петля.
from prognoz.shelf.params import OUTCOMES, Params

SRC = "facts.blocks.shelf:compute"

# короткие английские суффиксы для id фактов
OUT_KEY = {
    "остаток списан": "writeoff",
    "остаток ушёл в уценку": "markdown",
    "не продавалось": "silent",
    "кончилось рано": "early",
    "распродано под закрытие": "clean",
}

METHOD_LOST = (
    "время последней продажи дня (чеки r_keeper, возвраты-сторно вычтены, время — "
    "только по строкам продаж) × часовой профиль пары за год (пары с ≥30 днями "
    "жизни; день жизни = день с ≥1 продажей, не календарный span); интеграл по "
    "ЦЕЛЫМ часам — от следующего часа после t_last по час закрытия включительно; "
    "потеря засчитывается только дню «кончилось рано»: списания нет, ВЕЧЕРНЕЙ "
    "уценки нет (первая у40-строка чека раньше close−3ч — не блокер: дневная "
    "дробная у40 — скидочная программа, а не распродажа), и за остаток вечера "
    "ожидалось ≥2 шт; профиль включает дефицитные дни, поэтому оценка — "
    "НИЖНЯЯ граница; карточки-призраки склеены с живыми двойниками (sku_twins)"
)
METHOD_MONEY = (
    "деньги через среднюю БАЗОВУЮ цену (до скидки): недопродажа × 0,67 маржи "
    "(в день дефицита с дневной уценкой × 0,27 — хвост ушёл бы со скидкой −40%), "
    "списание × 0,33 (сгоревшая себестоимость); пары без профиля получают "
    "фолбэк-цену SKU по сети — списанные штуки редких позиций не дают 0 ₽; "
    "уценка НЕ входит в цену промаха — уценённая единица продана и принесла "
    "прибыль (+27% цены против −33% у списанной)"
)


@block("shelf")
def compute(ctx, reg) -> None:
    from prognoz.shelf.core import load_or_build, summarize
    p = Params()
    # shelf_daily.parquet остаётся неизменяемым исходным кэшем. Собственное
    # производство убираем из read-view, чтобы те же строки не возвращались в
    # факты, графики и Excel после исключения из заявки и оценки модели.
    df = filter_frame(
        load_or_build(ctx.con, p), "metric_scope", date_col="trade_day")
    s = summarize(df, p)
    period = p.period_ru
    settled = df[df["wo_settled"]]

    facts = [
        {"id": "shelf.year.rows", "value": int(s["rows"]), "unit": "дней-позиций",
         "title": "строк точка×позиция×день за год", "period": period},
        {"id": "shelf.year.pairs", "value": int(s["pairs"]), "unit": "пар",
         "title": "активных пар точка×позиция", "period": period},
        {"id": "shelf.year.sold_units", "value": round(s["sold_units"]),
         "unit": "шт", "title": "продано за год (скоуп прогноза, дни с актами)",
         "period": period},
        {"id": "shelf.year.lost_units", "value": round(s["lost_units"]), "unit": "шт",
         "title": "недопродано из-за раннего исчерпания", "period": period,
         "method": METHOD_LOST},
        {"id": "shelf.year.human_underorder_pct",
         "value": round(100 * s["lost_units"]
                        / (s["sold_units"] + s["lost_units"]), 1),
         "unit": "%",
         "title": "недозаказ человека за год: доля восстановленного спроса, "
                  "которую не удовлетворили", "period": period,
         "method": "lost / (sold + lost); весь год ДО точки возили по заявкам "
                   "людей, поэтому это показатель человека; НИЖНЯЯ граница — "
                   "см. lost_zeroed и supply_gap"},
        {"id": "shelf.year.lost_rub_m", "value": round(s["lost_rub_m"], 1),
         "unit": "млн ₽", "title": "потерянная маржа недопродаж", "period": period,
         "method": METHOD_MONEY},
        {"id": "shelf.year.wo_units", "value": round(s["wo_units"]), "unit": "шт",
         "title": "списано (акты 1С, скоуп прогноза)", "period": period,
         "method": "fact_writeoff_daily; включает питание сотрудников и порчу "
                   "при приёмке — не всё сокращается точным заказом"},
        {"id": "shelf.year.wo_rub_m", "value": round(s["wo_rub_m"], 1),
         "unit": "млн ₽", "title": "сгоревшая себестоимость списаний",
         "period": period, "method": METHOD_MONEY},
        {"id": "shelf.year.u40_units", "value": round(s["u40_units"]), "unit": "шт",
         "title": "ушло по вечерней скидке", "period": period,
         "method": "СПРАВОЧНО, не потеря: уценённая единица продана с прибылью "
                   "+27% цены; скидка — инструмент спасения остатка (решение "
                   "клиента 08.08)"},
        {"id": "shelf.year.miss_rub_m", "value": round(s["miss_rub_m"], 1),
         "unit": "млн ₽", "title": "цена промаха заказа за год "
         "(недопродажи + списания, БЕЗ уценки)", "period": period,
         "method": METHOD_MONEY},
        {"id": "shelf.outcome.early_significant_share",
         "value": round(s["early_significant_share"], 1), "unit": "%",
         "title": "дней-позиций со ЗНАЧИМЫМ дефицитом", "period": period,
         "method": "ранний обрыв продаж, подтверждённый ожиданием ≥2 шт за остаток "
                   "вечера; заголовочное число — сырые 38% «ранних» дней без порога "
                   "содержат пуассоновский шум редких позиций"},
        {"id": "shelf.hour.gap", "value": round(s["hour.gap"], 1), "unit": "ч",
         "title": "разрыв: час затишья дефицитных дней против дней с уценкой",
         "period": period,
         "method": "самопроверка разметки: если бы «кончилось рано» было шумом, "
                   "разрыва бы не было; наблюдаемый ≈4 часа"},
        {"id": "shelf.meta.prelim_days", "value": int(s["prelim_days"]),
         "unit": "дней-позиций",
         "title": "свежие дни без актов списания (исход предварительный)",
         "period": period,
         "method": "акты приходят с лагом ~7 дней; эти дни в годовые числа не входят"},
        {"id": "shelf.meta.lost_zeroed_units",
         "value": round(s["lost_zeroed_units"]), "unit": "шт",
         "title": "недопродажа, обнулённая порогом значимости", "period": period,
         "method": "честный остаток: столько штук ожидалось в днях, где молчание "
                   "статистически неотличимо от нормы (ожидание <2 шт)"},
        {"id": "shelf.year.supply_gap_days", "value": int(s["supply_gap_days"]),
         "unit": "точко-дней",
         "title": "срывы поставки: дни массовых нулей (≥5 позиций «заявка была, "
                  "продаж ноль» разом)", "period": period,
         "method": "операционный сигнал ОТДЕЛЬНОЙ строкой: спрос этих дней "
                   "неизвестен, в деньги промаха не входит, из бэктеста "
                   "исключается"},
        {"id": "shelf.year.supply_gap_units_est",
         "value": round(s["supply_gap_units_est"]), "unit": "шт",
         "title": "оценка непроданного в дни срывов поставки", "period": period,
         "method": "сумма дневных скоростей молчавших пар (часовой профиль за "
                   "год) по точко-дням массовых нулей; справочно, не в деньгах"},
        {"id": "shelf.params.early_gap_h", "value": p.early_gap_h, "unit": "ч",
         "title": "порог «замолчала рано» до закрытия", "period": period},
        {"id": "shelf.params.markdown_evening_h", "value": p.markdown_evening_h,
         "unit": "ч",
         "title": "окно «вечерней» уценки до закрытия (гейт дефицита)",
         "period": period},
        {"id": "shelf.params.signif_units", "value": p.signif_units, "unit": "шт",
         "title": "порог значимости дефицита", "period": period},
        {"id": "shelf.params.min_days", "value": p.min_days, "unit": "дн.",
         "title": "минимум дней жизни пары для профиля", "period": period},
    ]
    for o in OUTCOMES:
        facts.append({
            "id": f"shelf.outcome.{OUT_KEY[o]}_share",
            "value": round(s[f"share.{o}"], 1), "unit": "%",
            "title": f"доля дней-позиций: {o}", "period": period})

    # --- таблицы для панели и Excel ---
    settled = settled.copy()
    settled["mon"] = pd.to_datetime(settled["trade_day"]).dt.strftime("%Y-%m")

    by_month = settled.groupby("mon").agg(
        lost_units=("lost_units", "sum"),
        lost_rub_k=("lost_rub_margin", lambda x: x.sum() / 1e3),
        wo_units=("wo_units", "sum"),
        wo_rub_k=("wo_rub_cost", lambda x: x.sum() / 1e3),
        u40_units=("u40_units", "sum"),
        sold_units=("sold_units", "sum")).round(1).reset_index()
    reg.table("shelf.by_month", by_month,
              title="Обе стороны промаха по месяцам (сеть переменного состава: "
                    "2 точки в начале года → 5 в конце)")

    outc = settled.groupby("outcome").agg(
        rows=("outcome", "size"),
        t_last_avg=("t_last", "mean"),
        sold_avg=("sold_units", "mean")).reset_index()
    outc["share_pct"] = (100 * outc["rows"] / len(settled)).round(1)
    reg.table("shelf.outcomes", outc.round(1), title="Чем кончается день позиции")

    early = settled[(settled["outcome"] == "кончилось рано") &
                    (settled["lost_units"] > 0)].copy()
    early["hour"] = early["t_last"].fillna(0).astype(int)
    by_hour = early.groupby("hour").agg(
        days=("hour", "size"), lost_units=("lost_units", "sum")).round(0).reset_index()
    reg.table("shelf.by_hour", by_hour,
              title="В какой час позиции замолкают в дни значимого дефицита")

    top = settled.groupby(["canon_name", "sku_name"]).agg(
        lost_rub_k=("lost_rub_margin", lambda x: x.sum() / 1e3),
        wo_rub_k=("wo_rub_cost", lambda x: x.sum() / 1e3),
        lost_units=("lost_units", "sum"),
        wo_units=("wo_units", "sum"),
        deficit_days=("lost_units", lambda x: (x > 0).sum()),
        days=("trade_day", "nunique")).reset_index()
    top["miss_rub_k"] = top["lost_rub_k"] + top["wo_rub_k"]
    top = top.nlargest(30, "miss_rub_k").round(1)
    reg.table("shelf.top_pairs", top, title="Топ-30 пар по цене промаха")

    by_point = settled.groupby("canon_name").agg(
        sold_units=("sold_units", "sum"),
        lost_units=("lost_units", "sum"),
        lost_rub_m=("lost_rub_margin", lambda x: x.sum() / 1e6),
        wo_units=("wo_units", "sum"),
        wo_rub_m=("wo_rub_cost", lambda x: x.sum() / 1e6)).round(2).reset_index()
    by_point["miss_rub_m"] = (by_point["lost_rub_m"] + by_point["wo_rub_m"]).round(2)
    reg.table("shelf.by_point", by_point, title="Обе потери по точкам")

    reg.add_many("shelf", SRC, facts)
