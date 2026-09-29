# -*- coding: utf-8 -*-
"""Утренняя сводка (запрос сотрудника 11.08): «нейронка утром в отдельном окне
выдаёт статистику и точность за предыдущий день, отчёты сохраняются».

Разделение труда жёсткое: ВСЕ числа считает этот модуль детерминированно
(из shadow-сверки, shadow_log и свежего forecast_log), LLM пишет только
короткий связный текст ПО ГОТОВОМУ JSON и обязан копировать числа дословно.
LLM недоступен/не настроен → сводка выходит без текста, числа те же.

Выход: analysis/output/ai_reports/<дата>.json (машинный контекст),
<дата>.md (текст + таблица чисел), <дата>.html (для веба и для чата).
Запуск: ночной run-daily.ps1 после reconcile; падение НЕ блокирует конвейер.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import OUTPUT                                      # noqa: E402
from prognoz.assortment import filter_frame                 # noqa: E402
from prognoz.report.llm_client import chat, llm_settings    # noqa: E402

PROJ = SRC.parents[0]
REPORTS = OUTPUT / "ai_reports"
SHADOW = OUTPUT / "shadow"
LOG_DIR = OUTPUT / "forecast_log"
FC = "Прогноз, шт"
WD = ("понедельник", "вторник", "среда", "четверг",
      "пятница", "суббота", "воскресенье")
CO, CU = 0.33, 0.67          # экономика ошибки: лишнее / недовоз (методика)
ENGINE_LABELS = {
    "ml": "ML-ядро",
    "catboost": "ML-ядро · CatBoost",
    "stacked_guard": "ML-ядро · ансамбль",
    # резервный контур: ML-ядро не прошло ночную проверку либо выпуск до 12.08
    "baseline": "Резерв: базовая формула",
}


def _engine_label(engine: str) -> str:
    normalized = str(engine or "").strip().lower()
    if not normalized:
        return ENGINE_LABELS["baseline"]
    return ENGINE_LABELS.get(normalized, f"движок {engine}")

# Операционный день в UTC+10, серверное время UTC+3.
LOCAL_SHIFT_H = 7


def _local_now() -> datetime:
    """Операционное время UTC+10 при серверном UTC+3."""
    return datetime.now() + timedelta(hours=LOCAL_SHIFT_H)


def _local_today() -> date:
    return _local_now().date()

SYSTEM_PROMPT = """Ты — утренний аналитик демонстрационной сети точек с шаурмой (прогноз заказов готовой еды).
Напиши сводку для руководителя на русском, 8–14 предложений, три блока с заголовками:
1) Как отработал прогноз за прошедший день. 2) Что в сегодняшней заявке. 3) На что обратить внимание.
Используй ТОЛЬКО числа из JSON и копируй их дословно, ничего не вычисляй и не выдумывай.
Помни: строки с пометкой sellout — товар распродан подчистую, там факт это нижняя граница
спроса, «перезаказ» по ним не критикуй. Тон деловой и спокойный, без воды и превосходных степеней."""


def _prices() -> pd.Series:
    """Цена пары из свежей полки; пусто — деньги в сводке опускаются."""
    for name in ("shelf_rolling.parquet", "shelf_daily.parquet"):
        p = OUTPUT / name
        if p.exists():
            df = pd.read_parquet(
                p, columns=["point_key", "sku_key", "trade_day", "avg_price"])
            df = filter_frame(df, "metric_scope", date_col="trade_day")
            return df.groupby(["point_key", "sku_key"])["avg_price"].max()
    return pd.Series(dtype=float)


def _load_shadow(path: Path) -> pd.DataFrame:
    """Чистый read-view старого/нового shadow; исходный parquet не меняется."""
    day = date.fromisoformat(path.stem)
    frame = pd.read_parquet(path)
    if not {"_point_key", "_sku_key"}.issubset(frame.columns):
        return frame.iloc[0:0].copy()
    frame = frame.copy()
    frame["_metric_date"] = day
    frame = filter_frame(
        frame, "metric_scope", point_col="_point_key", sku_col="_sku_key",
        date_col="_metric_date")
    live = frame[frame["canon_name"].notna()]
    alive = live.groupby("canon_name")["actual"].sum()
    return live[live["canon_name"].isin(alive[alive > 0].index)].copy()


def _yesterday_block() -> dict | None:
    files = sorted(SHADOW.glob("*.parquet"))
    if not files:
        return None
    day = date.fromisoformat(files[-1].stem)
    live = _load_shadow(files[-1])
    if live.empty:
        return None
    px = _prices()
    if len(px):
        key = pd.MultiIndex.from_frame(live[["_point_key", "_sku_key"]])
        live = live.assign(px=key.map(px))
        live["px"] = live["px"].fillna(px.median())
        over_rub = (live["err"].clip(lower=0) * live["px"] * CO).sum()
        under_rub = ((-live["err"]).clip(lower=0) * live["px"] * CU).sum()
    else:
        over_rub = under_rub = None

    def _top(df, n=5):
        return [{"point": r["canon_name"], "sku": str(r["Позиция"])[:40],
                 "forecast": round(r[FC]), "sold": round(r["actual"]),
                 "sellout": bool(r["sellout"])}
                for _, r in df.iterrows()]

    pts = {}
    for pt, g in live.groupby("canon_name"):
        f, a = g[FC].sum(), g["actual"].sum()
        pts[pt] = {"forecast": round(f), "sold": round(a),
                   "miss_pct": round(100 * (f - a) / a, 1) if a else None}
    return {
        "date": day.isoformat(), "weekday": WD[day.weekday()],
        "net_forecast": round(live[FC].sum()),
        "net_sold": round(live["actual"].sum()),
        "net_miss_pct": round(100 * (live[FC].sum() - live["actual"].sum())
                              / live["actual"].sum(), 1),
        "by_point": pts,
        "under_units": round(-live.loc[live["err"] < 0, "err"].sum()),
        "over_units": round(live.loc[live["err"] > 0, "err"].sum()),
        "sellout_rows": int(live["sellout"].sum()),
        "miss_money_rub": (round(over_rub + under_rub)
                           if over_rub is not None else None),
        "top_under": _top(live.nsmallest(5, "err")),
        "top_over": _top(live.nlargest(5, "err")),
    }


def _trend_block() -> list[dict]:
    rows = []
    for path in sorted(SHADOW.glob("*.parquet"))[-7:]:
        frame = _load_shadow(path)
        actual = float(frame["actual"].sum()) if not frame.empty else 0.0
        if actual <= 0:
            continue
        err = frame[FC] - frame["actual"]
        rows.append({
            "date": path.stem,
            "wape_pos_pct": round(100 * float(err.abs().sum()) / actual, 1),
            "bias_pct": round(100 * float(err.sum()) / actual, 1),
        })
    return rows


def _order_block() -> dict | None:
    files = sorted(LOG_DIR.glob("*.parquet"))
    if not files:
        return None
    day = date.fromisoformat(files[-1].stem)
    fc = pd.read_parquet(files[-1])
    if not {"_point_key", "_sku_key"}.issubset(fc.columns):
        return None
    fc = fc.copy()
    fc["target_date"] = day
    fc = filter_frame(
        fc, "factory_order", point_col="_point_key", sku_col="_sku_key",
        date_col="target_date")
    if fc.empty:
        return None
    by_pt = {pt: round(q) for pt, q in
             fc.groupby("Точка")[FC].sum().items()}
    novelties = fc[fc.get("_floor", pd.Series(0, index=fc.index)) > 0]
    guard_hits = fc.get("_guard_scale")
    cal = fc.get("_cal_mult")
    return {
        "target_date": day.isoformat(), "weekday": WD[day.weekday()],
        "engine": (fc["engine"].iloc[0] if "engine" in fc.columns
                   else "baseline"),
        "total_units": round(fc[FC].sum()), "rows": len(fc),
        "by_point": by_pt,
        "novelty_rows": int(len(novelties)),
        "novelty_names": sorted(novelties["Позиция"].unique().tolist())[:8],
        "guard_clipped_points": (sorted(fc.loc[guard_hits < 1.0, "Точка"]
                                        .unique().tolist())
                                 if guard_hits is not None else []),
        "calendar_mult": (round(float(cal.iloc[0]), 3)
                          if cal is not None and len(cal) else 1.0),
        "model_cutoff": (str(fc["model_cutoff"].iloc[0])
                         if "model_cutoff" in fc.columns else None),
    }


def build_context() -> dict:
    # ALERT-файл ищем по СЕРВЕРНОЙ дате: его пишет этот же прогон run-daily.ps1
    # минутами раньше, именем с Get-Date сервера. Локальная дата в 22:30
    # серверного уже «завтра» — по ней тревогу не найти.
    alert = PROJ / "logs" / f"ALERT-{date.today().isoformat()}.txt"
    return {
        "generated_at": _local_now().isoformat(timespec="minutes"),
        "yesterday": _yesterday_block(),
        "wape_trend_7d": _trend_block(),
        "today_order": _order_block(),
        "alert_today": (alert.read_text(encoding="utf-8").strip()
                        if alert.exists() else None),
    }


def _fmt_table(ctx: dict) -> list[str]:
    """Детерминированная часть сводки — числа без участия LLM."""
    out = []
    y = ctx["yesterday"]
    if y:
        out += [f"## Числа за {y['date']} ({y['weekday']})", "",
                "| Точка | Прогноз | Продано | Промах |",
                "|---|---|---|---|"]
        for pt, r in y["by_point"].items():
            miss = f"{r['miss_pct']:+.1f}%" if r["miss_pct"] is not None else "—"
            out.append(f"| {pt} | {r['forecast']} | {r['sold']} | {miss} |")
        out.append(f"| **Сеть** | **{y['net_forecast']}** | **{y['net_sold']}** "
                   f"| **{y['net_miss_pct']:+.1f}%** |")
        out.append("")
        money = (f"; цена промаха ≈ {y['miss_money_rub']:,} ₽".replace(",", " ")
                 if y["miss_money_rub"] is not None else "")
        out.append(f"Недодали {y['under_units']} шт, лишнего {y['over_units']} шт, "
                   f"распродано подчистую {y['sellout_rows']} строк{money}.")
    t = ctx["today_order"]
    if t:
        out += ["", f"## Заявка на {t['target_date']} ({t['weekday']})", "",
                f"Движок: {_engine_label(t['engine'])}, "
                f"итого {t['total_units']} шт, позиций {t['rows']}, "
                f"новинок {t['novelty_rows']}."]
        out.append(" · ".join(f"{pt}: {q} шт" for pt, q in t["by_point"].items()))
        if t["guard_clipped_points"]:
            out.append(f"⚠ Аварийный сторож клипал: "
                       f"{', '.join(t['guard_clipped_points'])}")
    if ctx["alert_today"]:
        out += ["", "## ⚠ Тревоги сегодня", "", "```",
                ctx["alert_today"], "```"]
    return out


def render_md(ctx: dict, llm_text: str | None) -> str:
    today = _local_today()      # дата заголовка — локальная, читателя
    lines = [f"# Утренняя сводка — {today:%d.%m.%Y} ({WD[today.weekday()]})", ""]
    if llm_text:
        lines += [llm_text, "", "---", ""]
    else:
        lines += ["*Текст помощника недоступен — числа ниже актуальны.*", ""]
    lines += _fmt_table(ctx)
    lines += ["", f"*Сгенерировано {ctx['generated_at']}; числа считает конвейер, "
              "текст — помощник по готовым числам.*"]
    return "\n".join(lines)


# --- HTML layout for the report -------------------------------
# Светлые токены по умолчанию; тему родителя веб-страница прокидывает атрибутом
# data-theme на <html> (srcdoc в reports.html). Автономное открытие файла без
# атрибута — фолбэк на системную тему через prefers-color-scheme.
_DARK_TOKENS = """
    color-scheme:dark;
    --paper:#1C1C1F;  --wash:#141416;
    --ink:#F1EFEC;    --ink2:#C9C6C1;   --mut:#918D88;
    --line:#2A2A2E;
    --accent:#91B8E8; --accent-dark:#B6D0EF; --accent-soft:#1C3148;
    --amber:#E0A458;  --amber-soft:#332818;
    --row-alt:#202023;
    --grid-line:transparent;
