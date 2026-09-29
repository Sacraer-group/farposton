"""The weekly rehearsal is causal and its first delivery obeys stock/pack limits."""
import pandas as pd

from web.weekly_demo import _features, scenario


def test_weekly_demo():
    dates = pd.date_range("2026-01-01", periods=29)
    history = pd.DataFrame({
        "date": dates, "point_key": "P", "sku_key": "S",
        "qty_demand": list(range(28)) + [10_000],
    })
    before = _features(history, dates[-1], [("P", "S")], {"P": 0}, {"S": 0})
    history.loc[28, "qty_demand"] = 1
    after = _features(history, dates[-1], [("P", "S")], {"P": 0}, {"S": 0})
    pd.testing.assert_frame_equal(before, after)

    result = scenario()
    assert result["chosen_method"] == min(result["wape"], key=result["wape"].get)
    assert len(result["rows"]) == 18
    for row in result["rows"]:
        assert row["covered_days"] <= row["shelf_life_days"]
        assert row["covered_days"] <= 7 - row["lead_time_days"]
        assert row["draft_qty"] % row["pack_size"] == 0
        need = max(0, row["first_delivery_demand"] + row["safety_stock"]
                   - row["available_delivery"])
        assert row["draft_qty"] >= need
        if need:
            assert row["draft_qty"] >= row["min_order"]
