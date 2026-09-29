# -*- coding: utf-8 -*-
"""Прод-прогноз: проект заявки «точка × позиция» на D+2/D+3/D+4.

Отличие от бэктеста: харнесс берёт список строк из фактической заявки человека, а в бою
никто заранее не говорит, какие позиции заказывать. Поэтому здесь два шага, а не один:
сначала ассортиментная матрица (что вообще возить на эту точку), потом количество.

Расчёт — донастроенная базовая методика (engine/improved.py), логика автора сохранена:
одноимённые дни недели, веса свежести, сравнение проданного с заказанным. Сверху три вещи,
которых в методике нет и без которых она в бою разваливается:

1. УСАДКА К ПРОФИЛЮ ПОЗИЦИИ. Если у пары «точка × позиция» нет четырёх одноимённых дней
   истории, среднее по ним даёт ноль или случайное число. Подмешиваем средние продажи
   позиции за 28 дней, приведённые к дню недели. Это же решает холодный старт новой точки.

2. СТОПОР КВП. Вечерняя распродажа −40% вычищает остаток, продано становится равно
   привезённому, методика читает это как «спрос выше» и растит заказ — остаток растёт,
   скидка съедает больше. Контур замыкается. Если позицию за 28 дней вычищала уценка,
   поправка вверх не применяется.

3. ОГРАНИЧЕНИЕ ШАГА. Заявка не уходит дальше чем на ±15% от того, что человек заказывал
   в этот день недели, и не прыгает больше чем на 30% + 1 шт от прошлого раза.
   В теневом режиме это страховка от собственной ошибки, а не от человека.

Погода в расчёт НЕ входит: проверено на этих данных — вклад в дневной итог 1,8–6,1%
объяснённой дисперсии, средняя ошибка не улучшается. Остаётся полем в логе.

Запуск:
    PYTHONUTF8=1 py prognoz/src/prognoz/predict/daily.py                 # на завтра
    PYTHONUTF8=1 py prognoz/src/prognoz/predict/daily.py --date=2026-08-03
    PYTHONUTF8=1 py prognoz/src/prognoz/predict/daily.py --date=2026-07-29 --dry
Выход: analysis/output/Заявка_<дата>.xlsx + лог analysis/output/forecast_log/<дата>.parquet
"""
import hashlib
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT, connect_reader                      # noqa: E402
from prognoz.engine.improved import forecast as forecast_v2  # noqa: E402
from prognoz.external import calendar_effects                # noqa: E402
from prognoz.assortment import (filter_frame, filter_pairs,  # noqa: E402
                                sql_exclusion_predicate)
from prognoz.predict.demand import BuildCfg, uplift_lookup   # noqa: E402
from prognoz.scope import (GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,  # noqa: E402
                           canon_cte, sku_twins_cte)
WEEK_LAGS = (7, 14, 21, 28)
ACTIVE_DAYS = 28              # окно активности пары «точка × позиция»
HORIZON_DAYS = 2              # на сколько дней вперёд считаем по умолчанию
HORIZON_LAGS = {2: 3, 3: 4, 4: 5}  # утром issue видим завершённые данные по issue−1
STALE_EXTRA = 1               # при D+2 разрыв с данными на день больше — это норма
WD_CLIP = (0.70, 1.40)        # коэффициент дня недели не выходит за эти рамки
SHRINK_K = 1.5                # вес профиля позиции против одноимённых дней
U40_LOCK_SHARE = 0.35         # выше этой доли уценки поправка КВП вверх блокируется
CLIP_TO_HUMAN = (0.85, 1.15)
STEP_REL, STEP_ABS = 0.30, 1.0
DAY_CLIP = (0.85, 1.12)       # рамка дневного итога точки вокруг того же дня недели
STALE_DAYS = 2                # если данные старше — флаг в файле
# Правило нулей (08.09, малый риск): пара с историей, у которой за 28 дней ни
# продаж, ни потерь полки, в заявку не идёт. На выпуске 2026-09-09 под условие
# «продано 0 за 28 дн» попадали 2 строки из 425 (0,5%) — включено по умолчанию,
# выключатель PROGNOZ_ZERO_RULE=0.
ZERO_RULE_MIN_OBS_DAYS = 14   # меньше — новинка, правило не трогает
ZERO_RULE_NOTE = "нет спроса 28 дн."
# Правило медленных позиций (10.09, встреча с ответственным сотрудником: «3 упаковки за 27 дней, а он
# прогнозирует по несколько в день»). Квантиль 0,70 на редких продажах даёт 1–3 шт/день,
# то есть перевоз в разы. Пока спрос за 28 дней ≤ SLOW_RULE_MAX_D28, дневная заявка не выше
# max(1, ceil(D28/7)) — «не больше недельного объёма». Выключатель PROGNOZ_SLOW_RULE=0.
SLOW_RULE_MAX_D28 = 14.0
SLOW_RULE_MIN_OBS_DAYS = 14
# Пол новинок (уровень сети / старт 8) — только для позиций, новых ДЛЯ СЕТИ: первая продажа
# где угодно не старше NOVELTY_NETWORK_DAYS или продаж нет нигде. Старая позиция, которую
# точка только начала заказывать, пол не получает (10.09: «Купаты… минимум 5 заказывает»).
NOVELTY_NETWORK_DAYS = 21
VERSION = "forecast-demo-v1"
WD_NAMES = ("понедельника", "вторника", "среды", "четверга",
            "пятницы", "субботы", "воскресенья")


def resolve_release_context(args: list[str], *,
                            today: date | None = None) -> tuple[date, date, int, int]:
    """Return ``(issue_date, target, horizon_days, feature_lag)``.

    One nightly issue contains D+2, D+3 and D+4.  The last complete sales day is
    issue−1, hence the causal feature lag is always horizon+1.  Explicit CLI
    values are checked against each other instead of silently drifting.
    """
    issue_date = today or date.today()
    target: date | None = None
    horizon = HORIZON_DAYS
    feature_lag: int | None = None
    issue_explicit = False
    for arg in args:
        if arg.startswith("--date="):
            target = date.fromisoformat(arg.split("=", 1)[1])
        elif arg.startswith("--issue-date="):
            issue_date = date.fromisoformat(arg.split("=", 1)[1])
            issue_explicit = True
        elif arg.startswith("--horizon="):
            horizon = int(arg.split("=", 1)[1])
        elif arg.startswith("--lag="):
            feature_lag = int(arg.split("=", 1)[1])
    if horizon not in HORIZON_LAGS:
        raise ValueError("Поддерживаются только горизонты D+2, D+3 и D+4")
    expected_lag = HORIZON_LAGS[horizon]
    if feature_lag is None:
        feature_lag = expected_lag
    if feature_lag != expected_lag:
        raise ValueError(
            f"Для D+{horizon} нужен честный lag={expected_lag}, получен {feature_lag}")
    if target is None:
        target = issue_date + timedelta(days=horizon)
    elif not issue_explicit:
        # Исторический ручной --date по прежнему означает обычный выпуск D+2,
        # если пользователь не передал дату выпуска отдельно.
        issue_date = target - timedelta(days=horizon)
    expected_target = issue_date + timedelta(days=horizon)
    if target != expected_target:
        raise ValueError(
            f"Цель {target} не равна issue {issue_date} + D+{horizon} ({expected_target})")
    return issue_date, target, horizon, feature_lag