"""

REPORT_CSS = """
  :root{
    color-scheme:light;
    --paper:#FFFFFF;  --wash:#F8F6F3;
    --ink:#141414;    --ink2:#3A3A3A;   --mut:#6E6E6E;
    --line:#E8E4E0;
    --accent:#315A85; --accent-dark:#25486C; --accent-soft:#EAF1F8;
    --amber:#B26B00;  --amber-soft:#FDF3E2;
    --row-alt:#FBFAF8;
    --grid-line:transparent;
  }
  :root[data-theme="dark"]{@DARK@}
  @media (prefers-color-scheme: dark){
    html:not([data-theme="light"]){@DARK@}
  }
  *{box-sizing:border-box}
  body{margin:0; padding:26px 18px 48px; color:var(--ink);
    font:14.5px/1.65 'Segoe UI',system-ui,-apple-system,Roboto,Arial,sans-serif;
    
    background:var(--wash);}
  main{max-width:780px; margin:0 auto}
  h1{font-size:24px; line-height:1.3; margin:0 0 18px; padding-bottom:10px;
    border-bottom:3px solid var(--accent)}
  h2{font-size:18px; margin-top:34px; padding-top:14px; border-top:1px solid var(--line)}
  h3{font-size:15px; margin-top:24px}
  a{color:var(--accent)}
  em{color:var(--mut)}
  code{font:12.5px/1.5 Consolas,monospace; background:var(--paper);
    border:1px solid var(--line); border-radius:4px; padding:0 4px}
  pre{background:var(--paper); border:1px solid var(--line); border-radius:8px;
    padding:12px 14px; overflow-x:auto}
  pre code{border:0; padding:0; background:transparent}
  blockquote{border-left:3px solid var(--accent); margin:14px 0;
    padding:2px 0 2px 14px; color:var(--mut)}
  .tablewrap{overflow-x:auto; background:var(--paper); border:1px solid var(--line);
    border-radius:10px; margin:14px 0}
  table{border-collapse:collapse; width:100%; font-size:13px}
  th,td{border-bottom:1px solid var(--line); padding:8px 12px; text-align:left;
    vertical-align:top}
  th{color:var(--mut); font-weight:600; font-size:11px; text-transform:uppercase;
    letter-spacing:.06em; border-bottom:2px solid var(--ink)}
  td{font-variant-numeric:tabular-nums}
  tbody tr:nth-child(even) td{background:var(--row-alt)}
  tbody tr:last-child td{border-bottom:0}
  li{margin-bottom:5px}
  hr{border:none; border-top:1px solid var(--line); margin:26px 0}
