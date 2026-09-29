from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from web import svgchart


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"


def test_web_line_chart_is_accessible_and_distinguishes_series() -> None:
    svg = svgchart.lines(
        {
            "прогноз ПО": [("24.08", 12.0), ("25.08", 14.0)],
            "факт заявки": [("24.08", 11.0), ("25.08", 15.0)],
            "продажи": [("24.08", 10.0), ("25.08", 13.0)],
        },
        title="Прогноз, заявка и продажи",
        unit="шт",
    )

    root = ET.fromstring(svg)
    assert root.attrib["role"] == "img"
    assert root.attrib["aria-label"] == "Прогноз, заявка и продажи"
    assert root.find("{http://www.w3.org/2000/svg}title") is not None
    assert "var(--chart-1,#2563EB)" in svg
    assert "var(--chart-2,#D97706)" in svg
    assert "var(--chart-3,#059669)" in svg
    assert 'stroke-dasharray="9 5"' in svg
    assert 'stroke-dasharray="3 4"' in svg
    assert "24.08 · прогноз ПО: 12,0шт" in svg
    assert "<polygon" in svg


def test_dark_theme_primary_buttons_have_readable_text() -> None:
    css = (STATIC / "assets" / "app.css").read_text(encoding="utf-8")
    login = (STATIC / "login.html").read_text(encoding="utf-8")

    def luminance(color: str) -> float:
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
        return sum(x * weight for x, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    contrast = (luminance("#91B8E8") + 0.05) / (luminance("#141416") + 0.05)
    assert contrast >= 4.5
    assert '.btn:not(.sec){color:#141416}' in css
    assert 'button{color:#141416}' in login


def test_web_bar_chart_exposes_exact_value_on_hover() -> None:
    svg = svgchart.bars(["24.08", "25.08"], [12.5, 8.0], title="WAPE", unit="%")

    ET.fromstring(svg)
    assert "24.08: 12,5%" in svg


def test_chart_and_table_controls_are_wired_on_all_analytics_pages() -> None:
    accuracy = (STATIC / "accuracy.html").read_text(encoding="utf-8")
    comparison = (STATIC / "comparison.html").read_text(encoding="utf-8")
    analytics = (STATIC / "analytics.html").read_text(encoding="utf-8")
    app_js = (STATIC / "assets" / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "assets" / "app.css").read_text(encoding="utf-8")

    for html in (accuracy, comparison):
        assert 'class="tablewrap"' in html
        assert 'scope="col"' in html
        assert "enhanceCharts(" in html
        assert "attachTopScroll" in html

    assert "scope: 'col'" in analytics
    assert "enhanceChart(s)" in analytics
    assert "attachTopScroll(tbl)" in analytics
    assert ".chartgrid{display:grid;grid-template-columns:minmax(0,1fr)" in analytics
    assert "repeat(auto-fit,minmax(320px,1fr))" not in analytics
    assert "function enhanceChart" in app_js
    assert "tools.setAttribute('role', 'group')" in app_js
    assert "function fit() {\n    scale = 100;" in app_js
    assert "Показать график целиком по ширине" in app_js
    assert "function openExpandedChart" in app_js
    assert "Открыть крупный график" in app_js
    assert ".wrap{max-width:1920px;width:100%}" in analytics
    assert "data-label': headers[i].t" in analytics
    assert "grid-template-columns:minmax(118px,42%) minmax(0,1fr)" in analytics
    assert "shelf_drilldown" in analytics
    assert "new AbortController()" in analytics
    assert "query.set('sku_key', skuKey)" in analytics
    assert "query.set('as_of', asOf)" in analytics
    assert "{t:'Позиция'},{t:'Точка'},{t:'Списано, шт',num:1,series:1}" in analytics
    assert "--chart-1:#2563EB" in css
    assert "--chart-2:#D97706" in css
    assert "--chart-3:#059669" in css
    assert "td.best" in css


def test_accuracy_explains_why_latest_date_can_lag_today() -> None:
    accuracy = (STATIC / "accuracy.html").read_text(encoding="utf-8")

    assert "Последняя завершённая сверка" in accuracy
    assert "после загрузки фактических продаж и ночной сверки" in accuracy


def test_manager_navigation_hides_network_analytics_until_admin_capability() -> None:
    pages = [
        (STATIC / name).read_text(encoding="utf-8")
        for name in ("index.html", "accuracy.html", "comparison.html",
                     "analytics.html", "reports.html", "chat.html")
    ]
    for html in pages:
        for href in ("/accuracy", "/comparison", "/analytics"):
            assert f'href="{href}" data-admin-only' in html

    app_js = (STATIC / "assets" / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "assets" / "app.css").read_text(encoding="utf-8")
    assert "applyAccessNavigation" in app_js
    assert "meta.access.analytics" in app_js
    assert '[data-admin-only]{display:none!important}' in css

    denied = (STATIC / "access-denied.html").read_text(encoding="utf-8")
    assert "доступны только с администраторского аккаунта" in denied
    assert "Менеджеру точки доступны заявка" in denied


def test_every_enhanced_table_can_open_full_screen_without_cloning_state() -> None:
    app_js = (STATIC / "assets" / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "assets" / "app.css").read_text(encoding="utf-8")

    assert "function openExpandedTable(wrap)" in app_js
    assert "viewport.append(wrap)" in app_js
    assert "placeholder.parentNode.insertBefore(wrap, placeholder)" in app_js
    assert "Открыть таблицу на весь экран" in app_js
    assert "openExpandedTable(wrap)" in app_js
    assert ".table-dialog" in css
    assert "max-height:calc(94vh - 100px)" in css


def test_redundant_reference_formula_is_removed_but_kvp_comparison_remains() -> None:
    order = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "Дополнительный справочный расчёт (не влияет на заявку)" not in order
    assert 'id="refbar"' not in order
    assert "/api/order/reference?date=" not in order
    assert "Справочно ' + refWeeks + ' нед" not in order
    assert 'id="krapBtn"' in order
    assert "показать формулу КВП" in order


def test_comparison_tables_show_primary_ml_before_formula_and_explain_gap() -> None:
    comparison = (STATIC / "comparison.html").read_text(encoding="utf-8")

    for table_id in ("tbl-daily", "tbl-points", "tbl-gap"):
        start = comparison.index(f'id="{table_id}"')
        end = comparison.index("</thead>", start)
        header = comparison[start:end]
        assert header.index("ML-ядро") < header.index("Формула КВП")
    assert "Расхождение ML–КВП, шт" in comparison
    assert "по убыванию абсолютной разницы" in comparison
    assert "fmt(r.gap_units)" in comparison


def test_no_order_rows_note_matches_current_assortment_filter() -> None:
    comparison = (STATIC / "comparison.html").read_text(encoding="utf-8")

    assert "Собственное производство и согласованные исключения" in comparison
    assert "отфильтрованы отдельно до расчёта" in comparison
    assert "Часть из них точка производит сама" not in comparison


# ---- 08.09: таблицы аналитики, баннер честности, подсказка про перепродажу ----

def test_analytics_tables_keep_text_columns_readable() -> None:
    analytics = (STATIC / "analytics.html").read_text(encoding="utf-8")

    assert ".tblwrap{max-height:min(72vh,760px);overflow-y:auto;overflow-x:auto;" in analytics
    assert "overflow-x:hidden;margin-top:14px" not in analytics
    assert ".tblwrap table{font-size:12.5px;min-width:max-content;table-layout:auto}" in analytics
    assert ".tblwrap th:not(.namecell){white-space:nowrap}" in analytics
    assert (".tblwrap td:not(.num){min-width:180px;overflow-wrap:normal;"
            "word-break:normal;white-space:normal}") in analytics
    assert ".tblwrap th,.tblwrap td{overflow-wrap:anywhere}" not in analytics
    # верхняя синхронная полоса прокрутки по-прежнему цепляется к обёртке
    assert "attachTopScroll(tbl)" in analytics
    assert "if (h.title) th.title = h.title" in analytics
    assert ("Отмеченные дни — дни, когда по чекам позиция закончилась заметно "
            "раньше закрытия точки") in analytics


def test_accuracy_and_comparison_carry_static_honesty_banner() -> None:
    accuracy = (STATIC / "accuracy.html").read_text(encoding="utf-8")
    comparison = (STATIC / "comparison.html").read_text(encoding="utf-8")
    css = (STATIC / "assets" / "app.css").read_text(encoding="utf-8")

    text = ("Оценка точности опирается на списания и чеки. Пока списания на точках "
            "проводятся не день в день, промах завышен — цифры будут уточняться по "
            "мере дисциплины данных.")
    for html in (accuracy, comparison):
        assert 'class="banner warn" id="honesty" style="display:block' in html
        assert text in html
        assert '<div class="banner err" id="err"></div>' in html
        assert html.index('id="honesty"') < html.index('id="err"')
    assert "резервный контур CatBoost" in comparison
    assert ".banner.warn{display:block" in css


def test_oversold_rows_are_explained_not_blamed_on_forecast() -> None:
    analytics = (STATIC / "analytics.html").read_text(encoding="utf-8")
    accuracy = (STATIC / "accuracy.html").read_text(encoding="utf-8")

    hint = ("Продано больше привезённого — остаток с прошлого дня, привоз без документа "
            "или собственное производство точки; это не ошибка прогноза")
    assert "const OVERSOLD_HINT = '" + hint + "'" in analytics
    assert analytics.count("text: OVERSOLD_HINT + '.'") == 2      # шаг 1 (точки) и шаг 2 (дни)
    assert "if (Number(row.sold || 0) > Number(row.ordered || 0))" in analytics
    assert "Почему продано больше, чем заказано" in analytics
    assert "Почему заказ 0, а продажа есть" in analytics
    assert hint in accuracy
    assert 'title="' + hint + '">Продано</th>' in accuracy