def load_excluded_days() -> set[date]:
    """Нетипичные дни из analysis/config/excluded_days.json — тот же файл, что у ядра."""
    from prognoz.predict.ml_core import load_excluded        # noqa: PLC0415
    return load_excluded()


def zero_rule_enabled() -> bool:
    """PROGNOZ_ZERO_RULE: любое значение, кроме «0», — правило включено."""
    return os.environ.get("PROGNOZ_ZERO_RULE", "1").strip() != "0"


def zero_demand_note(qty: int, *, sold28: float, lost28: float | None,
                     n_obs_days: int | None) -> str | None:
    """Пост-правило после ML/формулы: вернуть текст пояснения, если строку надо
    обнулить, иначе None.

    Условия: восстановленный спрос за 28 дней = 0 (продано 0 и потерь полки 0;
    потери неизвестны — только продано 0), пара не новинка (n_obs_days ≥ 14;
    глубина истории неизвестна — правило не применяется) и qty > 0.
    Признака «привоза» у строки нет: пара попадает в заявку только при заявках
    за 28 дней, так что условие «нет привоза» обнулило бы само правило.
    """
    if qty <= 0 or not zero_rule_enabled():
        return None
    if n_obs_days is None or int(n_obs_days) < ZERO_RULE_MIN_OBS_DAYS:
        return None
    if float(sold28 or 0.0) > 0:
        return None
    if lost28 is not None and float(lost28) > 0:
        return None
    return ZERO_RULE_NOTE


def slow_rule_enabled() -> bool:
    """PROGNOZ_SLOW_RULE: любое значение, кроме «0», — правило включено."""
    return os.environ.get("PROGNOZ_SLOW_RULE", "1").strip() != "0"


def slow_demand_cap(qty: int, *, sold28: float, lost28: float | None,
                    n_obs_days: int | None) -> tuple[int, str | None]:
    """Потолок для медленной позиции. Возвращает (qty, пояснение|None).

    Спрос за 28 дней D28 = продано + потери полки (потери неизвестны — только продано).
    Условия: пара с историей (n_obs_days ≥ 14), 0 < D28 ≤ 14, qty выше потолка
    max(1, ceil(D28 / 7)).
    """
    if qty <= 0 or not slow_rule_enabled():
        return qty, None
    if n_obs_days is None or int(n_obs_days) < SLOW_RULE_MIN_OBS_DAYS:
        return qty, None
    d28 = float(sold28 or 0.0) + float(lost28 or 0.0)
    if d28 <= 0 or d28 > SLOW_RULE_MAX_D28:
        return qty, None
    cap = max(1, int(np.ceil(d28 / 7.0)))
    if qty <= cap:
        return qty, None
    return cap, f"медленная позиция: {d28:g} шт за 28 дн — не больше {cap} в день"


def network_new_skus(sales: pd.DataFrame, target: date) -> set:
    """Позиции, новые для сети: первая продажа где угодно не старше NOVELTY_NETWORK_DAYS.
    Позиции без продаж нигде сюда не входят — их пол «старт 8» решается отдельно."""
    if sales.empty or "sku_key" not in sales.columns:
        return set()
    first = sales.groupby("sku_key")["d"].min()
    edge = target - timedelta(days=NOVELTY_NETWORK_DAYS)
    return {sku for sku, d0 in first.items() if d0 >= edge}


def production_guard_cfg(model_core, model_lag: int):
    """Build the fail-closed production config for the frozen shared bundle.

    The scheduler must never silently replace the approved D+2/D+3/D+4 bundle
    with a newly trained HGB model.  If the bundle is missing, stale or invalid,
    the staged run fails validation and the previous immutable release stays live.
    """
    return model_core.GuardCfg(lag=model_lag, allow_hgb_fallback=False)


def load(con, upto: date):
    """Панели продаж и заявок по маркетам, только готовая продукция, строго до upto.

    Карточки-призраки склеены с живыми двойниками (scope.sku_twins): заявка по
    старой карточке иначе невидима расчёту — рвётся КВП (продано/заказано) и
    рамка к заявке человека ровно там, где сотрудник реально заказывал.
    """
    sales = con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()}
        SELECT c.pt AS point_key, coalesce(tw.live, f.sku_key) AS sku_key,
               f.sale_date AS d,
               sum(f.qty_sold - f.qty_returned) AS qty,
               sum(f.u40) AS u40, sum(f.revenue) AS revenue
        FROM mart.fact_sales_daily f
        JOIN c ON c.point_key = f.point_key
        LEFT JOIN tw ON tw.ghost = f.sku_key
        JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
        WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ? AND s.group_path NOT LIKE ? AND f.sale_date < ?
        GROUP BY 1, 2, 3
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR, upto]).fetchdf()

    orders = con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()},
        o AS (
            SELECT c.pt AS point_key, coalesce(tw.live, f.sku_key) AS sku_key,
                   f.trade_date AS d, f.channel,
                   sum(f.qty_ordered) AS qty
            FROM mart.fact_orders_daily f
            JOIN c ON c.point_key = f.point_key
            LEFT JOIN tw ON tw.ghost = f.sku_key
            WHERE f.trade_date < ? AND f.trade_date > DATE '2024-01-01'
            GROUP BY 1, 2, 3, 4
        ),
        t0 AS (SELECT point_key, min(d) AS d0 FROM o WHERE channel = 'transfer' GROUP BY 1)
        SELECT o.point_key, o.sku_key, o.d, sum(o.qty) AS qty
        FROM o LEFT JOIN t0 USING (point_key)
        WHERE (t0.d0 IS NULL AND o.channel = 'zakaz')
           OR (o.d <  t0.d0 AND o.channel = 'zakaz')
           OR (o.d >= t0.d0 AND o.channel = 'transfer')
        GROUP BY 1, 2, 3
    """, [upto]).fetchdf()

    dims = con.execute("""
        SELECT point_key, canon_name FROM mart.dim_point WHERE canon_name IS NOT NULL
    """).fetchdf()
    sku = con.execute("""
        SELECT sku_key, code, name, group_path, category FROM mart.dim_sku
        WHERE group_path LIKE ? AND group_path NOT LIKE ? AND group_path NOT LIKE ?
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR]).fetchdf()

    for df in (sales, orders):
        df["d"] = pd.to_datetime(df["d"]).dt.date
    sales = filter_frame(sales, "factory_order", date_col="d")
    orders = filter_frame(orders, "factory_order", date_col="d")

    # Защита от неполного хвостового дня (случай 07.08: в витрине лежал сегодняшний
    # день с ОДНОЙ точкой и 8 штуками — кто-то провёл документ среди дня). Такой
    # огрызок добавляет точке лишний «рабочий день» в знаменатель профиля и занижает
    # прогноз. Отрезаем последний день, если его объём меньше трети медианного.
    if not sales.empty:
        daily = sales.groupby("d")["qty"].sum().sort_index()
        if len(daily) > 7 and daily.iloc[-1] < 0.3 * daily.iloc[:-1].median():
            cut = daily.index[-1]
            print(f"⚠ последний день продаж {cut} неполный "
                  f"({daily.iloc[-1]:.0f} шт при медиане {daily.iloc[:-1].median():.0f}) — отброшен")
            sales = sales[sales["d"] < cut]
    return sales, orders, dims, sku


