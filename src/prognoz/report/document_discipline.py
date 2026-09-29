# -*- coding: utf-8 -*-
"""Ежедневный отчёт о документной дисциплине точек — ответственному сотруднику в Telegram (запрос 17.09).

Что показывает (по каждой точке за два последних закрытых дня по UTC+10):
отчёт о розничных продажах есть/проведён, чеки загружены, акты списания есть/проведены,
привоз с фабрики есть/проведён; заявка на завтра — в 1С руками, через сервис (кто и когда)
или её нет; правки в кабинете; движок ночного выпуска по горизонтам и тревоги сторожа;
свежесть данных; дни-выбросы продаж (единицы ×N при обычной выручке — как 28–31.08).

Все числа — детерминированно из витрин (та же база, что у движка) и из файлов состояния
клиента, которые ночной прогон забирает по SSH (writes.jsonl, web_edits.sqlite,
nightly-status.json, ALERT-<дата>.txt) в analysis/state/client/. LLM не участвует.

Выход: analysis/output/ai_reports/discipline-<дата>.{json,md,txt}; --send — в Telegram
(tools/tg_notify.py). Запуск: ночной run-daily.ps1 после утренней сводки.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parents[0] / "analysis" / "etl"))
from _db import CONFIG, OUTPUT, connect_reader                  # noqa: E402
from prognoz.scope import canon_cte, sku_filter                 # noqa: E402

PROJ = SRC.parents[0]
REPORTS = OUTPUT / "ai_reports"
STATE = Path(os.environ.get("PROGNOZ_CLIENT_STATE_DIR", "").strip()
             or (PROJ / "analysis" / "state" / "client"))
MANAGERS_FILE = CONFIG / "point_managers.json"
LOCAL_SHIFT_H = 7                                       # сервер МСК, UTC+10 +7
WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
ENGINE_SHORT = {"stacked_guard": "ML-ядро", "ml": "ML-ядро", "catboost": "ML-ядро",
                "baseline": "формула КВП", "mixed": "смешанный"}
OK, WARN, MISS, NA = "✅", "🟡", "❌", "—"
ANOMALY_UNITS_PER_LINE = 1.8     # шт на строку чека ≥ ×1,8 медианы → выброс
ANOMALY_RUB_PER_UNIT = 0.65      # ₽ за штуку ≤ ×0,65 медианы при том же → выброс


def local_today() -> date:
    return (datetime.now() + timedelta(hours=LOCAL_SHIFT_H)).date()


def _dlist(days: list[date]) -> str:
    return ", ".join(f"DATE '{d.isoformat()}'" for d in days)


def load_managers() -> dict:
    if not MANAGERS_FILE.exists():
        return {}
    return json.loads(MANAGERS_FILE.read_text(encoding="utf-8")).get("points", {})


def points(con) -> pd.DataFrame:
    return con.execute("""
        SELECT point_key, canon_name FROM mart.dim_point
        WHERE role IN ('market', 'market_hub') AND canon_name IS NOT NULL
        ORDER BY canon_name
    """).fetchdf()


def day_matrix(con, days: list[date]) -> pd.DataFrame:
    """Точка × день: наличие и проведение документов четырёх видов."""
    dl = _dlist(days)
    return con.execute(f"""
        WITH {canon_cte()},
        pts AS (SELECT point_key AS pt, canon_name FROM mart.dim_point
                WHERE role IN ('market', 'market_hub') AND canon_name IS NOT NULL),
        dd AS (SELECT unnest([{dl}]) AS d),
        sl AS (
            SELECT c.pt, f.sale_date AS d, max(f.n_docs) > 0 AS has_doc,
                   bool_and(f.all_posted) AS posted, sum(f.qty_sold) AS units
            FROM mart.fact_sales_daily f JOIN c ON c.point_key = f.point_key
            JOIN mart.dim_sku s ON s.sku_key = f.sku_key
            WHERE {sku_filter('s')} AND f.sale_date IN ({dl}) GROUP BY 1, 2),
        ch AS (
            SELECT c.pt, h.trade_day AS d, count(*) AS n, sum(h.total) AS rub
            FROM mart.cheki_headers h JOIN c ON c.point_key = h.point_key
            WHERE h.posted AND h.op = 'Продажа' AND h.trade_day IN ({dl}) GROUP BY 1, 2),
        wo AS (
            SELECT c.pt, w.wo_date AS d, sum(w.n_docs) AS n, bool_and(w.all_posted) AS posted
            FROM mart.fact_writeoff_daily w JOIN c ON c.point_key = w.point_key
            JOIN mart.dim_sku s ON s.sku_key = w.sku_key
            WHERE {sku_filter('s')} AND w.wo_date IN ({dl}) GROUP BY 1, 2),
        sp AS (
            SELECT c.pt, s.supply_date AS d, sum(s.qty) AS qty, bool_and(s.all_posted) AS posted
            FROM mart.fact_supply_daily s JOIN c ON c.point_key = s.point_key
            WHERE s.supply_date IN ({dl}) GROUP BY 1, 2),
        od AS (
            SELECT c.pt, o.trade_date AS d, sum(o.qty_ordered) AS qty,
                   bool_and(o.all_posted) AS posted, sum(o.n_docs) AS n
            FROM mart.fact_orders_daily o JOIN c ON c.point_key = o.point_key
            WHERE o.channel = 'transfer' AND o.trade_date IN ({dl}) GROUP BY 1, 2)
        SELECT pts.canon_name AS point, pts.pt AS point_key, dd.d,
               coalesce(sl.has_doc, false) AS sales_has, sl.posted AS sales_posted,
               coalesce(sl.units, 0) AS sales_units,
               coalesce(ch.n, 0) AS checks_n, coalesce(ch.rub, 0) AS checks_rub,
               coalesce(wo.n, 0) AS wo_docs, wo.posted AS wo_posted,
               coalesce(sp.qty, 0) AS supply_qty, sp.posted AS supply_posted,
               coalesce(od.qty, 0) AS order_qty, od.posted AS order_posted, coalesce(od.n, 0) AS order_docs
        FROM pts CROSS JOIN dd
        LEFT JOIN sl ON sl.pt = pts.pt AND sl.d = dd.d
        LEFT JOIN ch ON ch.pt = pts.pt AND ch.d = dd.d
        LEFT JOIN wo ON wo.pt = pts.pt AND wo.d = dd.d
        LEFT JOIN sp ON sp.pt = pts.pt AND sp.d = dd.d
        LEFT JOIN od ON od.pt = pts.pt AND od.d = dd.d
        ORDER BY 1, 3
    """).fetchdf()


def freshness(con) -> dict:
    """До какого дня загружены документы. Даты из будущего (документ, датированный
    31.12) не считаются. «loaded» — граница загруженных данных: по чекам (самый
    оперативный поток); дни после неё — «ещё не загружено», а не «документа нет»."""
    q = lambda sql: con.execute(sql).fetchone()[0]           # noqa: E731
    out = {
        "sales": q("SELECT max(sale_date) FROM mart.fact_sales_daily "
                   "WHERE n_docs > 0 AND sale_date <= current_date"),
        "checks": q("SELECT max(trade_day) FROM mart.cheki_headers "
                    "WHERE posted AND trade_day <= current_date"),
        "writeoffs": q("SELECT max(wo_date) FROM mart.fact_writeoff_daily WHERE wo_date <= current_date"),
        "supply": q("SELECT max(supply_date) FROM mart.fact_supply_daily "
                    "WHERE supply_date <= current_date"),
    }
    out["loaded"] = out["checks"] or out["sales"]
    return out


def orders_for(con, today: date, tomorrow: date) -> pd.DataFrame:
    """Заявки на перемещение «на завтра» по точкам: руками (без нашего комментария)
    и через сервис. Считаются документы с датой перемещения = завтра ЛИБО созданные
    сегодня (менеджеры часто оставляют дату документа = сегодня)."""
    return con.execute(f"""
        WITH {canon_cte()}
        SELECT c.pt AS point_key,
               count(DISTINCT t.doc_key) FILTER (WHERE NOT starts_with(lower(coalesce(t.comment, '')), 'автозаявка')) AS manual_docs,
               bool_and(t.posted) FILTER (WHERE NOT starts_with(lower(coalesce(t.comment, '')), 'автозаявка')) AS manual_posted,
               sum(t.qty_ordered) FILTER (WHERE NOT starts_with(lower(coalesce(t.comment, '')), 'автозаявка')) AS manual_qty,
               count(DISTINCT t.doc_key) FILTER (WHERE starts_with(lower(coalesce(t.comment, '')), 'автозаявка')) AS service_docs
        FROM mart.transfer_order_lines t JOIN c ON c.point_key = t.point_key
        WHERE NOT t.deleted
          AND (CAST(t.move_date AS DATE) = ? OR CAST(t.doc_date AS DATE) = ?)
        GROUP BY 1
    """, [tomorrow, today]).fetchdf().set_index("point_key")


def anomaly_days(con, upto: date, window: int = 7, base: int = 28) -> list[dict]:
    """Дни-выбросы по сети: штук на строку чека ≥ ×1,8 медианы ИЛИ ₽/шт ≤ ×0,65 медианы
    (сигнатура 28–31.08: количество удвоилось при обычной выручке)."""
    since = upto - timedelta(days=window + base)
    df = con.execute(f"""
        WITH {canon_cte()}
        SELECT cs.trade_day AS d, sum(cs.qty) AS units, sum(cs.amount) AS rub, count(*) AS lines
        FROM mart.cheki_sales cs JOIN c ON c.point_key = cs.point_key
        JOIN mart.dim_sku s ON s.sku_key = cs.sku_key
        WHERE {sku_filter('s')} AND cs.trade_day BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1
    """, [since, upto]).fetchdf()
    if df.empty:
        return []
    df["upl"] = df["units"] / df["lines"].clip(lower=1)
    df["rpu"] = df["rub"] / df["units"].clip(lower=1)
    df["d"] = pd.to_datetime(df["d"]).dt.date
    edge = upto - timedelta(days=window)
    hist = df[df["d"] <= edge]
    if len(hist) < 7:
        return []
    med_upl, med_rpu = float(hist["upl"].median()), float(hist["rpu"].median())
    out = []
    for r in df[df["d"] > edge].itertuples():
        bad_upl = r.upl >= ANOMALY_UNITS_PER_LINE * med_upl
        bad_rpu = r.rpu <= ANOMALY_RUB_PER_UNIT * med_rpu
        if bad_upl or bad_rpu:
            out.append({"date": r.d.isoformat(), "units_per_line": round(float(r.upl), 2),
                        "median_units_per_line": round(med_upl, 2),
                        "rub_per_unit": round(float(r.rpu)), "median_rub_per_unit": round(med_rpu),
                        "units": int(r.units)})
    return out


# --- состояние клиента (файлы, забранные по SSH) ------------------------------

def client_writes(targets: list[date]) -> dict[tuple[str, str], dict]:
    """(target_date, point_key) → последняя успешная запись в 1С (с учётом voided)."""
    path = STATE / "writes.jsonl"
    done: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return done
    want = {d.isoformat() for d in targets}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (str(rec.get("target_date")), str(rec.get("point_key")))
        if key[0] not in want:
            continue
        if rec.get("status") == "ok":
            done[key] = rec
        elif rec.get("status") == "voided":
            done.pop(key, None)
    return done


def client_edits(targets: list[date]) -> dict[tuple[str, str], dict]:
    """(target_date, point_key) → {n, actors, last} по журналу правок кабинета."""
    path = STATE / "web_edits.sqlite"
    out: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return out
    marks = ", ".join("?" for _ in targets)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            rows = con.execute(
                f"SELECT target_date, point_key, actor, count(*), max(edited_at) FROM edits "
                f"WHERE target_date IN ({marks}) AND qty_user IS NOT NULL "
                f"GROUP BY 1, 2, 3", [d.isoformat() for d in targets]).fetchall()
    except sqlite3.Error:
        return out
    for target, point_key, actor, n, last in rows:
        cur = out.setdefault((str(target), str(point_key)), {"n": 0, "actors": set(), "last": ""})
        cur["n"] += int(n)
        if actor:
            cur["actors"].add(str(actor))
        cur["last"] = max(cur["last"], str(last or ""))
    for cur in out.values():
        cur["actors"] = sorted(cur["actors"])
    return out


def client_status() -> dict:
    path = STATE / "nightly-status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def client_alerts(today: date) -> list[str]:
    out: list[str] = []
    for d in (today, today - timedelta(days=1)):
        path = STATE / f"ALERT-{d.isoformat()}.txt"
        if path.exists():
            out += [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip()]
    seen: list[str] = []
    for ln in out:
        if ln not in seen:
            seen.append(ln)
    return seen[:6]


# --- сборка контекста и текста ---------------------------------------------------

def _b(value) -> bool:
    """bool без сюрпризов pandas: NA/None → False."""
    try:
        return bool(value) if value is not None and not pd.isna(value) else False
    except (TypeError, ValueError):
        return bool(value)


def _mark(has: bool, posted, na: bool) -> str:
    if na:
        return NA
    if not has:
        return MISS
    return OK if _b(posted) else WARN


def build_context(today: date | None = None) -> dict:
    today = today or local_today()
    days = [today - timedelta(days=2), today - timedelta(days=1)]
    tomorrow = today + timedelta(days=1)
    con = connect_reader()
    try:
        fresh = freshness(con)
        mat = day_matrix(con, days + [tomorrow])
        anomalies = anomaly_days(con, upto=today - timedelta(days=1))
        pts = points(con)
        ords = orders_for(con, today, tomorrow)
    finally:
        con.close()
    fresh = {k: (pd.Timestamp(v).date() if v is not None else None) for k, v in fresh.items()}
    loaded = fresh["loaded"]
    managers = load_managers()
    writes = client_writes([tomorrow, today])
    edits = client_edits([tomorrow, today])
    status = client_status()
    rows = []
    for pt in pts.itertuples():
        meta = managers.get(pt.point_key, {})
        m = mat[mat["point_key"] == pt.point_key].set_index(pd.to_datetime(mat.loc[mat["point_key"] == pt.point_key, "d"]).dt.date)
        per_day = []
        for d in days:
            r = m.loc[d] if d in m.index else None
            g = (lambda col: r[col] if r is not None else None)
            na = loaded is None or d > loaded
            per_day.append({
                "date": d.isoformat(),
                "sales": _mark(_b(g("sales_has")), g("sales_posted"), na),
                "checks": _mark(float(g("checks_n") or 0) > 0, True, na),
                "writeoffs": _mark(float(g("wo_docs") or 0) > 0, g("wo_posted"), na),
                "supply": _mark(float(g("supply_qty") or 0) > 0, g("supply_posted"), na),
                "sales_units": int(float(g("sales_units") or 0)),
                "checks_n": int(float(g("checks_n") or 0)),
            })
        o = ords.loc[pt.point_key] if pt.point_key in ords.index else None
        w = writes.get((tomorrow.isoformat(), pt.point_key))
        e = edits.get((tomorrow.isoformat(), pt.point_key))
        rows.append({
            "point": pt.canon_name, "point_key": pt.point_key,
            "short": meta.get("short", pt.canon_name), "managers": meta.get("managers", []),
            "days": per_day,
            "order_tomorrow": {
                "in_1c": bool(o is not None and float(o["manual_docs"] or 0) > 0),
                "posted": _b(o["manual_posted"]) if o is not None else False,
                "qty": int(float(o["manual_qty"] or 0)) if o is not None else 0,
                "service_docs_in_1c": int(float(o["service_docs"] or 0)) if o is not None else 0,
                "via_service": ({"actor": w.get("actor"), "at": w.get("ts", "")[:16], "rows": w.get("rows")}
                                if w else None),
                "edits": ({"n": e["n"], "actors": e["actors"]} if e else None),
            },
        })
    return {
        "date": today.isoformat(), "weekday": WD[today.weekday()],
        "days": [d.isoformat() for d in days], "tomorrow": tomorrow.isoformat(),
        "freshness": {k: (v.isoformat() if v else None) for k, v in fresh.items()},
        "nightly": status, "alerts": client_alerts(today),
        "anomalies": anomalies, "points": rows,
    }


def _d(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{WD[d.weekday()]} {d:%d.%m}"


def render_text(ctx: dict) -> str:
    L = [f"📋 сети точек питания · документы · {_d(ctx['date'])}"]
    f = ctx["freshness"]
    fmt = lambda v: date.fromisoformat(v).strftime("%d.%m") if v else "нет"   # noqa: E731
    L.append(f"Данные в базе загружены по {fmt(f['loaded'])} (продажи по {fmt(f['sales'])}, "
             f"чеки по {fmt(f['checks'])}, акты списания по {fmt(f['writeoffs'])}, "
             f"привоз по {fmt(f['supply'])})")
    st = ctx.get("nightly") or {}
    if st:
        engines = st.get("engines") or {}
        if engines:
            parts = " · ".join(f"{h} {ENGINE_SHORT.get(str(e), str(e))}" for h, e in engines.items())
        else:
            parts = ENGINE_SHORT.get(str(st.get("engine")), str(st.get("engine")))
        L.append(f"Ночной выпуск {fmt(st.get('issue_date'))}: {parts}")
    for a in ctx.get("alerts", [])[:3]:
        L.append(f"⚠ {a[:160]}")
    d1, d2 = ctx["days"]
    L.append("")
    L.append(f"За {_d(d1)} и {_d(d2)} — продажи · чеки · списания · привоз:")
    for p in ctx["points"]:
        who = f" ({', '.join(m.split(' (')[0] for m in p['managers'])})" if p["managers"] else ""
        cells = []
        for key in ("sales", "checks", "writeoffs", "supply"):
            cells.append("".join(day[key] for day in p["days"]))
        L.append(f"{p['short']}{who}: " + " · ".join(cells))
    L.append(f"{OK} проведён · {WARN} есть, не проведён · {MISS} документа нет · {NA} данные ещё не загружены")
    L.append("")
    L.append(f"Заявки на {_d(ctx['tomorrow'])}:")
    for p in ctx["points"]:
        o = p["order_tomorrow"]
        bits = []
        if o["via_service"]:
            vs = o["via_service"]
            bits.append(f"в 1С через сервис ({vs.get('actor') or '?'}, {vs.get('at', '')[11:16]} UTC, {vs.get('rows')} строк)")
        elif o.get("service_docs_in_1c"):
            bits.append("в 1С через сервис")
        if o["in_1c"]:
            bits.append(f"в 1С руками, {o['qty']} шт" + ("" if o["posted"] else ", не проведена"))
        if not bits:
            bits.append(f"{MISS} заявки в 1С нет")
        if o["edits"]:
            bits.append(f"правок в кабинете {o['edits']['n']}")
        L.append(f"{p['short']}: " + "; ".join(bits))
    an = ctx.get("anomalies") or []
    L.append("")
    if an:
        L.append("Выбросы продаж за 7 дней (штук на чек ×, ₽/шт ↓ — проверить промо/учёт): " + ", ".join(
            f"{date.fromisoformat(a['date']):%d.%m} ({a['units_per_line']} шт/строку при медиане {a['median_units_per_line']})"
            for a in an))
    else:
        L.append("Выбросов продаж за 7 дней нет.")
    return "\n".join(L)


def render_md(ctx: dict, text: str) -> str:
    rows = ["| Точка | " + " | ".join(f"{_d(d)} прод/чеки/спис/привоз" for d in ctx["days"]) + " |",
            "|---|" + "---|" * len(ctx["days"])]
    for p in ctx["points"]:
        cells = [" ".join(day[k] for k in ("sales", "checks", "writeoffs", "supply")) for day in p["days"]]
        rows.append(f"| {p['point']} | " + " | ".join(cells) + " |")
    return (f"# Документная дисциплина — {_d(ctx['date'])}\n\n```\n{text}\n```\n\n"
            + "\n".join(rows) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat, help="дата отчёта (по UTC+10)")
    parser.add_argument("--send", action="store_true", help="отправить в Telegram")
    parser.add_argument("--print", action="store_true", help="напечатать текст")
    args = parser.parse_args()
    ctx = build_context(args.date)
    text = render_text(ctx)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / f"discipline-{ctx['date']}"
    stem.with_suffix(".json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    stem.with_suffix(".txt").write_text(text, encoding="utf-8")
    stem.with_suffix(".md").write_text(render_md(ctx, text), encoding="utf-8")
    if args.print or not args.send:
        print(text)
    if args.send:
        sys.path.insert(0, str(PROJ / "tools"))
        from tg_notify import send                          # noqa: PLC0415
        try:
            ids = send(text, save=True)
            print(f"Telegram: отправлено {len(ids)} сообщений")
        except RuntimeError as exc:
            print(f"Telegram: не отправлено — {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
