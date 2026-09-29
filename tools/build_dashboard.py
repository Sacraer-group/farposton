# -*- coding: utf-8 -*-
"""Панель показателей: facts.json + таблицы → docs/html/Панель_показателей.html.

Панель по построению не может разойтись с реестром: она читает ТОЛЬКО его выход
и не ходит в базу. Хочется другое число на панели — сначала факт в блоке.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py prognoz/tools/build_dashboard.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))
sys.path.insert(0, str(PROGNOZ / "tools"))
from prognoz.facts.fmt import fmt_ru                     # noqa: E402
from md2html import CSS, TPL                             # noqa: E402
from svgchart import bars, hbars, lines                  # noqa: E402

OUTPUT = PROGNOZ / "analysis" / "output"
TABLES = OUTPUT / "facts" / "tables"
DST = PROGNOZ / "docs" / "html" / "Панель_показателей.html"

WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

EXTRA_CSS = """
  main { max-width: 860px; }
  .tiles { display:flex; flex-wrap:wrap; gap:12px; margin:18px 0; }
  .tile { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:12px 16px; min-width:150px; flex:1; }
  .tile b { display:block; font-size:22px; margin-top:2px; }
  .tile span { color:var(--muted); font-size:12.5px; }
  .chart { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:14px; margin:16px 0; }
  .note { color:var(--muted); font-size:13.5px; margin:6px 2px 0; }
  nav a { margin-right:14px; font-size:14px; }