def weekday_factors(sales: pd.DataFrame, upto: date, weeks: int = 8) -> dict:
    """Коэффициент дня недели по (точка, категория): во сколько раз этот день недели
    отличается от среднего дня. Считается на агрегате категории — там сотни наблюдений,
    в отличие от отдельной позиции, где это был бы шум."""
    since = upto - timedelta(days=weeks * 7)
    tr = sales[sales["d"] >= since]
    if tr.empty:
        return {}
    tr = tr.copy()
    tr["wd"] = pd.to_datetime(tr["d"]).dt.weekday
    daily = tr.groupby(["point_key", "cat", "d", "wd"])["qty"].sum().reset_index()
    overall = daily.groupby(["point_key", "cat"])["qty"].mean()
    by_wd = daily.groupby(["point_key", "cat", "wd"])["qty"].mean()
    out = {}
    for (pt, cat, wd), v in by_wd.items():
        base = overall.get((pt, cat), 0.0)
        if base > 0:
            out[(pt, cat, wd)] = min(WD_CLIP[1], max(WD_CLIP[0], v / base))
    return out


def build(target: date, con, *, cfg: BuildCfg = BuildCfg(),
          panel: tuple | None = None,
          known_until: date | None = None) -> pd.DataFrame:
    """Проект заявки на target. cfg.demand_recovery (ПО УМОЛЧАНИЮ ВЫКЛ) включает
    правило восстановленного спроса — см. predict.demand; с выключенным флагом
    расчёт байт-в-байт совпадает с прежним (закреплено тестом тождества).

    known_until — последний завершённый день, доступный выпуску. В ночном
    D+2/D+3/D+4 это issue−1; явная граница не даёт ретропрогону увидеть дни
    между выпуском и целью.

    panel — результат load(con, upto_max) для бэктеста: 60 дат × полный скан
    витрины превращались в час ожидания; с панелью фильтр d < target идёт в
    pandas. Эквивалентность двух путей закреплена тестом.
    """
    data_upto = ((known_until + timedelta(days=1))
                 if known_until is not None else target)
    if panel is None:
        sales, orders, dims, sku = load(con, data_upto)
    else:
        sales_all, orders_all, dims, sku = panel
        sales = sales_all[sales_all["d"] < data_upto].copy()
        orders = orders_all[orders_all["d"] < data_upto].copy()
    # Защита для поданной извне/старой панели: собственное производство не должно
    # вернуться ни в формулу, ни в ML, ни в дневную рамку.
    sales = filter_frame(sales, "factory_order", date_col="d")
    orders = filter_frame(orders, "factory_order", date_col="d")
    # Нетипичные дни (excluded_days.json) — вне одноимённых недель формулы и вне
    # 28-дневного профиля: 28–31.08 (единицы ×2 при обычной выручке) удваивали
    # субботние заявки 12–19.09. ML-ядро исключает их само (ml_core.load_excluded).
    excluded_days = load_excluded_days()
    if excluded_days:
        sales = sales[~sales["d"].isin(excluded_days)].copy()
        orders = orders[~orders["d"].isin(excluded_days)].copy()
    if sales.empty:
        raise SystemExit(f"Нет продаж до {target} — витрина не собрана?")
    # Добавки восстановленного спроса: пустой словарь при выключенном флаге.
    # Панель продаж НЕ подменяется — КВП внутри forecast_v2 остаётся на сыром
    # sold, добавка применяется множителем к mu (иначе КВП удвоил бы эффект).
    upl = uplift_lookup(con, data_upto, cfg)

    # ЭКСПЕРИМЕНТ demand_feed (бэктест): история = спрос (sold+lost) по всем
    # парам. Только строки < target — утечки нет.
    if cfg.demand_feed:
        from prognoz.predict.demand import load_shelf, lost_lookup  # noqa: PLC0415
        ll = lost_lookup(load_shelf(con, data_upto, cfg.shelf_path))
        sales = sales.copy()
        extra = [ll.get((r.point_key, r.sku_key, r.d), 0.0)
                 for r in sales.itertuples()]
        sales["qty"] = sales["qty"] + extra

    cat_by_sku = dict(zip(sku["sku_key"], sku["category"].fillna("прочее")))
    sales["cat"] = sales["sku_key"].map(cat_by_sku).fillna("прочее")
    wd_f = weekday_factors(sales, target)
    name_by_pt = dict(zip(dims["point_key"], dims["canon_name"]))
    meta = sku.set_index("sku_key")[["code", "name", "group_path"]].to_dict("index")

    s_look = {(r.point_key, r.sku_key, r.d): r.qty for r in sales.itertuples()}
    seen_skus = set(sales["sku_key"].unique()) if not sales.empty else set()
    new_skus = network_new_skus(sales, target)
    o_look = {(r.point_key, r.sku_key, r.d): r.qty for r in orders.itertuples()}

    since = data_upto - timedelta(days=ACTIVE_DAYS)
    recent_s = sales[sales["d"] >= since]
    recent_o = orders[orders["d"] >= since]

    # Позиции, которые продаются, но с фабрики не приезжают ни разу за два месяца
    # (разбор 04.08: 52 пары, 7,4% объёма — почти все на Точки 1, где
    # 39% строк продаж дробные, то есть чеки разузловываются в компоненты).
    # Менеджеру такую строку заказывать нечем, и молча давать по ней число нельзя.
    ord60 = orders[orders["d"] >= data_upto - timedelta(days=60)] \
        .groupby(["point_key", "sku_key"])["qty"].sum().to_dict()

    # сколько дней точка реально работала в окне — иначе новая точка получит заниженный профиль
    days_open = recent_s.groupby("point_key")["d"].nunique().to_dict()
    # возраст точки считается по ВСЕЙ истории, а не по 28-дневному окну:
    # иначе «младше 60 дней» получают все точки, включая старейшую
    first_sale = sales.groupby("point_key")["d"].min().to_dict()

    # профиль пары за 28 дней: средние продажи в день и доля уценённых единиц
    agg = recent_s.groupby(["point_key", "sku_key"]).agg(
        qty28=("qty", "sum"), u4028=("u40", "sum")).reset_index()
    prof = {(r.point_key, r.sku_key): (r.qty28, r.u4028) for r in agg.itertuples()}

    pairs = set(zip(recent_s["point_key"], recent_s["sku_key"])) | \
            set(zip(recent_o["point_key"], recent_o["sku_key"]))
    pairs = {(p, s) for p, s in pairs if s in meta}
    pairs = filter_pairs(pairs, "factory_order") or set()
    # дней наблюдения пары за окно (продажа или заявка) — для правил нулей/медленных
    # в резервной формуле (у ML-ядра свой n_obs_days по полке)
    obs28 = (pd.concat([recent_s[["point_key", "sku_key", "d"]],
                        recent_o[["point_key", "sku_key", "d"]]], ignore_index=True)
             .drop_duplicates().groupby(["point_key", "sku_key"])["d"].nunique().to_dict())

    wd = target.weekday()
    # Одноимённые недели формулы: нетипичные даты выкидываются из окна
    # (осталось меньше двух — берём все четыре, как раньше).
    hist_lags = [l for l in WEEK_LAGS if (target - timedelta(days=l)) not in excluded_days]
    if len(hist_lags) < 2:
        hist_lags = list(WEEK_LAGS)
    # Календарь сравнивается с теми же днями, по которым считается прогноз: обычная
    # пятница по обычным пятницам даёт ровно 1,0 и заявку не трогает. Поправка
    # включается только на аномальных датах — 1 января, 8 марта, кануны праздников.
    hist_days = [target - timedelta(days=l) for l in hist_lags]
    cal_mult, cal_why = calendar_effects.relative_factor(target, hist_days)
    cal_anomaly = calendar_effects.is_anomalous(target)

    # --- ML-ядро (predict.ml_core, решение сотрудника 11.08: выпускается основным).
    # Формула базовой формулы при этом СЧИТАЕТСЯ для каждой пары как раньше и уходит
    # в лог колонкой _mu_baseline: сравнение движков живёт бесплатно, откат —
    # одна переменная окружения. При проблеме frozen bundle расчёт остаётся только
    # в staging на формуле, а release-gate не публикует его вместо утверждённой модели.
    ml_rows: dict = {}
    ml_stamp = None
    use_ml = cfg.engine in {"ml", "catboost", "stacked_guard"}
    effective_engine = cfg.engine
    model_core = None
    if use_ml:
        from prognoz.predict import guard                   # noqa: PLC0415
        try:
            if cfg.engine == "stacked_guard":
                from prognoz.predict import stacked_guard_core as model_core  # noqa: PLC0415
            elif cfg.engine == "catboost":
                from prognoz.predict import catboost_core as model_core  # noqa: PLC0415
            else:
                from prognoz.predict import ml_core as model_core        # noqa: PLC0415
            shelf_df = (pd.read_parquet(cfg.shelf_path)
                        if cfg.shelf_path is not None else None)
            if cfg.engine == "stacked_guard":
                preds, ml_meta = model_core.stacked_guard_forecast(
                    con, target, cfg=production_guard_cfg(model_core, cfg.model_lag),
                    shelf_df=shelf_df, pairs=pairs)
            elif cfg.engine == "catboost":
                if cfg.model_lag != 3:
                    raise ValueError(
                        "Прямой CatBoost не выпускался на D+3/D+4; "
                        "используйте проверенный stacked_guard")
                preds, ml_meta = model_core.catboost_forecast(
                    con, target, cfg=model_core.CatCfg(lag=cfg.model_lag),
                    shelf_df=shelf_df, pairs=pairs)
            else:
                preds, ml_meta = model_core.ml_forecast(
                    con, target, cfg=model_core.MlCfg(lag=cfg.model_lag),
                    shelf_df=shelf_df, pairs=pairs)
            ml_stamp = ml_meta["stamp"]
            effective_engine = str(ml_meta.get("engine", cfg.engine))
            if ml_meta.get("guard_mode") == "hgb_fail_safe":
                reason = ml_stamp.get("fallback_reason", "stacked bundle unavailable")
                guard.alert([f"STACKED-GUARD: включён HGB fail-safe: {reason}"])
            if effective_engine == "ml" and cfg.engine == "stacked_guard":
                from prognoz.predict import ml_core as hgb_core  # noqa: PLC0415
                prev_stamp = hgb_core.load_prev_stamp(
                    hgb_core.MlCfg(lag=cfg.model_lag))
            else:
                if cfg.engine == "catboost":
                    prev_stamp = model_core.load_prev_stamp(
                        model_core.CatCfg(lag=cfg.model_lag))
                elif cfg.engine == "ml":
                    prev_stamp = model_core.load_prev_stamp(
                        model_core.MlCfg(lag=cfg.model_lag))
                else:
                    prev_stamp = model_core.load_prev_stamp()
            sanity_infos: list[str] = []
            problems = guard.model_sanity(
                ml_stamp, prev_stamp, preds, target,
                lag_days=int(ml_stamp.get("effective_lag", cfg.model_lag)),
                infos=sanity_infos)
            if sanity_infos:
                guard.alert(sanity_infos)
            if problems:
                guard.alert(["ML-СТОРОЖ: фолбэк на базовый движок: " + p
                             for p in problems])
                use_ml = False
            else:
                ml_rows = {(r.point_key, r.sku_key): r
                           for r in preds.itertuples()}
        except Exception as e:                              # noqa: BLE001
            guard.alert([f"ML-ядро упало ({type(e).__name__}: {e}) — "
                         "фолбэк на базовый движок"])
            use_ml = False

    rows = []
    for pt, sk in sorted(pairs):
        sh = [s_look.get((pt, sk, target - timedelta(days=l)), 0.0) for l in hist_lags]
        oh = [o_look.get((pt, sk, target - timedelta(days=l)), 0.0) for l in hist_lags]

        # глубина истории: сколько последних одноимённых дней вообще что-то показывают
        n_hist = len(sh)
        while n_hist > 2 and sh[n_hist - 1] == 0 and oh[n_hist - 1] == 0:
            n_hist -= 1
        sh_w, oh_w = sh[:n_hist], oh[:n_hist]
        n_obs = sum(1 for x in sh_w if x > 0)

        qty28, u4028 = prof.get((pt, sk), (0.0, 0.0))
        open_days = max(1, days_open.get(pt, ACTIVE_DAYS))
        cat = cat_by_sku.get(sk, "прочее")
        f_wd = wd_f.get((pt, cat, wd), 1.0)
        base_profile = (qty28 / open_days) * f_wd          # цель усадки

        u40_share = (u4028 / qty28) if qty28 > 0 else 0.0
        locked = u40_share >= U40_LOCK_SHARE

        flags, notes = [], []
        if any(sh_w) or any(oh_w):
            # demand_feed: история уже спрос — КВП (прокси недозаказа) выключен
            oh_arg = None if cfg.demand_feed else (oh_w if any(oh_w) else None)
            fc = forecast_v2(sh_w, oh_arg, kvp_locked=locked)
            mu_engine, notes = fc.final, [fc.notes] if fc.notes else []
        else:
            mu_engine = base_profile
            flags.append("нет истории по одноимённым дням")

        # усадка к профилю позиции: чем меньше наблюдений, тем сильнее тянем к профилю
        if base_profile > 0 and n_obs < 4:
            mu = (n_obs * mu_engine + SHRINK_K * base_profile) / (n_obs + SHRINK_K)
            notes.append(f"усадка к профилю позиции ({n_obs} набл. из 4)")
        else:
            mu = mu_engine

        if abs(cal_mult - 1) > 0.01:
            mu *= cal_mult
            notes.append(f"календарь ×{cal_mult:.2f} — {cal_why}")
        elif cal_why:
            notes.append(f"календарь: {cal_why}")

        # Восстановленный спрос (predict.demand): у хронически дефицитной пары
        # история продаж обрезана её же недозаказом — добавляем оценку полки.
        # ПОСЛЕ календаря, ДО предохранителей: рамки ниже знают о добавке.
        u = upl.get((pt, sk))
        uplift = 1.0
        if u is not None and u.active and not u.revoked and u.uplift > 1.001:
            uplift = u.uplift
            mu *= uplift
            notes.append(f"восстановленный спрос +{(uplift - 1) * 100:.0f}% "
                         f"(дефицит {u.deficit_days28} дн из 28)")
        elif u is not None and u.revoked:
            notes.append(f"добавка спроса отозвана: {u.reason}")

        # предохранители
        human_ref = sum(oh_w) / len(oh_w) if any(oh_w) else None
        # В календарно аномальный день рамку к заявке сотрудника снимаем: она вернула бы
        # заявку 1 января к обычному уровню и обнулила смысл поправки. Рамка защищает от
        # ошибки модели в обычные дни — а в такой день сотрудник ошибается сильнее её.
        if human_ref and not cal_anomaly:
            # Верх рамки растёт на добавку: заявка человека — ровно тот недозаказ,
            # который восстанавливаем; рамка без поправки срезала бы добавку у
            # хроников (вывод панели). Низ не трогаем — защита от занижения цела.
            lo, hi = CLIP_TO_HUMAN[0] * human_ref, CLIP_TO_HUMAN[1] * human_ref * uplift
            if mu < lo or mu > hi:
                mu = min(hi, max(lo, mu))
                notes.append("ограничено рамкой ±15% к заявке сотрудника")
        elif human_ref and cal_anomaly:
            notes.append("рамка к заявке сотрудника снята: календарная аномалия")
        last_order = oh[0] if oh[0] else None
        if last_order:
            # Суточный шаг тоже расширяем в аномальный день — иначе 1 января заявка
            # не сможет опуститься ниже 70% вчерашней, а спрос падает впятеро.
            step = STEP_REL if not cal_anomaly else max(STEP_REL, abs(cal_mult - 1) + 0.1)
            cap = last_order * (1 + step) + STEP_ABS
            floor = last_order * (1 - step) - STEP_ABS
            if mu > cap or mu < floor:
                mu = min(cap, max(floor, mu))
                notes.append("ограничен суточный шаг")

        # Дальше базовая формула готова: mu_krap уходит в лог всегда.
        # ML-строка считается ЗАНОВО от сырого квантиля: усадка, uplift и рамки
        # старого движка к ней не применяются — годовой экзамен сдан без них,
        # а рамка к человеку означала бы, что ядро не живёт без его заявки.
        mu_krap = mu
        ml_r = ml_rows.get((pt, sk)) if use_ml else None
        hgb_floor_qty = np.nan
        if ml_r is not None:
            notes = [model_core.explain_row(ml_r)]
            mu = float(ml_r.mu_ml)
            if abs(cal_mult - 1) > 0.01:
                mu *= cal_mult
                notes.append(f"календарь ×{cal_mult:.2f} — {cal_why}")
            qty = int(np.floor(mu + 0.5))
            fl = int(ml_r.floor_units)
            # Пол только для позиций, новых для сети (или без продаж нигде): старая
            # позиция, впервые заказанная на точке, идёт по модели без пола.
            truly_new = (sk in new_skus) or (sk not in seen_skus)
            if fl > qty and truly_new:
                qty = fl
                notes.append(f"новинка: пол «уровень сети» {fl} шт"
                             if ml_r.n_obs_days > 0 else
                             f"новинка: старт {fl} шт — истории нет нигде")
            elif fl > qty:
                fl = 0
                notes.append("позиция не новая для сети — пол новинки не применён")
            if bool(ml_r.is_novelty) and truly_new:
                flags.append("холодный старт — правило сети")
            # Медленная позиция: не больше недельного объёма в день.
            lost28_val = (float(getattr(u, "lost28", 0.0) or 0.0)
                          if u is not None else None)
            qty, slow_note = slow_demand_cap(
                qty, sold28=qty28, lost28=lost28_val,
                n_obs_days=int(getattr(ml_r, "n_obs_days", 0) or 0))
            if slow_note:
                notes.append(slow_note)
            # Правило нулей: история есть, а спроса за 28 дней нет — не возить.
            # Нулевая строка ниже выпадает из выпуска (фильтр «Прогноз, шт» > 0).
            zero_note = zero_demand_note(
                qty, sold28=qty28,
                lost28=(float(getattr(u, "lost28", 0.0) or 0.0)
                        if u is not None else None),
                n_obs_days=int(getattr(ml_r, "n_obs_days", 0) or 0))
            if zero_note:
                qty = 0
                notes.append(zero_note)
            raw_hgb = getattr(ml_r, "hgb_qty", np.nan)
            if pd.notna(raw_hgb):
                # пол сторожа не выше итога: правила нулей/медленных/новинок — последнее слово
                hgb_floor_qty = min(
                    max(int(np.floor(float(raw_hgb) * cal_mult + 0.5)), fl), qty)
        else:
            qty = max(0, round(mu))
            if use_ml:
                flags.append("вне панели ядра — расчёт движком")
            # Правила медленных и нулевых позиций действуют и в резервной формуле:
            # они опираются на полку (продано/потери за 28 дн), а не на ядро. До 17.09
            # резервный выпуск шёл без них — «мёртвые» позиции возвращались в заявку.
            lost28_val = (float(getattr(u, "lost28", 0.0) or 0.0)
                          if u is not None else None)
            n_obs_formula = int(obs28.get((pt, sk), 0))
            qty, slow_note = slow_demand_cap(
                qty, sold28=qty28, lost28=lost28_val, n_obs_days=n_obs_formula)
            if slow_note:
                notes.append(slow_note)
            zero_note = zero_demand_note(
                qty, sold28=qty28, lost28=lost28_val, n_obs_days=n_obs_formula)
            if zero_note:
                qty = 0
                notes.append(zero_note)
        if locked:
            flags.append(f"уценка {u40_share * 100:.0f}% за 28 дн")
        if qty28 == 0:
            flags.append("нет продаж 28 дней")
        if ord60.get((pt, sk), 0) == 0 and qty28 > 0:
            flags.append("с фабрики не возят 60 дн — проверьте")
        age_at = known_until or target
        age = (age_at - first_sale.get(pt, age_at)).days
        if age < 60:
            flags.append(f"точка работает {age} дн.")

        m = meta[sk]
        rows.append({
            "Точка": name_by_pt.get(pt, pt),
            # Колонка «Код» убрана 03.08 по просьбе базовой формулы на встрече: «код нам
            # абсолютно не нужен, менеджеры должны ориентироваться только на таблице».
            "Позиция": m["name"],
            "Группа": (m["group_path"] or "").split(" / ")[-1],
            "Прогноз, шт": qty,
            # Пустые колонки под ручной ввод: оператор ставит своё число ДО того, как
            # посмотрит наше, и пишет причину расхождения. Это носитель парного замера —
            # единственный способ узнать, чего он видит такого, чего не видим мы.
            "Ваш заказ, шт": None,
            "Почему разошлось": None,
            "Продажи по неделям": " / ".join(f"{x:g}" for x in sh),
            "Заявки по неделям": " / ".join(f"{x:g}" for x in oh),
            "Продано за 28 дн": round(qty28, 1),
            "Уценено за 28 дн, %": round(u40_share * 100),
            "Пояснение": "; ".join(n for n in notes if n),
            "Внимание": "; ".join(flags),
            "_point_key": pt, "_sku_key": sk, "_mu": round(mu, 3),
            "_uplift": round(uplift, 3),
            "_mu_add": round(mu - mu / uplift, 3) if uplift > 1 else 0.0,
            "_add_revoked": bool(u is not None and u.revoked),
            "_mu_baseline": round(mu_krap, 3),
            "_mu_ml": (round(float(ml_r.mu_ml), 3) if ml_r is not None
                       else np.nan),
            "_model_variant": (getattr(ml_r, "guard_mode", cfg.engine)
                               if ml_r is not None else "baseline"),
            "_guard_applied": (bool(getattr(ml_r, "guard_applied", False))
                               if ml_r is not None else False),
            "_hgb_qty": (float(getattr(ml_r, "hgb_qty", np.nan))
                          if ml_r is not None else np.nan),
            "_hgb_floor_qty": hgb_floor_qty,
            "_floor": int(ml_r.floor_units) if ml_r is not None else 0,
            "_cal_mult": round(cal_mult, 3),
        })

    df = pd.DataFrame(rows)
    df = df[df["Прогноз, шт"] > 0].sort_values(["Точка", "Группа", "Позиция"])

    add_share = None
    if cfg.demand_recovery and not df.empty:
        # Ориентир дневной рамки цензурирован тем же недозаказом — двигаем его
        # на долю добавки точки (кап +10%), иначе DAY_CLIP срежет добавку сети.
        g = df.groupby("Точка").agg(mu_add=("_mu_add", "sum"), mu=("_mu", "sum"))
        add_share = {pt: min(0.10, r["mu_add"] / r["mu"]) for pt, r in g.iterrows()
                     if r["mu"] > 0 and r["mu_add"] > 0}
        col = (df["_mu_add"].round().astype(int)).clip(lower=0)
        df.insert(df.columns.get_loc("Прогноз, шт") + 1, "Восст. спрос, шт", col)
    df["engine"] = effective_engine if use_ml else "baseline"
    if ml_stamp is not None:
        df["model_cutoff"] = ml_stamp["train_cutoff"]
        df["_feature_lag"] = int(ml_stamp.get("feature_lag", cfg.model_lag))
        df["_bundle_lag"] = int(ml_stamp.get("bundle_lag", cfg.model_lag))
        df["_bundle_manifest_sha256"] = ml_stamp.get(
            "bundle_manifest_sha256", "")
        # фактическая граница данных панели (может быть раньше issue−1 — см.
        # stacked_guard_core.effective_lag); номинальный _feature_lag не меняется
        df["_effective_lag"] = int(ml_stamp.get("effective_lag", cfg.model_lag))
        df["_data_as_of"] = str(ml_stamp.get("data_as_of", ""))
    if cfg.demand_feed:
        # ориентир дневной рамки — продажи, к спросу он занижен системно
        return df[df["Прогноз, шт"] > 0]
    if use_ml:
        # Вместо DAY_CLIP — аварийный сторож с намеренно широкой рамкой:
        # в норме не срабатывает (экзаменационное поведение не искажается),
        # ловит только «модель сошла с ума» + пишет ALERT.
        from prognoz.predict.guard import day_guard         # noqa: PLC0415
        floor_col = ("_hgb_floor_qty"
                     if df["_hgb_floor_qty"].notna().any() else None)
        return day_guard(
            df, weekday_reference(con, target, known_until=known_until,
                                  excluded=excluded_days),
            target, cal_mult,
            floor_col=floor_col)
    return reconcile_to_day(
        df, weekday_reference(con, target, known_until=known_until,
                              excluded=excluded_days), target,
                            add_share_by_point=add_share)


