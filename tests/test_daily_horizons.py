from datetime import date

import pytest

from prognoz.predict import stacked_guard_core
from prognoz.predict.daily import (ZERO_RULE_NOTE, production_guard_cfg,
                                   resolve_release_context, zero_demand_note)
from prognoz.predict.guard import model_sanity


@pytest.mark.parametrize("horizon,lag", [(2, 3), (3, 4), (4, 5)])
def test_release_context_maps_each_horizon_to_causal_lag(horizon, lag):
    issue = date(2026, 8, 25)
    target = issue.fromordinal(issue.toordinal() + horizon)
    got = resolve_release_context([
        f"--issue-date={issue}", f"--date={target}",
        f"--horizon={horizon}", f"--lag={lag}",
    ], today=date(2099, 1, 1))
    assert got == (issue, target, horizon, lag)


def test_release_context_rejects_misaligned_target_and_lag():
    with pytest.raises(ValueError, match="честный lag=5"):
        resolve_release_context(["--horizon=4", "--lag=3"],
                                today=date(2026, 8, 25))
    with pytest.raises(ValueError, match="не равна"):
        resolve_release_context([
            "--issue-date=2026-08-25", "--date=2026-08-28", "--horizon=4",
        ])


def test_model_sanity_uses_horizon_information_boundary():
    stamp = {"train_cutoff": "2026-08-21", "n_train_rows": 1000}
    preds = __import__("pandas").DataFrame({
        "mu_ml": [1.0], "r28_mean": [1.0], "wd_mean": [1.0],
        "lag_recent": [1.0],
    })
    problems = model_sanity(
        stamp, None, preds, date(2026, 8, 25), lag_days=5)
    assert any("заглядывание" in problem for problem in problems)


@pytest.mark.parametrize("lag", [3, 4, 5])
def test_production_guard_is_fail_closed_for_every_horizon(lag):
    cfg = production_guard_cfg(stacked_guard_core, lag)
    assert cfg.lag == lag
    assert cfg.allow_hgb_fallback is False


# ---- правило нулей (C8, 08.09): история есть, спроса за 28 дней нет --------------

def test_zero_rule_only_for_pairs_with_history_and_no_demand(monkeypatch):
    monkeypatch.delenv("PROGNOZ_ZERO_RULE", raising=False)
    assert zero_demand_note(5, sold28=0.0, lost28=0.0, n_obs_days=30) == ZERO_RULE_NOTE
    # потери полки неизвестны (добавка спроса выключена) — достаточно «продано 0»
    assert zero_demand_note(5, sold28=0.0, lost28=None, n_obs_days=30) == ZERO_RULE_NOTE
    # полка видела дефицит — спрос был, не обнуляем
    assert zero_demand_note(5, sold28=0.0, lost28=2.0, n_obs_days=30) is None
    assert zero_demand_note(5, sold28=1.0, lost28=0.0, n_obs_days=30) is None
    # новинка (мало наблюдений) или глубина истории неизвестна — не трогаем
    assert zero_demand_note(5, sold28=0.0, lost28=0.0, n_obs_days=13) is None
    assert zero_demand_note(5, sold28=0.0, lost28=0.0, n_obs_days=None) is None
    assert zero_demand_note(0, sold28=0.0, lost28=0.0, n_obs_days=30) is None


def test_zero_rule_has_environment_kill_switch(monkeypatch):
    monkeypatch.setenv("PROGNOZ_ZERO_RULE", "0")
    assert zero_demand_note(5, sold28=0.0, lost28=0.0, n_obs_days=30) is None
    monkeypatch.setenv("PROGNOZ_ZERO_RULE", "1")
    assert zero_demand_note(5, sold28=0.0, lost28=0.0, n_obs_days=30) == ZERO_RULE_NOTE