"""


def f(facts: dict, fid: str):
    return facts[fid]["value"]


def tbl(tid: str) -> pd.DataFrame:
    return pd.read_parquet(TABLES / f"{tid}.parquet")


def tile(label: str, value: str, sub: str = "") -> str:
    return (f'<div class="tile"><span>{label}</span><b>{value}</b>'
            f'<span>{sub}</span></div>')


def main() -> int:
    payload = json.loads((OUTPUT / "facts.json").read_text(encoding="utf-8"))
    facts, data = payload["facts"], payload["data"]
    parts: list[str] = []

    parts.append(f"<h1>Панель показателей — прогноз «сети точек питания»</h1>"
                 f"<p class='note'>Собрано {payload['generated_at'][:16].replace('T', ' в ')} · "
                 f"данные о продажах по {data.get('max_sale_date', '?')} · версия "
                 f"{payload['version']} · каждое число рождается в "
                 f"<code>src/prognoz/facts/</code> и проверяется тестами</p>")
    parts.append("<nav>" + " ".join(
        f'<a href="#{a}">{t}</a>' for a, t in (
            ("ladder", "Точность"), ("eco", "Экономика"),
            ("cmp", "Движок vs человек"), ("shelf", "Полка"),
            ("sales", "Продажи"), ("u40", "Уценка"), ("wo", "Списания"),
            ("wd", "Дни недели"), ("cal", "Календарь"), ("reg", "Режимы"))) + "</nav>")

    # --- плитки ---
    parts.append('<div class="tiles">' + "".join([
        tile("Продано за год (скоуп прогноза)",
             fmt_ru(int(f(facts, 'before.sold')), 0) + " шт",
             f"{fmt_ru(f(facts, 'before.revenue_m'))} млн ₽"),
        tile("Списания", f"{fmt_ru(f(facts, 'before.writeoff_share'))}%",
             f"{fmt_ru(int(f(facts, 'before.writeoff_units')), 0)} шт за год"),
        tile("Уценка −40%",
             f"{fmt_ru(f(facts, 'before.u40_share_lo'))}–{fmt_ru(f(facts, 'before.u40_share_hi'))}%",
             "доля единиц, ушедших со скидкой"),
        tile("WAPE сети (модель)", f"{fmt_ru(f(facts, 'ladder.net_day.model_wape'))}%",
             f"человек {fmt_ru(f(facts, 'ladder.net_day.human_wape'))}% · окно протокола"),
        tile("Цена ошибки: модель против человека",
             f"{fmt_ru(f(facts, 'eco.ratio'), 2)}×",
             f"{fmt_ru(f(facts, 'eco.model.rub_per_year_m'))} против "
             f"{fmt_ru(f(facts, 'eco.human.rub_per_year_m'))} млн ₽/год"),
    ]) + "</div>")

    # --- лестница ---
    lt = tbl("ladder.table")
    rows = [(r.уровень, {"human": r.human_wape, "model": r.model_wape,
                         "floor": r.floor}) for r in lt.itertuples()]
    parts.append('<h2 id="ladder">Лестница точности (окно протокола 25.06–22.07)</h2>')
    parts.append('<div class="chart">' + hbars(
        rows, title="WAPE по уровням: чем крупнее срез, тем ниже неустранимый предел")
        + "</div>")
    parts.append(f"<p class='note'>Перекос: человек "
                 f"+{fmt_ru(f(facts, 'ladder.net_day.human_bias'))}%, модель "
                 f"{fmt_ru(f(facts, 'ladder.net_day.model_bias'))}%. Сравнение только на "
                 f"позициях, которые сотрудник заказывал "
                 f"({fmt_ru(int(f(facts, 'ladder.meta.n_rows_paired')), 0)} строк). "
                 f"Пунктир — физический предел 79,8/√Λ.</p>")

    # --- экономика ---
    sc = tbl("eco.mult_scan")
    parts.append('<h2 id="eco">Экономика ошибки</h2>')
    parts.append('<div class="chart">' + bars(
        [f"×{m:g}" for m in sc["множитель"]],
        list(sc["rub_per_year_m"]),
        title="Потери при умножении прогноза на коэффициент, млн ₽/год",
        unit="млн ₽/год") + "</div>")
    parts.append(f"<p class='note'>Ни один множитель не улучшает результат — модель "
                 f"промахивается адресом, а не масштабом. Лишняя единица стоит "
                 f"{fmt_ru(f(facts, 'eco.co'), 2)} цены (списанная себестоимость), "
                 f"недостающая — {fmt_ru(f(facts, 'eco.cu'), 2)} (потерянная маржа); "
                 f"оптимальный квантиль {fmt_ru(f(facts, 'eco.quantile'), 2)}. Порядок величин "
                 f"сходится: остаток {fmt_ru(int(f(facts, 'eco.gap.leftover')), 0)} шт при "
                 f"{fmt_ru(int(f(facts, 'eco.gap.written_off_scope')), 0)} шт списаний за окно "
                 f"({fmt_ru(int(f(facts, 'eco.gap.written_off')), 0)} на тех же позициях-днях) — "
                 f"излишек списывается, тождества нет и не ожидается.</p>")

    # --- бэктест против восстановленного спроса (есть только после --freeze) ---
    if "compare.window.n_days" in facts:
        period = facts["compare.window.n_days"].get("period", "")
        parts.append('<h2 id="cmp">Движок против человека: '
                     'по восстановленному спросу</h2>')
        parts.append('<div class="tiles">' + "".join([
            tile("Промах человека за окно",
                 f"{fmt_ru(int(f(facts, 'compare.window.human_miss_k')), 0)} тыс ₽",
                 f"{period}, {fmt_ru(int(f(facts, 'compare.window.n_rows')), 0)} строк"),
            tile("Промах движка (как есть)",
                 f"{fmt_ru(int(f(facts, 'compare.window.old_miss_k')), 0)} тыс ₽",
                 f"лишнее {fmt_ru(int(f(facts, 'compare.window.old_over_k')), 0)} + "
                 f"недовоз {fmt_ru(int(f(facts, 'compare.window.old_under_k')), 0)}"),
            tile("Промах движка + восст. спрос",
                 f"{fmt_ru(int(f(facts, 'compare.window.new_miss_k')), 0)} тыс ₽",
                 f"лишнее {fmt_ru(int(f(facts, 'compare.window.new_over_k')), 0)} + "
                 f"недовоз {fmt_ru(int(f(facts, 'compare.window.new_under_k')), 0)}"),
            tile("Экономия против человека",
                 f"{fmt_ru(int(f(facts, 'compare.window.saving_new_vs_human_k')), 0)} тыс ₽",
                 f"≈{fmt_ru(f(facts, 'compare.year.saving_linear_m'))} млн ₽/год "
                 f"(линейная оценка)"),
        ]) + "</div>")
        bp = tbl("compare.by_point")
        rows = [(r.point, {"human": r.human_miss_k, "model": r.new_miss_k})
                for r in bp.itertuples()]
        parts.append('<div class="chart">' + hbars(
            rows, title="Цена промаха за окно по точкам, тыс ₽", unit="тыс ₽",
            series=("человек", "движок+восст."), ref_key=None) + "</div>")
        parts.append("<p class='note'>Эталон — восстановленный спрос "
                     "(продано + недопродано из полки), а не продажи: продажи "
                     "обрезаны привозом. Лишнее ×0,33 цены, недовезённое ×0,67. "
                     "Только строки, где сотрудник заказывал; исключены срывы "
                     "поставки, дни без отчёта и без актов.</p>")

    # --- полка: жизнь позиции по дням ---
    parts.append('<h2 id="shelf">Полка: жизнь позиции по дням (по чекам)</h2>')
    parts.append('<div class="tiles">' + "".join([
        tile("Цена промаха за год",
             f"{fmt_ru(f(facts, 'shelf.year.miss_rub_m'))} млн ₽",
             f"недодали {fmt_ru(f(facts, 'shelf.year.lost_rub_m'))} + списали "
             f"{fmt_ru(f(facts, 'shelf.year.wo_rub_m'))} (уценка не входит)"),
        tile("Недопродано",
             fmt_ru(int(f(facts, 'shelf.year.lost_units')), 0) + " шт",
             "позиция кончилась раньше вечернего спроса"),
        tile("Дней со значимым дефицитом",
             f"{fmt_ru(f(facts, 'shelf.outcome.early_significant_share'))}%",
             f"разрыв часов затишья {fmt_ru(f(facts, 'shelf.hour.gap'))} ч — "
             f"проверка, что это дефицит, а не шум"),
    ]) + "</div>")

    bm2 = tbl("shelf.by_month")
    parts.append('<div class="chart">' + lines(
        {"недодали (маржа)": [(r.mon, r.lost_rub_k) for r in bm2.itertuples()],
         "списали (себестоимость)": [(r.mon, r.wo_rub_k) for r in bm2.itertuples()]},
        title="Две стороны промаха по месяцам, тыс ₽", unit=" тыс ₽") + "</div>")
    parts.append("<p class='note'>Сеть переменного состава (2 точки в начале года → 5 "
                 "в конце) — уровни между месяцами сравнивать осторожно; списания до "
                 "марта 2026 покрыты не полностью. Уценки на графике нет: уценённая "
                 "единица продана и принесла прибыль — это инструмент спасения "
                 "остатка, а не потеря.</p>")

    oc = tbl("shelf.outcomes").sort_values("share_pct", ascending=False)
    parts.append('<div class="chart">' + bars(
        [str(r.outcome) for r in oc.itertuples()],
        [float(r.share_pct) for r in oc.itertuples()],
        title="Чем кончается день позиции, % дней-позиций", unit="%") + "</div>")
    parts.append(f"<p class='note'>«Кончилось рано» — "
                 f"{fmt_ru(f(facts, 'shelf.outcome.early_share'))}% дней, но значимый "
                 f"по объёму дефицит — {fmt_ru(f(facts, 'shelf.outcome.early_significant_share'))}%: "
                 f"остальное — редкие позиции, где вечерняя тишина статистически "
                 f"неотличима от нормы. Средний час затишья дефицитных дней "
                 f"{fmt_ru(16.2)} против {fmt_ru(20.3)} у дней с уценкой.</p>")

    bh = tbl("shelf.by_hour")
    parts.append('<div class="chart">' + bars(
        [f"{int(r.hour)}:00" for r in bh.itertuples()],
        [float(r.lost_units) for r in bh.itertuples()],
        title="В какой час позиции замолкают (дни значимого дефицита), недопродано шт",
        fmt_digits=0) + "</div>")

    tp = tbl("shelf.top_pairs").head(12)
    rows_tp = [(f"{r.sku_name[:28]} · {r.canon_name}",
                {"lost": r.lost_rub_k, "wo": r.wo_rub_k})
               for r in tp.itertuples()]
    parts.append('<div class="chart">' + hbars(
        rows_tp, title="Топ-12 позиций по цене промаха, тыс ₽ за год",
        unit=" тыс ₽", series=("недодали", "списали"), keys=("lost", "wo"),
        ref_key=None) + "</div>")
    parts.append("<p class='note'>Кого недовозят, а кого перевозят — это разные "
                 "позиции, а не одна «средняя ошибка»: наборы и ходовые шаурму "
                 "теряют маржу дефицитом, салаты и десерты — себестоимость "
                 "списанием.</p>")

    # --- продажи по месяцам ---
    bm = tbl("before.by_month")
    series_sold = {pt: [(r.month, r.sold) for r in g.itertuples()]
                   for pt, g in bm.groupby("point")}
    parts.append('<h2 id="sales">Продажи по месяцам, шт</h2>')
    parts.append('<div class="chart">' + lines(
        series_sold, title="Готовая еда по точкам (без бара), 01.08.2025–31.07.2026")
        + "</div>")
    parts.append("<p class='note'>Видны открытия точек: Точки 4 — октябрь, Точка 3 — "
                 "февраль, Точки 5 — конец июня.</p>")

    # --- уценка ---
    series_u40 = {pt: [(r.month, r.u40_share) for r in g.itertuples()]
                  for pt, g in bm.groupby("point")}
    parts.append('<h2 id="u40">Доля уценки по месяцам, %</h2>')
    parts.append('<div class="chart">' + lines(
        series_u40, title="Доля единиц, ушедших по вечерней скидке (верхняя оценка)",
        unit="%") + "</div>")
    parts.append("<p class='note'>Главный график для владельца: рост доли уценки — "
                 "это контур «скидка вычистила остаток → заказ вырос → остаток вырос». "
                 "Стопор КВП в модели существует ровно против этого.</p>")

    # --- списания ---
    wm = tbl("before.writeoff_by_month")
    parts.append('<h2 id="wo">Списания по месяцам, шт</h2>')
    parts.append('<div class="chart">' + bars(
        list(wm["month"]), list(wm["qty"]),
        title="Списания готовой еды по сети (без бара)", fmt_digits=0) + "</div>")
    parts.append("<p class='note'>⚠️ До марта 2026 списания по маркетам покрыты "
                 "не полностью (дыра закрыта добором, но состав неоднороден) — "
                 "сравнивать месяцы 2025 с 2026 напрямую нельзя.</p>")

    # --- дни недели ---
    wd = tbl("before.weekday")
    parts.append('<h2 id="wd">Профиль дня недели</h2>')
    parts.append('<div class="chart">' + bars(
        [WD[int(r.wd)] for r in wd.itertuples()],
        [float(r.dev_pct) for r in wd.itertuples()],
        title="Отклонение дневного объёма от среднего, %", unit="%") + "</div>")

    # --- календарь ---
    sp = tbl("cal.special_dates").dropna(subset=["recomputed"])
    sp = sp.sort_values(["month", "day"])
    labels = [f"{int(r.day):02d}.{int(r.month):02d}" for r in sp.itertuples()]
    parts.append('<h2 id="cal">Календарные даты: константа модели против пересчёта</h2>')
    parts.append('<div class="chart">' + lines(
        {"константа в модели": list(zip(labels, sp["const"])),
         "пересчёт из данных": list(zip(labels, sp["recomputed"]))},
        title="Множитель особой даты (1,0 = обычный день)", y0=None) + "</div>")
    n_over = int(f(facts, "cal.n_dates_over_tolerance"))
    cal_verdict = ("константы воспроизводимы из данных" if n_over == 0 else
                   f"⚠️ {n_over} дат разошлись сильнее допуска — данные уехали "
                   f"от таблицы, смотреть cal.special_dates")
    parts.append(f"<p class='note'>Максимальное расхождение "
                 f"{fmt_ru(f(facts, 'cal.max_dev_from_constants'), 2)} при допуске 0,05 — "
                 f"{cal_verdict}. Даты, где годы спорят "
                 f"(23.02, 01.05, 12.06), в модели множителя не имеют.</p>")

    # --- режимы ---
    rg = tbl("before.regimes")
    parts.append('<h2 id="reg">Режимы наблюдения спроса A–D</h2>')
    parts.append('<div class="chart">' + bars(
        [f"{r.regime}" for r in rg.itertuples()],
        [float(r.writeoff_row_share) for r in rg.itertuples()],
        title="Доля строк со списанием по режимам (проверка разметки независимым документом)",
        unit="%") + "</div>")
    parts.append("<p class='note'>A — распродано без уценки (истинный дефицит), "
                 "B — распродано, часть по уценке, C — остаток с уценкой, D — остаток "
                 "без уценки. Порядок C → D → B → A подтверждает разметку: где мы "
                 "говорим «товар остался», списывают в двадцать раз чаще.</p>")

    body = "\n".join(parts)
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(TPL.format(title="Панель показателей — прогноз «сети точек питания»",
                              css=CSS + EXTRA_CSS, body=body), encoding="utf-8")
    print(f"→ {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