def weekday_reference(con, target: date, *, known_until: date | None = None,
                      excluded: set[date] | None = None) -> dict:
    """Средний дневной объём точки за 4 последних таких же дня недели.

    excluded — нетипичные дни: не входят в четвёрку (иначе один день ×2 тянет рамку).

    ⚠️ У DuckDB неделя начинается с воскресенья (0=вс … 6=сб), у Python — с понедельника
    (0=пн … 6=вс). Без сдвига сверка сравнивает понедельник со вторниками."""
    data_upto = ((known_until + timedelta(days=1))
                 if known_until is not None else target)
    excl_sql = ""
    if excluded:
        excl_sql = "AND f.sale_date NOT IN (" + ", ".join(
            f"DATE '{d.isoformat()}'" for d in sorted(excluded)) + ")"
    ref = con.execute(f"""
        WITH {canon_cte()}, {sku_twins_cte()},
        nm AS (SELECT canon_name, point_key AS pt FROM mart.dim_point
               WHERE role IN ('market', 'market_hub')),
        d AS (
            SELECT nm.canon_name AS pt, f.sale_date, sum(f.qty_sold - f.qty_returned) AS qty
            FROM mart.fact_sales_daily f
            JOIN c ON c.point_key = f.point_key
            JOIN nm ON nm.pt = c.pt
            LEFT JOIN tw ON tw.ghost = f.sku_key
            JOIN mart.dim_sku s ON s.sku_key = coalesce(tw.live, f.sku_key)
            WHERE s.group_path LIKE ? AND s.group_path NOT LIKE ? AND s.group_path NOT LIKE ?
              AND {sql_exclusion_predicate(
                  "factory_order", point_expr="c.pt",
                  sku_expr="coalesce(tw.live, f.sku_key)", date_expr="f.sale_date")}
              AND dayofweek(f.sale_date) = ? AND f.sale_date < ? {excl_sql}
            GROUP BY 1, 2 HAVING sum(f.qty_sold - f.qty_returned) > 0
        ),
        r AS (SELECT pt, qty, row_number() OVER (PARTITION BY pt ORDER BY sale_date DESC) AS rn
              FROM d)
        SELECT pt, avg(qty) AS avg_qty FROM r WHERE rn <= 4 GROUP BY 1
    """, [GROUP_LIKE, GROUP_NOT_LIKE, GROUP_NOT_LIKE_BAR,
           (target.weekday() + 1) % 7, data_upto]).fetchdf()
    return dict(zip(ref["pt"], ref["avg_qty"]))