""".replace("@DARK@", _DARK_TOKENS)

REPORT_TPL = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{css}</style></head>
<body><main>{body}</main>
</body></html>"""


def render_html(md_text: str) -> str:
    import markdown                                         # noqa: PLC0415
    body = markdown.markdown(md_text,
                             extensions=["tables", "fenced_code", "sane_lists"])
    body = body.replace("<table>", '<div class="tablewrap"><table>') \
               .replace("</table>", "</table></div>")
    title = md_text.splitlines()[0].lstrip("# ").strip()
    return REPORT_TPL.format(title=title, css=REPORT_CSS, body=body)


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    today = _local_today()      # имена файлов — локальной датой (дата читателя)
    ctx = build_context()
    llm_text = None
    st = llm_settings()
    if st is None:
        print("LLM не настроен или не разрешена передача данных — сводка без текста")
    else:
        # сводке хватает дешёвого яруса модели (LLM_MODEL_CHEAP, если задан)
        st = dict(st, model=st.get("model_cheap") or st["model"])
        llm_text = chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
        ], settings=st)
    ctx["llm"] = bool(llm_text)
    (REPORTS / f"{today}.json").write_text(
        json.dumps(ctx, ensure_ascii=False, indent=1), encoding="utf-8")
    md = render_md(ctx, llm_text)
    (REPORTS / f"{today}.md").write_text(md, encoding="utf-8")
    (REPORTS / f"{today}.html").write_text(render_html(md), encoding="utf-8")
    print(f"сводка: {REPORTS / f'{today}.md'} (LLM: {'да' if llm_text else 'нет'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
