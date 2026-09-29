# -*- coding: utf-8 -*-
"""ML-ядро прогноза: градиентный бустинг по восстановленному спросу.

Победитель мозгового анализа 09.08 и годового экзамена 11.08 (запрос сотрудника):
HistGradientBoostingRegressor(loss="quantile", q=0.70) на плотной дневной панели
пар точка×позиция, цель — восстановленный спрос полки (sold + lost), а не
продажи. На годовом прогоне по двум старейшим точкам ядро идёт вровень с
человеком (Точки 1 — лучше него на 20–23%), старый базовый движок
оно обыгрывает на ~25%. Эталон переноса — analysis/research/year_run.py; его
перепрогон через ЭТОТ модуль (analysis/research/year_run_prod.py) обязан
воспроизводить экзаменационные числа — так прод и лаборатория не расходятся.

Правила честности (analysis/research/README_PANEL.md, нарушать нельзя):
  * все признаки строятся лагом >= cfg.lag от целевого дня (прод D+2 → лаг 3:
    утром D мы видим данные по конец D−1);
  * дни срыва поставки (supply_gap) и «нетипичные дни» из
    analysis/config/excluded_days.json в обучение не попадают (dem = NaN);
  * обучающая выборка — только day <= cutoff = target − lag.

Персистентность: model.joblib + stamp.json в analysis/output/ml_model/.
Переобучение — раз в REFIT_DAYS от cutoff штампа (fit стоит секунды, но
недельный шаг повторяет условия годового экзамена). Категории точек/групп
фиксируются списками в штампе: predict на новой панели кодирует их тем же
словарём, незнакомая точка/группа честно уходит в NaN (missing для HistGB),
а не молча получает чужой код.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import CONFIG, OUTPUT                              # noqa: E402
from prognoz.assortment import (filter_frame, filter_pairs,  # noqa: E402
                                provenance as assortment_provenance)

EXCLUDED_PATH = CONFIG / "excluded_days.json"


def model_dir(lag: int | None = None) -> Path:
    """Папка модели. Обычно боевая; PROGNOZ_ML_MODEL_DIR даёт песочницу.

    Песочница нужна пересчёту задним числом (predict.duel): он обучает модели с
    прошлыми cutoff, и без отдельной папки такая модель затёрла бы боевую.
    Читается на каждый вызов, а не при импорте, — чтобы переменную можно было
    выставить из кода до самого расчёта.
    """
    env = os.environ.get("PROGNOZ_ML_MODEL_DIR")
    root = Path(env) if env else OUTPUT / "ml_model"
    # Keep the established D+2 location byte-for-byte compatible.  Longer
    # horizon fail-safe models must never replace its joblib/stamp files.
    return root if lag is None or lag == LAG else root / f"lag_{lag}"

# Версия набора признаков/методики. Поднимать при ЛЮБОЙ правке фич или панели:
# несовпадение со штампом вызывает переобучение и попадает в forecast_log.
FEATS_VERSION = "hgb-demo-v1"      # b: заморожен прод-набор (DROP_FEATS)

Q = 0.70                     # квантиль: тюнинг июнь, чистый замер июль (анализ)
LAG = 3                      # горизонт D+2: утром D видим данные по D−1
REFIT_DAYS = 7               # шаг переобучения — как на годовом экзамене
GBM_PARAMS = dict(learning_rate=0.06, max_iter=400, max_leaf_nodes=63,
                  min_samples_leaf=25, l2_regularization=1.0,
                  early_stopping=False, random_state=7)     # cfg «deep»

# Признаки, ИСКЛЮЧЁННЫЕ из прод-набора — вердикт контрольного перепрогона года
# (year_run_prod.py, 11.08): month/week — нулевая важность; wo_rate28/surplus_l —
# на свежем хвосте систематически занижены непришедшими актами списаний
# (обучение и бой расходились бы). price_r28 ОСТАВЛЕНА: в проде цена пары
# константна, обучение и инференс согласованы. Прод-набор против полного:
# год +0.19% (внутри допуска +1%), ни один месяц не хуже >10%, WAPE +0.2 пп.
DROP_FEATS: tuple[str, ...] = ("month", "week", "wo_rate28", "surplus_l")

# Floor-правило новинок (правило клиента «8 штук», умная форма: пол, не замена)
NOVELTY_MIN_DAYS = 7         # меньше наблюдённых дней — пара считается новинкой
NOVELTY_FADE_DAYS = 14       # к этому числу дней истории пол полностью погашен
NOVELTY_COLD_START = 8.0     # штук, когда истории нет ни на одной точке
POINT_SCALE_CLIP = (0.4, 1.6)


@dataclass(frozen=True)
class MlCfg:
    lag: int = LAG
    quantile: float = Q
    refit_days: int = REFIT_DAYS


# ------------------------------------------------------------------ панель

def load_excluded(path: Path = EXCLUDED_PATH) -> set[date]:
    """Нетипичные дни (ТЗ, Блок 2): исключаются из обучения и окон движка."""
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {date.fromisoformat(d["date"]) for d in data.get("days", [])}


def build_panel(shelf_df: pd.DataFrame, end: date,
                excluded: set[date] | None = None) -> pd.DataFrame:
    """Плотная дневная сетка пар от первого наблюдения до end включительно.

    Перенос year_run.py:84–99. Строки будущего (после последнего дня полки)
    существуют с NaN-целью — на них считаются только признаки. Цель dem —
    восстановленный спрос без дней срыва поставки и без нетипичных дней.
    """
    dd = filter_frame(
        shelf_df, "metric_scope", date_col="trade_day")
    dd["trade_day"] = pd.to_datetime(dd["trade_day"])
    dd["demand"] = dd["sold_units"] + dd["lost_units"]
    end_ts = pd.Timestamp(end)
    frames = []
    for (pk, sk), g in dd.groupby(["point_key", "sku_key"], sort=False):
        g = g.sort_values("trade_day").set_index("trade_day")
        idx = pd.date_range(g.index.min(), end_ts, freq="D")
        r = g[["demand", "sold_units", "u40_units", "wo_units",
               "avg_price", "supply_gap", "wo_settled"]].reindex(idx)
        r["point_key"], r["sku_key"] = pk, sk
        r["grp"] = g["grp"].iloc[-1]
        frames.append(r)
    pan = pd.concat(frames)
    pan.index.name = "day"
    pan = pan.reset_index().sort_values(
        ["point_key", "sku_key", "day"]).reset_index(drop=True)
    pan["dem"] = pan["demand"].where(pan["supply_gap"] != True)  # noqa: E712
    if excluded:
        excl = pan["day"].dt.date.isin(excluded)
        pan.loc[excl, "dem"] = np.nan
    return pan


def _align(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = s.index.get_level_values(-1)
    return s


def add_features(pan: pd.DataFrame, lag: int) -> tuple[pd.DataFrame, list[str]]:
    """Признаки годового экзамена (year_run.py:108–147) минус DROP_FEATS.

    Все окна идут по уже сдвинутой на lag серии — заканчиваются на D−lag.
    Категории здесь НЕ кодируются: см. apply_categories (фиксация словаря).
    """
    g = pan.groupby(["point_key", "sku_key"], sort=False)
    gp = [pan["point_key"], pan["sku_key"]]
    lags = sorted({lag, 7, 14, 21, 28})
    for k in lags:
        pan[f"lag{k}"] = g["dem"].shift(k)
    shl = g["dem"].shift(lag)
    r28 = shl.groupby(gp).rolling(28, min_periods=7)
    pan["r28_mean"] = _align(r28.mean())
    pan["r28_med"] = _align(r28.median())
    pan["r28_std"] = _align(r28.std())
    pan["r28_q75"] = _align(r28.quantile(0.75))
    pan["weekday"] = pan["day"].dt.weekday
    sw = pan.groupby(["point_key", "sku_key", "weekday"], sort=False)["dem"].shift(1)
    rw = sw.groupby([pan["point_key"], pan["sku_key"], pan["weekday"]]).rolling(
        4, min_periods=2)
    pan["wd_mean"] = _align(rw.mean())
    pan["wd_med"] = _align(rw.median())
    pan["wd_std"] = _align(rw.std())
    pan["wd_q75"] = _align(rw.quantile(0.75))
    price = g["avg_price"].shift(lag)
    pan["price_r28"] = _align(price.groupby(gp).rolling(28, min_periods=3).mean())
    u40 = g["u40_units"].shift(lag)
    sold = g["sold_units"].shift(lag)
    wo = g["wo_units"].shift(lag)
    u40s = _align(u40.groupby(gp).rolling(28, min_periods=7).sum())
    solds = _align(sold.groupby(gp).rolling(28, min_periods=7).sum())
    pan["u40_share28"] = u40s / np.maximum(solds, 1.0)
    pan["wo_rate28"] = _align(wo.groupby(gp).rolling(28, min_periods=7).mean())
    pan["surplus_l"] = u40 + wo
    pan["month"] = pan["day"].dt.month
    pan["week"] = pan["day"].dt.isocalendar().week.astype(int)
    feats = [f"lag{k}" for k in lags] + [
        "r28_mean", "r28_med", "r28_std", "r28_q75",
        "wd_mean", "wd_med", "wd_std", "wd_q75",
        "price_r28", "u40_share28", "wo_rate28", "surplus_l",
        "weekday", "month", "week", "point_cat", "grp_cat"]
    feats = [f for f in feats if f not in DROP_FEATS]
    return pan, feats


def snapshot_categories(pan: pd.DataFrame) -> dict:
    """Словарь категорий с обучающей панели — уезжает в штамп модели."""
    return {"points": sorted(pan["point_key"].dropna().unique().tolist()),
            "grps": sorted(pan["grp"].dropna().unique().tolist())}


def apply_categories(pan: pd.DataFrame, cats: dict) -> pd.DataFrame:
    """Кодировать точки/группы ФИКСИРОВАННЫМ словарём из штампа.

    Незнакомое значение → NaN-категория (missing для HistGB) — молчаливый
    съезд кодов между обучением и предсказанием исключён по построению.
    """
    # Незнакомое значение переводится в NaN ЯВНО (в pandas 3 неявный перевод
    # при конструировании Categorical объявлен deprecated).
    pt = pan["point_key"].where(pan["point_key"].isin(cats["points"]))
    gr = pan["grp"].where(pan["grp"].isin(cats["grps"]))
    pan["point_cat"] = pd.Categorical(pt, categories=cats["points"])
    pan["grp_cat"] = pd.Categorical(gr, categories=cats["grps"])
    return pan


# ------------------------------------------------------------- модель+штамп

def _gbm_params_hash() -> str:
    return hashlib.sha256(json.dumps(GBM_PARAMS, sort_keys=True)
                          .encode()).hexdigest()[:16]


def _excluded_hash() -> str:
    days = sorted(d.isoformat() for d in load_excluded())
    return hashlib.sha256("|".join(days).encode()).hexdigest()[:16]


def fit(pan: pd.DataFrame, feats: list[str], cutoff: date, cfg: MlCfg):
    """Обучение на day <= cutoff. Gap/excluded-дни уже NaN и отпадают сами."""
    from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: PLC0415
    tr = pan[pan["dem"].notna() & (pan["day"] <= pd.Timestamp(cutoff))]
    if len(tr) < 1000:
        raise RuntimeError(f"обучающая выборка подозрительно мала: {len(tr)} строк")
    model = HistGradientBoostingRegressor(
        loss="quantile", quantile=cfg.quantile,
        categorical_features=["point_cat", "grp_cat"], **GBM_PARAMS)
    model.fit(tr[feats], tr["dem"])
    return model, tr


def make_stamp(feats: list[str], cutoff: date, cfg: MlCfg,
               tr: pd.DataFrame, cats: dict) -> dict:
    import sklearn                                          # noqa: PLC0415
    dem_sig = "{}|{}|{}".format(round(float(tr["dem"].sum()), 2), len(tr),
                                tr["day"].max().date())
    return {
        "feats_version": FEATS_VERSION,
        "features": feats,
        "lag": cfg.lag,
        "quantile": cfg.quantile,
        "gbm_params_hash": _gbm_params_hash(),
        "train_cutoff": cutoff.isoformat(),
        "trained_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "n_train_rows": int(len(tr)),
        "dem_sum_hash": hashlib.sha256(dem_sig.encode()).hexdigest()[:16],
        "categories": cats,
        "excluded_hash": _excluded_hash(),
        "assortment_policy_sha256": assortment_provenance()[
            "assortment_policy_sha256"],
        "sklearn_version": sklearn.__version__,
        "pandas_version": pd.__version__,
    }


def save_model(model, stamp: dict, cfg: MlCfg = MlCfg()) -> None:
    import joblib                                           # noqa: PLC0415
    md = model_dir(cfg.lag)
    md.mkdir(parents=True, exist_ok=True)
    cur = md / "model.joblib"
    if cur.exists():                                        # откат на шаг назад
        cur.replace(md / "prev.joblib")
        st = md / "stamp.json"
        if st.exists():
            st.replace(md / "prev_stamp.json")
    joblib.dump(model, cur)
    (md / "stamp.json").write_text(
        json.dumps(stamp, ensure_ascii=False, indent=1), encoding="utf-8")


def load_model(cfg: MlCfg = MlCfg()):
    """(model, stamp) или None, если модели ещё нет / не читается."""
    md = model_dir(cfg.lag)
    cur, st = md / "model.joblib", md / "stamp.json"
    if not (cur.exists() and st.exists()):
        return None
    try:
        import joblib                                       # noqa: PLC0415
        return joblib.load(cur), json.loads(st.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        print(f"модель не загрузилась ({e}) — потребуется переобучение")
        return None


def load_prev_stamp(cfg: MlCfg = MlCfg()) -> dict | None:
    st = model_dir(cfg.lag) / "prev_stamp.json"
    if st.exists():
        return json.loads(st.read_text(encoding="utf-8"))
    return None


def _needs_refit(stamp: dict, cutoff: date, cfg: MlCfg) -> str:
    """Причина переобучения или пустая строка («модель годна»)."""
    import sklearn                                          # noqa: PLC0415
    if stamp["feats_version"] != FEATS_VERSION:
        return f"версия фич {stamp['feats_version']} → {FEATS_VERSION}"
    if stamp["gbm_params_hash"] != _gbm_params_hash():
        return "изменились параметры модели"
    if stamp["lag"] != cfg.lag or stamp["quantile"] != cfg.quantile:
        return "изменился лаг/квантиль"
    if stamp["sklearn_version"] != sklearn.__version__:
        return (f"sklearn {stamp['sklearn_version']} → {sklearn.__version__} "
                "(joblib непереносим)")
    if stamp["excluded_hash"] != _excluded_hash():
        return "изменился список нетипичных дней"
    if stamp.get("assortment_policy_sha256") != assortment_provenance()[
        "assortment_policy_sha256"
    ]:
        return "изменилась ассортиментная политика"
    age = (cutoff - date.fromisoformat(stamp["train_cutoff"])).days
    if age >= cfg.refit_days:
        return f"модели {age} дн (порог {cfg.refit_days})"
    # Отрицательный возраст = модель обучена ПОЗЖЕ цели: так бывает при пересчёте
    # задним числом. Без этой ветки прошлое предсказывала бы модель, видевшая
    # будущее, и выглядело бы это безупречно.
    if age < 0:
        return f"модель обучена по {stamp['train_cutoff']} — позже цели ({cutoff})"
    return ""


def ensure_model(pan: pd.DataFrame, feats: list[str], target: date,
                 cfg: MlCfg = MlCfg(), force: bool = False):
    """Загрузить годную модель или переобучить. → (model, stamp, refitted)."""
    cutoff = target - timedelta(days=cfg.lag)
    loaded = load_model(cfg)
    if loaded is not None and not force:
        model, stamp = loaded
        reason = _needs_refit(stamp, cutoff, cfg)
        if not reason:
            return model, stamp, False
        print(f"переобучение: {reason}")
    cats = snapshot_categories(pan)
    pan = apply_categories(pan, cats)
    t0 = time.time()
    model, tr = fit(pan, feats, cutoff, cfg)
    stamp = make_stamp(feats, cutoff, cfg, tr, cats)
    save_model(model, stamp, cfg)
    print(f"обучено: {len(tr):,} строк по {cutoff}, {time.time()-t0:.1f} c")
    return model, stamp, True


# --------------------------------------------------------------- предсказание

def predict_rows(model, stamp: dict, pan: pd.DataFrame, target: date
                 ) -> pd.DataFrame:
    """Сырой квантиль по всем парам панели на день target.

    Возвращает point_key, sku_key, mu_ml (несглаженный, до календаря и floor),
    n_obs_days (наблюдённых дней пары) и значения опорных фич для «Пояснения».
    """
    pan = apply_categories(pan, stamp["categories"])
    te = pan[pan["day"] == pd.Timestamp(target)].copy()
    if te.empty:
        raise RuntimeError(f"в панели нет строк на {target} — сетка короче цели")
    feats = stamp["features"]
    te["mu_ml"] = np.clip(np.floor(model.predict(te[feats]) + 0.5), 0, None)
    n_obs = (pan[pan["dem"].notna()]
             .groupby(["point_key", "sku_key"]).size().rename("n_obs_days"))
    te = te.merge(n_obs, on=["point_key", "sku_key"], how="left")
    te["n_obs_days"] = te["n_obs_days"].fillna(0).astype(int)
    lag_col = f"lag{stamp['lag']}"
    cols = ["point_key", "sku_key", "grp", "mu_ml", "n_obs_days",
            "r28_mean", "wd_mean", lag_col, "lag7"]
    return te[[c for c in cols if c in te.columns]].rename(
        columns={lag_col: "lag_recent"})


def novelty_floor(preds: pd.DataFrame, pan: pd.DataFrame, target: date,
                  lag: int) -> pd.DataFrame:
    """Floor-правило новинок: пол «уровень SKU на других точках», не замена.

    «8 штук всем новинкам» в лоб на бэктесте УХУДШАЛО (+175…+299 тыс/июль):
    половина новинок — мелкая ротация меню. Работает форма-пол: молодая пара
    не может получить меньше уровня сети, гашение по мере накопления истории,
    модель правит как только у неё появляются данные.

      floor_units = round(floor_val × (1 − w)),  w = min(1, n_obs/14)
      floor_val   = median(r28_mean этого SKU на ДРУГИХ точках) × point_scale
      point_scale = дневной объём точки / медиана по точкам, клип [0.4; 1.6]
      истории нет ни на одной точке → floor_val = 8 (правило клиента).
    """
    day_t = pd.Timestamp(target)
    at_t = pan[pan["day"] == day_t][["point_key", "sku_key", "r28_mean"]]

    # дневной объём точки за 28 дней, лаг-честно (окно кончается на D−lag)
    hist = pan[(pan["day"] < day_t - pd.Timedelta(days=lag - 1))
               & (pan["day"] >= day_t - pd.Timedelta(days=lag - 1 + 28))
               & pan["dem"].notna()]
    per_pt = hist.groupby("point_key")["dem"].sum() / 28.0
    med_pt = per_pt.median()
    scale = ((per_pt / med_pt).clip(*POINT_SCALE_CLIP)
             if med_pt and med_pt > 0 else pd.Series(dtype=float))

    out = preds.copy()
    is_new = out["n_obs_days"] < NOVELTY_MIN_DAYS
    floor_vals = np.zeros(len(out))
    for i, r in enumerate(out.itertuples()):
        if not is_new.iloc[i]:
            continue
        others = at_t[(at_t["sku_key"] == r.sku_key)
                      & (at_t["point_key"] != r.point_key)]["r28_mean"].dropna()
        if len(others):
            floor_vals[i] = float(others.median()) * float(
                scale.get(r.point_key, 1.0))
        else:
            floor_vals[i] = NOVELTY_COLD_START
    w = np.minimum(1.0, out["n_obs_days"] / NOVELTY_FADE_DAYS)
    out["floor_units"] = np.where(
        is_new, np.round(floor_vals * (1 - w)), 0.0)
    out["is_novelty"] = is_new
    return out


def explain_row(r, quantile: float = Q) -> str:
    """«Почему столько» — сами признаки словами, без внутренностей модели."""
    bits = []
    if pd.notna(getattr(r, "r28_mean", np.nan)):
        bits.append(f"средний спрос 28 дн {r.r28_mean:.1f} шт/д")
    if pd.notna(getattr(r, "wd_mean", np.nan)):
        bits.append(f"такие же дни недели {r.wd_mean:.1f}")
    if pd.notna(getattr(r, "lag_recent", np.nan)):
        bits.append(f"последний виденный день {r.lag_recent:g}")
    if not bits:
        bits.append("истории мало — уровень группы и точки")
    return ("ядро: " + "; ".join(bits)
            + f"; запас под пик (q{quantile:.2f})")


# ------------------------------------------------------------------ фасад

def ml_forecast(con, target: date, cfg: MlCfg = MlCfg(),
                shelf_df: pd.DataFrame | None = None,
                pairs: set[tuple[str, str]] | None = None,
                refit: bool = False) -> tuple[pd.DataFrame, dict]:
    """Полка → панель → модель → сырой прогноз на target + floor новинок.

    pairs — ассортиментная матрица из predict.daily: пары матрицы, которых нет
    в панели (совсем свежий SKU — в полке ни одной продажи), получают строку
    с mu_ml=0 и уходят в floor-правило: уровень SKU на других точках, а если
    SKU не знает вся сеть — стартовые 8 шт.

    Возвращает (df, meta): df с mu_ml/floor_units/is_novelty/explain-полями,
    meta — штамп и флаги для forecast_log и сторожа. Календарь и округление
    НЕ здесь — их накладывает predict.daily (единая точка выпуска).
    """
    from prognoz.predict.demand import load_shelf           # noqa: PLC0415
    t0 = time.time()
    pairs = filter_pairs(pairs, "factory_order")
    if shelf_df is None:
        # run-даты нет в сигнатуре: полка «по вчера» относительно сегодня
        shelf_df = load_shelf(con, date.today())
    pan = build_panel(shelf_df, target, load_excluded())
    pan, feats = add_features(pan, cfg.lag)
    model, stamp, refitted = ensure_model(pan, feats, target, cfg, force=refit)
    preds = predict_rows(model, stamp, pan, target)
    if pairs is not None:
        have = set(zip(preds["point_key"], preds["sku_key"]))
        missing = sorted(p for p in pairs if p not in have)
        if missing:
            extra = pd.DataFrame(missing, columns=["point_key", "sku_key"])
            extra["mu_ml"] = 0.0
            extra["n_obs_days"] = 0
            preds = pd.concat([preds, extra], ignore_index=True)
    preds = novelty_floor(preds, pan, target, cfg.lag)
    meta = {"stamp": stamp, "refitted": refitted,
            "elapsed_s": round(time.time() - t0, 1)}
    return preds, meta


def prepare(target: date | None = None, refit: bool = False) -> int:
    """Ночной шаг run-daily: скользящая полка + панель + свежая модель.

    Отделён от выпуска заявки: если здесь что-то падает, daily.py уходит в
    фолбэк на базовый движок, и точка не остаётся без файла.
    """
    from _db import connect_reader                          # noqa: PLC0415
    target = target or (date.today() + timedelta(days=2))
    con = connect_reader()
    try:
        preds, meta = ml_forecast(con, target, refit=refit)
    finally:
        con.close()
    st = meta["stamp"]
    print(f"ML-ядро готово: {len(preds):,} пар на {target}, "
          f"модель по {st['train_cutoff']} "
          f"({'переобучена' if meta['refitted'] else 'из кэша'}), "
          f"{meta['elapsed_s']} c")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--refit", action="store_true")
    ap.add_argument("--date", type=str, default=None)
    a = ap.parse_args()
    target = date.fromisoformat(a.date) if a.date else None
    if a.prepare or a.refit:
        return prepare(target, refit=a.refit)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
