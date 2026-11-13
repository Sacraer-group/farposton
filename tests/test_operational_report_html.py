from prognoz.report.operational import render_html


def test_report_details_are_grouped_and_escaped():
    point = {
        "name": "Точка <script>", "complete": False, "owner": "<img>",
        "metrics": {
            "forecast": 12, "web_order": 12, "order": 10, "sales": 9,
            "returns": 0, "writeoff": 1, "writeoff_rub": 100,
            "edit_count": 0, "edit_up": 0, "edit_down": 0,
            "confirmed_unchanged": False, "comparable_rows": 1, "rows": 1,
            "coverage_pct": 100, "sales_wape_pct": 25,
        },
        "sources": {name: {"status": "complete"} for name in
                    ("receipts", "sales", "orders", "writeoffs")},
        "documents": [], "early_stop": [],
    }
    report = {"trade_date": "2026-09-29", "generated_at": "2026-09-29T12:00:00+10:00",
              "points": [point]}

    result = render_html(report)

    assert result.count('class="detail-group"') == 3
    assert '<h3>Источники и документы</h3>' in result
    assert '<strong>Прогноз:</strong> 12 шт.' in result
    assert '<pre>' not in result
    assert '<script>' not in result and '<img>' not in result
    assert 'Точка &lt;script&gt;' in result