def reconcile_to_day(df: pd.DataFrame, ref: dict, target: date,
                     add_share_by_point: dict | None = None) -> pd.DataFrame:
    """Приведение дневного итога точки к разумной рамке вокруг того же дня недели.

    Зачем: ошибки отдельных позиций не гасят друг друга, а складываются в одну сторону —
    у методики есть шаг «продали всё привезённое → растим заказ», и на точках, где полку
    каждый вечер вычищает распродажа, он срабатывает почти везде сразу. Позиционный стопор
    ловит это не полностью: на Точки 4е и Точки 5 дневной итог всё равно уходил на +19%,
    а это ровно те две точки, где доля уценки и так самая высокая (36% и 50%).

    Рамка НЕ узкая намеренно: небольшое превышение — это поправка на неудовлетворённый
    спрос, она нужна. Отсекается только явный занос.
    """
    if df.empty:
        return df
    df = df.copy()
    df["_scale"] = 1.0
    # Ориентир — объём тех же дней недели, поэтому его нужно сдвинуть календарём:
    # иначе рамка вернёт заявку 1 января к уровню обычного четверга и перечеркнёт
    # поправку, которую строка уже получила.
    cal_mult, _ = calendar_effects.relative_factor(
        target, [target - timedelta(days=l) for l in WEEK_LAGS])
    for pt, base in ref.items():
        m = df["Точка"] == pt
        if not m.any() or not base:
            continue
        base = base * cal_mult
        # Восстановленный спрос: ориентир по продажам прошлых недель обрезан
        # тем же недозаказом, который добавка чинит — сдвигаем на её долю.
        if add_share_by_point:
            base *= 1 + add_share_by_point.get(pt, 0.0)
        raw = df.loc[m, "Прогноз, шт"].astype(float)
        total = raw.sum()
        goal = None
        if total > base * DAY_CLIP[1]:
            goal = base * DAY_CLIP[1]
        elif total < base * DAY_CLIP[0]:
            goal = base * DAY_CLIP[0]
        if goal is None or total <= 0:
            continue
        # ⚠️ Просто умножить каждую строку на коэффициент нельзя: позиции по 1–3 штуки
        # после умножения на 0,94 округляются обратно вверх, и дневной итог не меняется.
        # Поэтому целевой итог набирается целыми штуками по методу наибольших остатков.
        k = goal / total
        exact = raw * k
        qty = exact.apply(lambda x: int(x))          # вниз
        need = int(round(goal)) - int(qty.sum())
        order = (exact - qty).sort_values(ascending=(need < 0)).index
        for i in order[:abs(need)]:
            qty[i] += 1 if need > 0 else -1
        df.loc[m, "Прогноз, шт"] = qty.clip(lower=0).astype(int)
        df.loc[m, "_scale"] = round(k, 3)
        note = (f"дневной итог точки приведён к рамке ×{k:.2f} "
                f"(расчёт давал {total:.0f}, средний такой день {base:.0f})")
        df.loc[m, "Пояснение"] = df.loc[m, "Пояснение"].apply(
            lambda s: f"{s}; {note}" if s else note)
    return df[df["Прогноз, шт"] > 0]


