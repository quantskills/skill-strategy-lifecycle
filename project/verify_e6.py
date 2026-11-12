"""Independent checks for the E6 lifecycle replay and generated artifacts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from state_machine import LifecycleParams, replay_lifecycle
from project_config import Q8, RESULTS, load_q8, validate_inputs


ROOT = Path(__file__).resolve().parent
OUT = RESULTS


def main() -> None:
    input_check = validate_inputs()
    summary = pd.read_csv(OUT / "summary.csv")
    ledger = pd.read_csv(OUT / "state_ledger.csv", dtype={"date": str, "monitor_cutoff": str})
    run = json.loads((OUT / "run.json").read_text(encoding="utf-8"))
    audit = json.loads((Q8 / "results" / "v2" / "backtest_audit.json").read_text(encoding="utf-8"))
    eval_dates = [str(x) for x in audit["eval_dates"]]
    state_attr = pd.read_csv(OUT / "state_attribution.csv")
    transitions = pd.read_csv(OUT / "state_transitions.csv", dtype={"date": str})
    episodes = pd.read_csv(OUT / "episode_attribution.csv", dtype={"start": str, "end_inclusive": str})

    required = {
        "date", "monitor_cutoff", "state_before", "state_after", "exposure",
        "alarm_ic", "alarm_health", "severe", "alarm_streak", "good_streak",
        "cooldown_age", "transition_reason", "visible_monitor_sha256",
        "score_sha256", "decision_index", "realized_gross", "trade_cost",
        "borrow_cost", "lifecycle_net", "full_net_counterfactual",
        "net", "missed_gain", "avoided_loss", "opportunity_cost", "expression",
    }
    missing = sorted(required - set(ledger.columns))
    assert not missing, f"ledger missing columns: {missing}"
    assert len(ledger) == 2 * len(eval_dates)
    for expression, group in ledger.groupby("expression"):
        assert group.date.tolist() == eval_dates, f"ledger order broken for {expression}"
        assert group.date.nunique() == len(eval_dates)
        assert group.score_sha256.notna().all()
        assert group.monitor_cutoff.notna().all()
        assert (group.monitor_cutoff < group.date).all()
        assert np.allclose(group.opportunity_cost,
                           group.full_net_counterfactual - group.lifecycle_net,
                           atol=1e-12)
        assert np.allclose(group.missed_gain,
                           group.full_gross_counterfactual.clip(lower=0) * (1 - group.exposure),
                           atol=1e-12)
        assert np.allclose(group.avoided_loss,
                           (-group.full_gross_counterfactual).clip(lower=0) * (1 - group.exposure),
                           atol=1e-12)

    allowed = {"FULL", "DOWNWEIGHT", "COOLDOWN", "RETIRED", "REACTIVATING"}
    assert set(ledger.state_before) <= allowed
    assert set(ledger.state_after) <= allowed
    assert set(ledger.exposure.round(6)) <= {0.0, 0.5, 1.0}
    assert run["retrained"] is False
    assert run["point_in_time_state_replay"] is True
    assert run["q8_score_source"].endswith("score_wf (archived walk-forward Ridge scores)")
    assert {"DOWNWEIGHT", "COOLDOWN", "RETIRED", "REACTIVATING"} <= set(state_attr.state)
    assert len(transitions) >= 4
    assert len(episodes) >= 1
    assert (state_attr.observations > 0).all()

    # The always-on values must reproduce the frozen Q8 A arm exactly.
    q8_metrics = json.loads((Q8 / "results" / "v2" / "metrics.json").read_text(encoding="utf-8"))
    for expression in ("long", "hedge"):
        got = float(summary.query("expression == @expression and arm == 'always_on'").iloc[0].total)
        ref = float(next(x["total"] for x in q8_metrics
                         if x["expression"] == expression and x["arm"] == "A"))
        assert abs(got - ref) < 1e-12, (expression, got, ref)

    # Future monitor edits must not change earlier states or exposures.
    monitor = pd.read_csv(Q8 / "results" / "v2" / "monitor.csv", dtype={"date": str}, float_precision='round_trip')
    params = LifecycleParams(theta=float(json.loads(
        (Q8 / "results" / "v2" / "calibration.json").read_text(encoding="utf-8"))["theta"]))
    dates = monitor.date.tolist()
    cut = len(dates) // 2
    baseline = replay_lifecycle(monitor.assign(health_alarm=False), dates, params)
    altered = monitor.copy()
    altered.loc[cut:, "monitor"] = 999.0
    altered.loc[cut:, "ic_alarm"] = False
    altered_run = replay_lifecycle(altered.assign(health_alarm=False), dates, params)
    cols = ["state_before", "state_after", "exposure", "alarm_streak", "good_streak"]
    assert baseline.loc[:cut - 1, cols].reset_index(drop=True).equals(
        altered_run.loc[:cut - 1, cols].reset_index(drop=True))

    checks = {
        "ok": True,
        "ledger_rows": int(len(ledger)),
        "evaluation_dates": len(eval_dates),
        "expressions": sorted(ledger.expression.unique().tolist()),
        "baseline_matches_q8": True,
        "future_invariance": True,
        "frozen_input_files_verified": input_check['files'],
    }
    (OUT / "verification.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
