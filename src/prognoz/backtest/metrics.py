# -*- coding: utf-8 -*-
"""Метрики бэктеста. Все числа считает код — LLM к ним не прикасается."""
import pandas as pd


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """df: строки (date, point, sku, model, forecast, actual, price).
    WAPE = Σ|f−a| / Σa;  bias = Σ(f−a) / Σa (плюс — перезаказ, минус — недозаказ);
    over_rub / under_rub — рублёвая цена ошибки в обе стороны.
    """
    d = df.copy()
    d["abs_err"] = (d["forecast"] - d["actual"]).abs()
    d["err"] = d["forecast"] - d["actual"]
    d["over_rub"] = d["err"].clip(lower=0) * d["price"]
    d["under_rub"] = (-d["err"]).clip(lower=0) * d["price"]

    g = d.groupby(group_cols, dropna=False).agg(
        rows=("actual", "size"),
        actual_qty=("actual", "sum"),
        forecast_qty=("forecast", "sum"),
        abs_err=("abs_err", "sum"),
        err=("err", "sum"),
        over_rub=("over_rub", "sum"),
        under_rub=("under_rub", "sum"),
    ).reset_index()
    g["wape"] = (100 * g["abs_err"] / g["actual_qty"].replace(0, pd.NA)).round(1)
    g["bias"] = (100 * g["err"] / g["actual_qty"].replace(0, pd.NA)).round(1)
    g["over_rub"] = g["over_rub"].round(0)
    g["under_rub"] = g["under_rub"].round(0)
    return g