def to_excel(df: pd.DataFrame, target: date, path: Path, meta_lines: list[str]):
    """Файл заявки. Оформление подчинено одному: менеджер смотрит в «Прогноз, шт»
    и вписывает своё в «Ваш заказ». Остальное — справка, она не должна кричать."""
    from openpyxl.styles import Alignment, Font, PatternFill      # noqa: PLC0415

    cols = [c for c in df.columns if not c.startswith("_")]
    accent = PatternFill("solid", fgColor="FFF2CC")               # мягкий жёлтый
    manual = PatternFill("solid", fgColor="E2EFDA")               # зелёный под ручной ввод
    head = Font(bold=True)

    def dress(ws, n_rows):
        ws.freeze_panes = "A2"
        by_name = {c.value: c.column_letter for c in ws[1]}
        for c in ws[1]:
            c.font = head
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for name, fill in (("Прогноз, шт", accent), ("Ваш заказ, шт", manual),
                           ("Почему разошлось", manual)):
            if name in by_name:
                for r in range(2, n_rows + 2):
                    ws[f"{by_name[name]}{r}"].fill = fill
        widths = {"Позиция": 38, "Группа": 18, "Прогноз, шт": 11,
                  "Восст. спрос, шт": 11, "Ваш заказ, шт": 11,
                  "Почему разошлось": 24, "Продажи по неделям": 20, "Заявки по неделям": 20,
                  "Продано за 28 дн": 12, "Уценено за 28 дн, %": 12,
                  "Пояснение": 46, "Внимание": 26, "Точка": 22}
        for name, w in widths.items():
            if name in by_name:
                ws.column_dimensions[by_name[name]].width = w

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        info = pd.DataFrame({"Проект заявки на " + target.strftime("%d.%m.%Y"): meta_lines})
        info.to_excel(xl, sheet_name="О расчёте", index=False)
        xl.sheets["О расчёте"].column_dimensions["A"].width = 100
        for pt, g in df.groupby("Точка"):
            sheet = str(pt)[:31]
            g[cols].drop(columns=["Точка"]).to_excel(xl, sheet_name=sheet, index=False)
            dress(xl.sheets[sheet], len(g))
        df[cols].to_excel(xl, sheet_name="Все точки", index=False)
        dress(xl.sheets["Все точки"], len(df))


