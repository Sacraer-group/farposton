# -*- coding: utf-8 -*-
"""Аварийный сторож ML-ядра: «модель сошла с ума» не должно дойти до клиента.

ML-режим работает БЕЗ рамок старого движка (CLIP_TO_HUMAN, суточный шаг,
DAY_CLIP): годовой экзамен сдан сырым квантилем, а рамка к человеку означала бы,
что ядро не может жить без его заявки. Взамен — два сторожа:

  * day_guard — дневной итог точки против среднего такого же дня недели
    (weekday_reference × календарь). Ориентир — ПРОДАЖИ, а ядро целится в
    СПРОС квантилем 0.70, поэтому итог ×1.2–1.7 от ориентира — рабочая норма
    (замер 11.08: точки шли ×1.32–1.69, на мелких глубокий пуассон задирает
    квантиль сильнее). Рамка клипа поэтому шире: [0.45; 1.8] — в норме не
    срабатывает никогда, ловит только явную аварию модели. Первый месяц
    дополнительно пишем ИНФОРМАЦИОННЫЕ строки на выходах за [0.6; 1.55] —
    без клипа, для глаза.
  * model_sanity — проверки ДО выпуска: протухший штамп, схлопнувшаяся
    обучающая выборка, пустые/нулевые предсказания, дырявые фичи целевого дня.
    Любая проблема → ALERT + daily.py уходит в фолбэк на базовый движок
    (философия run-daily: точка никогда не остаётся без файла).

ALERT-файл — тот же контракт, что у run-daily.ps1: logs/ALERT-<дата>.txt,
строки дописываются, файл виден глазами и грепается.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

PROJ = Path(__file__).resolve().parents[3]          # …\prognoz
LOGS = PROJ / "logs"


def _logs_dir() -> Path:
    """Runtime alert directory; client deployments keep mutable data on D:."""
    configured = os.environ.get("PROGNOZ_LOG_DIR", "").strip()
    return Path(configured).resolve() if configured else LOGS

DAY_GUARD = (0.45, 1.8)     # жёсткая рамка — клип (авария, не калибровка)
INFO_GUARD = (0.6, 1.55)    # информационная — только строка в ALERT
LAG_DAYS = 3                # горизонт ядра: обучение не позже target − 3 (ml_core.LAG)


def alert(lines: list[str], day: date | None = None) -> None:
    """Дописать строки в logs/ALERT-<дата>.txt (дата = сегодня, день запуска)."""
    if not lines:
        return
    day = day or date.today()
    logs = _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"ALERT-{day.isoformat()}.txt"
    with path.open("a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")
    for ln in lines:
        print(f"!!! {ln}")


def _fit_total(raw: pd.Series, goal: float) -> pd.Series:
    """Целевой итог целыми штуками по методу наибольших остатков.

    Механика reconcile_to_day (daily.py): простое умножение на коэффициент
    съедается округлением мелких позиций обратно вверх.
    """
    k = goal / raw.sum()
    exact = raw * k
    qty = exact.apply(int)
    need = int(round(goal)) - int(qty.sum())
    order = (exact - qty).sort_values(ascending=(need < 0)).index
    for i in order[:abs(need)]:
        qty[i] += 1 if need > 0 else -1
    return qty.clip(lower=0).astype(int)


def _fit_total_with_floor(raw: pd.Series, goal: float,
                          floor: pd.Series) -> pd.Series:
    """Fit a point total without lowering any row below a business floor."""
    lower = pd.to_numeric(floor, errors="coerce").fillna(0).clip(lower=0)
    lower = (lower + 0.5).apply(int).astype(int)
    goal_units = max(int(round(goal)), int(lower.sum()))
    extra_goal = goal_units - int(lower.sum())
    if extra_goal <= 0:
        return lower
    headroom = (raw.astype(float) - lower).clip(lower=0)
    if headroom.sum() <= 0:
        return lower
    return (lower + _fit_total(headroom, extra_goal)).astype(int)


def day_guard(df: pd.DataFrame, ref: dict, target: date,
              cal_mult: float = 1.0,
              floor_col: str | None = None) -> pd.DataFrame:
    """Дневной сторож ML-режима с опциональным построчным нижним порогом."""
    if df.empty:
        return df
    df = df.copy()
    df["_guard_scale"] = 1.0
    alerts: list[str] = []
    for pt, base in ref.items():
        m = df["Точка"] == pt
        if not m.any() or not base:
            continue
        base = base * cal_mult
        raw = df.loc[m, "Прогноз, шт"].astype(float)
        total = raw.sum()
        if total <= 0:
            continue
        ratio = total / base
        goal = None
        if ratio > DAY_GUARD[1]:
            goal = base * DAY_GUARD[1]
        elif ratio < DAY_GUARD[0]:
            goal = base * DAY_GUARD[0]
        if goal is not None:
            if floor_col and floor_col in df.columns:
                fitted = _fit_total_with_floor(raw, goal, df.loc[m, floor_col])
            else:
                fitted = _fit_total(raw, goal)
            df.loc[m, "Прогноз, шт"] = fitted
            achieved = float(fitted.sum())
            k = achieved / total
            df.loc[m, "_guard_scale"] = round(k, 3)
            note = (f"итог точки приведён аварийным сторожем ×{k:.2f} "
                    f"(расчёт давал {total:.0f}, средний такой день {base:.0f})")
            df.loc[m, "Пояснение"] = df.loc[m, "Пояснение"].apply(
                lambda s: f"{s}; {note}" if s else note)
            floor_note = ("; HGB-floor не дал опустить итог до рамки"
                          if achieved > round(goal) else "")
            alerts.append(f"ML-СТОРОЖ: {pt}: итог {total:.0f} при ориентире "
                          f"{base:.0f} (×{ratio:.2f}) — результат {achieved:.0f}, "
                          f"цель рамки {goal:.0f}{floor_note}")
        elif not INFO_GUARD[0] <= ratio <= INFO_GUARD[1]:
            alerts.append(f"ML-СТОРОЖ (инфо): {pt}: итог {total:.0f} = "
                          f"×{ratio:.2f} от ориентира {base:.0f} — без клипа, "
                          f"рамка [{INFO_GUARD[0]}; {INFO_GUARD[1]}]")
    alert(alerts)
    return df[df["Прогноз, шт"] > 0]


NAN_SHARE_FAIL = 0.6      # доля пар без единого опорного признака — выпуск ML запрещён
NAN_SHARE_INFO = 0.3      # выше — предупреждение в ALERT, выпуск продолжается


def model_sanity(stamp: dict, prev_stamp: dict | None,
                 preds: pd.DataFrame, target: date,
                 lag_days: int = LAG_DAYS,
                 infos: list[str] | None = None) -> list[str]:
    """Проблемы, при которых выпускать ML-заявку нельзя. Пусто = можно.

    infos — необязательный список: сюда складываются предупреждения, которые
    выпуск не блокируют (доля пустых признаков в зоне NAN_SHARE_INFO..FAIL)."""
    problems: list[str] = []
    cutoff = date.fromisoformat(stamp["train_cutoff"])
    information_day = target - timedelta(days=lag_days)
    if (information_day - cutoff).days > 14:
        problems.append(f"модель обучена по {cutoff} — старше 14 дней от "
                        f"доступной границы {information_day} для цели {target}")
    # Обратная сторона той же проверки: модель, обученная ПОЗЖЕ цели, знает ответ.
    # В бою так не бывает, при пересчёте задним числом (predict.duel) — легко, и
    # результат выглядит идеально ровно потому, что он нечестный.
    if cutoff > information_day:
        problems.append(f"модель обучена по {cutoff} — это позже допустимого "
                        f"{information_day} для цели {target}: "
                        "заглядывание в будущее")
    if prev_stamp is not None:
        prev_n = prev_stamp.get("n_train_rows", 0)
        if prev_n and stamp["n_train_rows"] < 0.7 * prev_n:
            problems.append(
                f"обучающая выборка схлопнулась: {stamp['n_train_rows']:,} "
                f"строк против {prev_n:,} в прошлой модели (потеря >30%)")
    if preds.empty:
        problems.append("ядро не вернуло ни одной строки")
    else:
        if preds["mu_ml"].isna().all() or (preds["mu_ml"] == 0).all():
            problems.append("все предсказания ядра нулевые или NaN")
        feat_cols = [c for c in ("r28_mean", "wd_mean", "lag_recent")
                     if c in preds.columns]
        # Доля считается по парам матрицы заявки, у которых история в принципе
        # есть: пары «холодного старта» (cold_start — истории нет нигде) пусты
        # по определению и о свежести данных ничего не говорят.
        scope = preds
        if "guard_mode" in preds.columns:
            scope = preds[preds["guard_mode"].astype(str) != "cold_start"]
        if feat_cols and not scope.empty:
            nan_share = scope[feat_cols].isna().all(axis=1).mean()
            if nan_share > NAN_SHARE_FAIL:
                problems.append(
                    f"у {nan_share:.0%} пар пустые опорные признаки — "
                    "панель не видит свежих данных")
            elif nan_share > NAN_SHARE_INFO and infos is not None:
                infos.append(
                    f"ML-СТОРОЖ (инфо): у {nan_share:.0%} пар пустые опорные "
                    f"признаки — ниже порога {NAN_SHARE_FAIL:.0%}, выпуск ML продолжен")
    return problems