def release_engine_matches(df: pd.DataFrame, requested_engine: str) -> bool:
    """True only when every released row was produced by the requested engine.

    The ML path deliberately has availability fallbacks. They may still produce
    a usable file, but the scheduler must receive a non-zero status so it cannot
    mistake that file for a successful release of the requested model.
    """
    if df.empty or "engine" not in df.columns:
        return False
    actual = set(df["engine"].dropna().astype(str))
    return actual == {requested_engine}


def main():
    import os                                                 # noqa: PLC0415
    args = sys.argv[1:]
    dry = "--dry" in args
    try:
        issue_date, target, horizon_days, feature_lag = resolve_release_context(args)
    except (TypeError, ValueError) as exc:
        print(f"ошибка горизонта выпуска: {exc}")
        return 2
    output_root = OUTPUT
    for arg in args:
        if arg.startswith("--output-root="):
            output_root = Path(arg.split("=", 1)[1]).resolve()
    # Движок: с 12.08 по умолчанию ML-ядро (решение сотрудника 11.08 — выпускаем
    # основным сразу). Откат на формулу базовой формулы — PROGNOZ_ENGINE=baseline
    # или --engine=baseline, без правки кода.
    engine = os.environ.get("PROGNOZ_ENGINE", "ml").strip().lower()
    for a in args:
        if a.startswith("--engine="):
            engine = a.split("=", 1)[1].strip().lower()
    if engine not in ("stacked_guard", "catboost", "ml", "baseline"):
        print(f"неизвестный движок «{engine}» — считаю stacked_guard")
        engine = "stacked_guard"
    # Восстановленный спрос: включается ТОЛЬКО явно — переменной окружения
    # (для Планировщика) или флагом. В ML-режиме не нужен вовсе: ядро учится
    # прямо на sold+lost, uplift сверху был бы двойным счётом.
    cfg = BuildCfg(demand_recovery=(
        os.environ.get("PROGNOZ_DEMAND_RECOVERY") == "1"
        or "--demand-recovery" in args), engine=engine,
        model_lag=feature_lag)

    con = connect_reader()
    max_sale = con.execute("SELECT max(sale_date) FROM mart.fact_sales_daily").fetchone()[0]
    known_until = issue_date - timedelta(days=1)
    visible_max_sale = min(max_sale, known_until)
    df = build(target, con, cfg=cfg, known_until=known_until)
    # Сверка «прогноз против того же дня недели» — первое, на что смотрит человек,
    # и самая дешёвая защита от грубой ошибки расчёта.
    ref_map = weekday_reference(con, target, known_until=known_until)
    con.close()

    gap = (target - visible_max_sale).days
    used_ml = (not df.empty) and df["engine"].isin(
        ["ml", "catboost", "stacked_guard"]).all()
    actual_engine = (str(df["engine"].iloc[0]) if not df.empty else "baseline")
    hgb_fail_safe = (
        not df.empty and "_model_variant" in df.columns
        and df["_model_variant"].eq("hgb_fail_safe").all())
    if actual_engine == "stacked_guard":
        method_lines = [
            "Методика — машинное ядро: HGB champion по восстановленному спросу",
            "и защитный stacked guard. Он добавляет 1 штуку только при согласии",
            "CatBoost q0,67, CatBoost q0,70 и meta-модели; ниже HGB не опускается.",
            (f"Горизонт D+{horizon_days}: те же замороженные веса; признаки "
             f"ограничены данными issue−1 (lag={feature_lag})."),
            "Календарь праздников и правило новинок «уровень сети» применяются после модели.",
            "Погода в расчёт не входит — на этих данных вклада не даёт.",
            "",
            "Расчёт — рекомендация; итоговую заявку можно скорректировать вручную.",
        ]
    elif used_ml:
        reserve = (
            "STACKED BUNDLE НЕДОСТУПЕН: включён HGB fail-safe; см. ALERT и журнал."
            if hgb_fail_safe else
            f"Активный ML-движок: {actual_engine}.")
        method_lines = [
            "Методика — машинное ядро по восстановленному спросу.",
            reserve,
            "Календарь праздников, правило новинок и аварийный сторож применяются после модели.",
            "Погода в расчёт не входит — на этих данных вклада не даёт.",
            "",
            "Расчёт — рекомендация; итоговую заявку можно скорректировать вручную.",
        ]
    else:
        method_lines = [
            "Методика — расчёт аналитика: одноимённые дни недели, веса свежести,",
            "сравнение проданного с заказанным. Добавлены усадка к профилю позиции,",
            "стопор поправки на вечернюю уценку и ограничение суточного шага.",
            "Погода в расчёт не входит — на этих данных вклада не даёт.",
            "",
            "РЕЗЕРВНЫЙ РЕЖИМ: считал классический движок (ML-ядро сегодня недоступно,",
            "см. журнал). В работу идёт решение сотрудника.",
        ]
    meta_lines = [
        f"Расчёт выполнен: {datetime.now():%d.%m.%Y %H:%M}",
        f"Данные о продажах — по {visible_max_sale:%d.%m.%Y} включительно (разрыв {gap} дн.)",
        f"Версия расчёта: {VERSION} ({df['engine'].iloc[0] if used_ml else 'базовый движок'})",
        f"Позиций в заявке: {len(df)}, точек: {df['Точка'].nunique()}",
        f"Итого штук: {df['Прогноз, шт'].sum():,.0f}",
        "",
        "Скоуп: синтетическое меню демо-сети, включая напитки.",
        *method_lines,
        "",
        f"СВЕРКА: прогноз против среднего за 4 последних {WD_NAMES[target.weekday()]}",
    ]
    by_pt = df.groupby("Точка")["Прогноз, шт"].sum()
    for pt, qty in by_pt.items():
        base = ref_map.get(pt)
        if base:
            meta_lines.append(f"    {pt}: {qty:.0f} шт против {base:.0f} "
                              f"({qty / base - 1:+.0%})")
        else:
            meta_lines.append(f"    {pt}: {qty:.0f} шт (сравнить не с чем — нет истории)")
    tot_ref = sum(ref_map.get(p, 0) for p in by_pt.index)
    if tot_ref:
        meta_lines.append(f"    ИТОГО: {by_pt.sum():.0f} против {tot_ref:.0f} "
                          f"({by_pt.sum() / tot_ref - 1:+.0%})")
        meta_lines += [
            "",
            "Небольшое превышение здесь — норма, а не ошибка: расчёт целится в спрос,",
            "а продажи прошлых недель обрезаны тем, сколько тогда привезли.",
            "Превышение больше чем на четверть — повод посмотреть внимательно.",
        ]
    # Нормальный разрыв равен feature_lag: для D+2/D+3/D+4 это 3/4/5 дней.
    # Ещё один день оставляем на ночную задержку выгрузки.
    if gap > feature_lag + 1:
        meta_lines.insert(2, "⚠ ДАННЫЕ УСТАРЕЛИ — расчёт по неполной истории")

    output_root.mkdir(parents=True, exist_ok=True)
    xls = output_root / f"Заявка_{target:%Y-%m-%d}.xlsx"
    to_excel(df, target, xls, meta_lines)

    log_dir = output_root / "forecast_log"
    log_dir.mkdir(exist_ok=True)
    log = df.copy()
    log["target_date"] = target
    log["_issue_date"] = issue_date
    log["_horizon_days"] = horizon_days
    log["_feature_lag"] = feature_lag
    if "_bundle_lag" not in log.columns:
        log["_bundle_lag"] = feature_lag
    log["max_sale_date"] = visible_max_sale
    log["version"] = VERSION
    log["run_at"] = datetime.now()
    log["input_hash"] = hashlib.sha256(
        f"{visible_max_sale}|{len(df)}|{df['_mu'].sum():.4f}".encode()).hexdigest()[:16]
    log.to_parquet(log_dir / f"{target:%Y-%m-%d}.parquet", index=False)

    print(f"Проект заявки на {target:%d.%m.%Y}: {len(df)} позиций, "
          f"{df['Прогноз, шт'].sum():,.0f} шт, данные по {visible_max_sale:%d.%m.%Y}")
    print(df.groupby("Точка").agg(позиций=("Прогноз, шт", "size"),
                                  штук=("Прогноз, шт", "sum")).to_string())
    print(f"\n→ {xls}")
    if dry:
        print(f"→ лог: {log_dir / f'{target:%Y-%m-%d}.parquet'}")
    if not release_engine_matches(df, engine):
        actual = ", ".join(sorted(set(df.get("engine", pd.Series(dtype=str)).astype(str))))
        print(
            f"ОШИБКА ВЫПУСКА: запрошен движок {engine}, фактически записан "
            f"{actual or 'неизвестный'}; резервный файл сохранён, требуется ALERT."
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
