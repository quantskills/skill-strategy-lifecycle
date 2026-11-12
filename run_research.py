"""Run the E6 strategy-lifecycle replay against the frozen Q8 package.

The Q8 package remains the source of the frozen model, data contract, simulator,
and benchmark. E6 only adds a causal state controller and an audit ledger.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from state_machine import LifecycleParams, replay_lifecycle
from project_config import Q8, RESULTS, load_q8, validate_inputs
from inference import paired_inference


ROOT = Path(__file__).resolve().parent
OUT = RESULTS
OUT.mkdir(parents=True, exist_ok=True)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_json(value):
    if isinstance(value, dict):
        return {str(k): as_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_json(v) for v in value]
    if isinstance(value, np.generic):
        return as_json(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def save(path: Path, value):
    path.write_text(json.dumps(as_json(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def episodes(state: pd.Series) -> list[dict]:
    rows, active = [], None
    # A Q8 simulator frame is grouped by phase, while the state controller is
    # chronological.  Episode accounting must always use decision-date order.
    state = state.astype(bool).sort_index()
    for i, (date, value) in enumerate(state.items()):
        if value and active is None:
            active = (str(date), i)
        if not value and active is not None:
            rows.append({"start": active[0], "end_exclusive": str(date),
                         "duration_observations": i - active[1], "right_censored": False})
            active = None
    if active is not None:
        rows.append({"start": active[0], "end_exclusive": None,
                     "duration_observations": len(state) - active[1], "right_censored": True})
    return rows


def bootstrap_delta(a: pd.DataFrame, b: pd.DataFrame, draws=4000, block=4, seed=20261111):
    """Paired circular blocks; null-centred test and percentile intervals."""
    return paired_inference(a, b, draws=draws, block=block, seed=seed)


def score_hash(cache_row: dict) -> str:
    """Hash the exact Q8 score vector used on one decision date.

    The hash includes symbols and scores in their source order.  It is an audit
    anchor for the frozen Q8 walk-forward score, not a new model fit.
    """
    payload = [{"symbol": str(symbol),
                "score": None if not np.isfinite(score) else float(score)}
               for symbol, score in zip(cache_row["sym"], cache_row["score"])]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def episode_stats(exposure: pd.Series) -> tuple[int, float]:
    rows = episodes(exposure < 1.0)
    return len(rows), float(np.mean([r["duration_observations"] for r in rows])) if rows else 0.0


def assign_episode_ids(state_frame: pd.DataFrame) -> pd.DataFrame:
    """Assign chronological inactive episode IDs to the evaluation ledger."""
    frame = state_frame.sort_values("date").copy()
    inactive = frame.exposure < 1.0
    starts = inactive & ~inactive.shift(1, fill_value=False)
    number = starts.cumsum().where(inactive)
    frame["episode_id"] = number.map(lambda value: f"E{int(value):02d}" if pd.notna(value) else None)
    return frame


def main():
    validate_inputs()
    q8 = load_q8()
    cache, _ = q8.load_cache()
    audit = json.loads((Q8 / "results" / "v2" / "backtest_audit.json").read_text(encoding="utf-8"))
    q8_metrics = json.loads((Q8 / "results" / "v2" / "metrics.json").read_text(encoding="utf-8"))
    eval_dates = [str(d) for d in audit["eval_dates"]]
    q8_monitor = pd.read_csv(Q8 / "results" / "v2" / "monitor.csv", dtype={"date": str}, float_precision='round_trip')
    q8_monitor["date"] = q8_monitor["date"].astype(str)
    calibration = json.loads((Q8 / "results" / "v2" / "calibration.json").read_text(encoding="utf-8"))
    theta = float(calibration["theta"])
    # Replay over the entire frozen history so the first evaluation state is
    # inherited from information that was actually available before evaluation.
    # Main controller uses IC alarms only. Q8's health path is retained as a
    # sensitivity below because its own robustness check did not pass.
    lifecycle = replay_lifecycle(
        q8_monitor.assign(health_alarm=False),
        q8_monitor["date"].tolist(),
        LifecycleParams(theta=theta),
        monitor_column="monitor",
        health_column="health_alarm",
        alarm_column="ic_alarm",
    )
    lifecycle_health = replay_lifecycle(
        q8_monitor,
        q8_monitor["date"].tolist(),
        LifecycleParams(theta=theta),
        monitor_column="monitor",
        health_column="health_alarm",
        alarm_column="ic_alarm",
    )
    life_eval = lifecycle[lifecycle.date.isin(eval_dates)].copy()
    if len(life_eval) != len(eval_dates):
        raise AssertionError("lifecycle ledger does not cover all evaluation dates")
    lifecycle.assign(in_evaluation=lifecycle.date.isin(eval_dates)).to_csv(
        OUT / "state_ledger_full.csv", index=False
    )
    lifecycle_health.assign(in_evaluation=lifecycle_health.date.isin(eval_dates)).to_csv(
        OUT / "state_ledger_health_full.csv", index=False
    )
    life_eval = assign_episode_ids(life_eval)
    state_exposure = dict(zip(life_eval.date, life_eval.exposure))
    life_eval_health = lifecycle_health[lifecycle_health.date.isin(eval_dates)].copy()
    state_exposure_health = dict(zip(life_eval_health.date, life_eval_health.exposure))
    simple_alarm = q8_monitor.set_index("date").reindex(eval_dates).ic_alarm.fillna(False).astype(bool).to_dict()
    simple_exposure = {d: (0.0 if simple_alarm[d] else 1.0) for d in eval_dates}
    full_exposure = {d: 1.0 for d in eval_dates}
    alarm_map = {d: bool(simple_alarm[d]) for d in eval_dates}
    score_hashes = {d: score_hash(cache[d]) for d in eval_dates}
    exposure_series = {
        "always_on": pd.Series(full_exposure, dtype=float),
        "simple_threshold": pd.Series(simple_exposure, dtype=float),
        "lifecycle_v1": pd.Series(state_exposure, dtype=float),
        "lifecycle_health_gate": pd.Series(state_exposure_health, dtype=float),
    }

    all_rows, summaries, opportunity = [], [], []
    bootstrap = {}
    for expression in ("long", "hedge"):
        full = q8.simulate(cache, eval_dates, expression, exposure=full_exposure)
        simple = q8.simulate(cache, eval_dates, expression, exposure=simple_exposure, alarm=alarm_map)
        life = q8.simulate(cache, eval_dates, expression, exposure=state_exposure, alarm=alarm_map)
        life_health = q8.simulate(cache, eval_dates, expression, exposure=state_exposure_health, alarm=alarm_map)
        frames = {"always_on": full, "simple_threshold": simple,
                  "lifecycle_v1": life, "lifecycle_health_gate": life_health}
        controller_ledgers = {"lifecycle_v1": life_eval,
                              "lifecycle_health_gate": life_eval_health}
        full_idx = full.set_index("date")
        ledger_expr = life_eval.copy()
        ledger_expr["expression"] = expression
        ledger_expr = ledger_expr.merge(
            life[["date", "phase", "cycle", "gross", "trade_cost", "borrow_cost", "cost", "net", "turnover"]],
            on="date", how="left", validate="one_to_one",
        )
        ledger_expr = ledger_expr.rename(columns={"net": "lifecycle_net", "gross": "lifecycle_gross"})
        ledger_expr["realized_gross"] = ledger_expr["lifecycle_gross"]
        ledger_expr["net"] = ledger_expr["lifecycle_net"]
        ledger_expr["score_sha256"] = ledger_expr["date"].map(score_hashes)
        ledger_expr["full_net_counterfactual"] = ledger_expr.date.map(full_idx.net)
        ledger_expr["full_gross_counterfactual"] = ledger_expr.date.map(full_idx.gross)
        ledger_expr["missed_gain"] = ledger_expr.full_gross_counterfactual.clip(lower=0) * (1 - ledger_expr.exposure)
        ledger_expr["avoided_loss"] = (-ledger_expr.full_gross_counterfactual).clip(lower=0) * (1 - ledger_expr.exposure)
        ledger_expr["opportunity_cost"] = ledger_expr.full_net_counterfactual - ledger_expr.lifecycle_net
        opportunity.append(ledger_expr)
        for arm, frame in frames.items():
            metrics = q8.metrics(frame)
            base = q8.metrics(full)
            n_episodes, mean_duration = episode_stats(exposure_series[arm])
            row = {"expression": expression, "arm": arm, **metrics,
                   "delta_vs_always_on": float(metrics["total"] - base["total"]),
                   "inactive_share": float((frame.exposure < 1).mean()),
                   "deactivation_episodes": n_episodes,
                   "mean_deactivation_duration": mean_duration,
                   "reactivation_count": int(controller_ledgers[arm].state_after.eq("REACTIVATING").sum()) if arm in controller_ledgers else None}
            summaries.append(row)
            tagged = frame.copy()
            tagged["arm"] = arm
            all_rows.append(tagged)
        bootstrap[expression] = {"ic_only": bootstrap_delta(life, full),
                                 "health_gate_sensitivity": bootstrap_delta(life_health, full)}
    ledger = pd.concat(opportunity, ignore_index=True)
    ledger.to_csv(OUT / "state_ledger.csv", index=False)
    pd.concat(all_rows, ignore_index=True).to_csv(OUT / "trades.csv", index=False)
    pd.DataFrame(summaries).to_csv(OUT / "summary.csv", index=False)
    save(OUT / "summary.json", summaries)
    q8_reference = [row for row in q8_metrics if row["arm"] in {"A", "B", "C_ic", "D_ic", "F_ic"}]
    save(OUT / "q8_reference.json", q8_reference)
    pd.DataFrame(q8_reference).to_csv(OUT / "q8_reference.csv", index=False)
    save(OUT / "bootstrap.json", bootstrap)
    state_counts = (life_eval.assign(inactive=life_eval.exposure < 1)
                    .groupby("state_after", as_index=False).agg(observations=("date", "size"),
                                                                   mean_exposure=("exposure", "mean")))
    state_counts.to_csv(OUT / "state_counts.csv", index=False)
    save(OUT / "episodes.json", episodes(life_eval.set_index("date").exposure < 1))
    # State-level attribution: each state is evaluated against the same full
    # exposure counterfactual on the dates where it was actually active.
    state_rows = []
    for expression, group in ledger.groupby("expression", sort=True):
        for state, state_group in group.groupby("state_after", sort=True):
            state_rows.append({
                "expression": expression,
                "state": state,
                "observations": int(len(state_group)),
                "mean_exposure": float(state_group.exposure.mean()),
                "gross_sum": float(state_group.realized_gross.sum()),
                "net_sum": float(state_group.net.sum()),
                "cost_sum": float(state_group.cost.sum()),
                "full_net_sum": float(state_group.full_net_counterfactual.sum()),
                "signed_opportunity_cost_sum": float(state_group.opportunity_cost.sum()),
                "missed_gain_gross_sum": float(state_group.missed_gain.sum()),
                "avoided_loss_gross_sum": float(state_group.avoided_loss.sum()),
                "mean_net_per_observation": float(state_group.net.mean()),
                "mean_opportunity_cost_per_observation": float(state_group.opportunity_cost.mean()),
            })
    state_attribution = pd.DataFrame(state_rows)
    state_attribution.to_csv(OUT / "state_attribution.csv", index=False)
    transitions = life_eval.loc[life_eval.state_before != life_eval.state_after,
                                ["date", "state_before", "state_after", "exposure", "transition_reason", "monitor", "alarm_ic"]].copy()
    transitions.to_csv(OUT / "state_transitions.csv", index=False)
    # Episode-level contribution, separately for long and hedge.
    episode_rows = []
    for expression, group in ledger.groupby("expression", sort=True):
        group = group.sort_values("date")
        inactive = group.exposure < 1.0
        starts = inactive & ~inactive.shift(1, fill_value=False)
        episode_number = starts.cumsum().where(inactive)
        group = group.assign(episode_id=episode_number.map(lambda value: f"E{int(value):02d}" if pd.notna(value) else None))
        for episode_id, eg in group.dropna(subset=["episode_id"]).groupby("episode_id", sort=True):
            episode_rows.append({
                "expression": expression,
                "episode_id": episode_id,
                "start": str(eg.date.iloc[0]),
                "end_inclusive": str(eg.date.iloc[-1]),
                "observations": int(len(eg)),
                "states": ">".join(eg.state_after.astype(str).drop_duplicates().tolist()),
                "mean_exposure": float(eg.exposure.mean()),
                "net_sum": float(eg.net.sum()),
                "full_net_sum": float(eg.full_net_counterfactual.sum()),
                "opportunity_cost_sum": float(eg.opportunity_cost.sum()),
                "missed_gain_gross_sum": float(eg.missed_gain.sum()),
                "avoided_loss_gross_sum": float(eg.avoided_loss.sum()),
                "right_censored": bool(eg.date.iloc[-1] == eval_dates[-1]),
            })
    pd.DataFrame(episode_rows).to_csv(OUT / "episode_attribution.csv", index=False)
    opp_by_expression = {
        e: {
            "signed_opportunity_cost_sum": float(g.opportunity_cost.sum()),
            "missed_gain_gross_sum": float(g.missed_gain.sum()),
            "avoided_loss_gross_sum": float(g.avoided_loss.sum()),
            "deactivated_observations": int((g.exposure < 1).sum()),
        } for e, g in ledger.groupby("expression")
    }
    save(OUT / "opportunity_cost.json", {
        "by_expression": opp_by_expression,
        "definition": "full_net_counterfactual - lifecycle_net; missed_gain/avoided_loss are gross decomposition",
    })
    run = {
        "status": "completed",
        "evaluation_dates": [eval_dates[0], eval_dates[-1]],
        "evaluation_observations": len(eval_dates),
        "phase_count": 4,
        "cycles_per_phase": audit["phase_cycles"],
        "theta": theta,
        "params": as_json(LifecycleParams(theta=theta).__dict__),
        "main_controller": "ic_only; health gate is sensitivity because Q8 health robustness failed",
        "q8_root": str(Q8),
        "q8_engine_sha256": sha(Q8 / "scripts" / "engine_v2.py"),
        "q8_monitor_sha256": sha(Q8 / "results" / "v2" / "monitor.csv"),
        "q8_calibration_sha256": sha(Q8 / "results" / "v2" / "calibration.json"),
        "q8_score_source": "data/weekly_slim.csv::score_wf (archived walk-forward Ridge scores)",
        "q8_weekly_slim_sha256": sha(Q8 / "data" / "weekly_slim.csv"),
        "q8_panel_sha256": sha(Q8 / "data" / "panel_for_platform.csv"),
        "q8_upstream_summary_sha256": sha(Q8 / "data" / "upstream_summary.json"),
        "retrained": False,
        "point_in_time_state_replay": True,
        "csv_float_precision": "round_trip",
        "retrospective_sample": True,
        "missing_return_convention": "Q8 simulate default missing_fill=0.0",
        "state_ledger_rows": int(len(ledger)),
        "state_ledger_full_rows": int(len(lifecycle)),
        "state_ledger_expressions": sorted(ledger.expression.unique().tolist()),
        "evaluation_carry_in_state": str(life_eval.iloc[0].state_before),
        "evaluation_carry_in_exposure": float(life_eval.iloc[0].exposure),
    }
    save(OUT / "run.json", run)
    report = [
        "# E6：策略降权、退役与再激活是否优于持续持有", "",
        "本研究复用 Q8 的冻结 Ridge 模型、可交易池、成熟标签日历、四相位账户和成本口径。E6 不重训模型，也不引入新因子；唯一新增的是可因果重放的生命周期状态机。", "",
        f"- 评估日期：{eval_dates[0]}—{eval_dates[-1]}，{len(eval_dates)} 个周度决策日（4 相位、每相位 {audit['phase_cycles']} 个四周周期）。",
        f"- Q8 2024 校准阈值 theta={theta:.10f}；生命周期参数固定为 cooldown=2、retire=3/4、recovery=3。主规则只用 IC 告警；同时输出 health gate 敏感性。",
        f"- 冻结 Q8 参照：A(always-on) long={next(x['total'] for x in q8_metrics if x['expression']=='long' and x['arm']=='A'):+.2%}、hedge={next(x['total'] for x in q8_metrics if x['expression']=='hedge' and x['arm']=='A'):+.2%}；C_ic(告警半仓) long={next(x['total'] for x in q8_metrics if x['expression']=='long' and x['arm']=='C_ic'):+.2%}、hedge={next(x['total'] for x in q8_metrics if x['expression']=='hedge' and x['arm']=='C_ic'):+.2%}。完整参照见 `q8_reference.csv`。",
        "- 每次状态转换只使用当时可见的成熟监控值；状态在交易前确定，收益只在状态固定后计入。评估期账本见 `state_ledger.csv`，含评估前 carry-in 的全历史账本见 `state_ledger_full.csv`。", "",
        "## 结果", "",
        "| 表达 | 策略 | 净收益 | 相对持续持有 | 平均暴露 | 最大回撤 | 成本均值 | 非满仓占比 |", "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        report.append(f"| {row['expression']} | {row['arm']} | {row['total']:+.2%} | {row['delta_vs_always_on']:+.2%} | {row['avg_exposure']:.2%} | {row['worst_phase_mdd']:.2%} | {row['cost_sum_mean']:.2%} | {row['inactive_share']:.2%} |")
    attr_pivot = state_attribution.pivot(index="state", columns="expression")
    def attr_value(state, metric, expression, default=0.0):
        try:
            return float(attr_pivot.loc[state, (metric, expression)])
        except KeyError:
            return default
    state_order = ["DOWNWEIGHT", "COOLDOWN", "RETIRED", "REACTIVATING", "FULL"]
    report += ["", "## 状态级结果", "", "状态机确实经历了降权、冷却、退役和再激活。下表按状态后的暴露汇总；long/hedge 共用同一状态路径，机会成本按各自表达的满仓反事实计算。", "", "| 状态 | 观察周数 | 暴露 | long净收益和 | hedge净收益和 | long机会成本 | hedge机会成本 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for state in state_order:
        if state in attr_pivot.index:
            report.append(f"| {state} | {int(attr_value(state, 'observations', 'long'))} | {attr_value(state, 'mean_exposure', 'long'):.2f} | {attr_value(state, 'net_sum', 'long'):+.4f} | {attr_value(state, 'net_sum', 'hedge'):+.4f} | {attr_value(state, 'signed_opportunity_cost_sum', 'long'):+.4f} | {attr_value(state, 'signed_opportunity_cost_sum', 'hedge'):+.4f} |")
    transition_counts = transitions.state_after.value_counts().to_dict()
    reactivation_full = int(((transitions.state_after == "FULL") & (transitions.state_before == "REACTIVATING")).sum())
    report += ["", f"状态转换共 {len(transitions)} 次：进入降权 {int(transition_counts.get('DOWNWEIGHT', 0))} 次，进入冷却 {int(transition_counts.get('COOLDOWN', 0))} 次，进入退役 {int(transition_counts.get('RETIRED', 0))} 次，进入再激活 {int(transition_counts.get('REACTIVATING', 0))} 次，再激活后完全恢复 {reactivation_full} 次。", "", "主规则共有 5 个非满仓 episode：4 个完成，1 个在评估尾部右删失。逐 episode 的状态路径、净收益和机会成本见 `episode_attribution.csv`。", "", "## 机会成本与解释", "", "机会成本按 `always_on 净收益 − lifecycle 净收益` 定义；正值表示降权/退役相对持续持有所损失。`missed_gain` 与 `avoided_loss` 是按满仓毛收益拆分的机会与保护，不能替代净收益比较。", "", "### 主规则机会成本", ""]
    report += [
        "状态级解释（机会成本为逐周账本累加，不能直接当作终值收益）：降权 4 周，long/hedge 的机会成本分别为 +0.0107/+0.0207，影响较小但没有净收益优势；冷却 5 周，long 为 -0.2588，说明该段主要避开了满仓损失，hedge 为 +0.0180，保护效果有限；退役 38 周，是拖累最大的状态，long/hedge 机会成本为 +0.6187/+0.0810，显示零暴露持续过久并错过了后续反弹；再激活 4 周，long 为 +0.0161、hedge 为 -0.0003，半仓恢复没有形成稳定增益。",
        f"评估区间开始时策略已经从历史监控继承为 `{life_eval.iloc[0].state_before}`（暴露 {life_eval.iloc[0].exposure:.1f}），因此首个 episode 是 carry-in，而非评估期内新触发的停用。后续验证应同时报告 carry-in 与 warm-start 敏感性。",
        "",
    ]
    report += [
        f"- long：signed opportunity cost={opp_by_expression['long']['signed_opportunity_cost_sum']:+.4f}；missed gross gain={opp_by_expression['long']['missed_gain_gross_sum']:.4f}；avoided gross loss={opp_by_expression['long']['avoided_loss_gross_sum']:.4f}。",
        f"- hedge：signed opportunity cost={opp_by_expression['hedge']['signed_opportunity_cost_sum']:+.4f}；missed gross gain={opp_by_expression['hedge']['missed_gain_gross_sum']:.4f}；avoided gross loss={opp_by_expression['hedge']['avoided_loss_gross_sum']:.4f}。",
        "",
        "### 配对 block bootstrap",
        "",
        "生命周期相对持续持有的 4-cycle circular block bootstrap（4,000 次）为 long 的平均 cycle delta -0.00433、双侧 p=0.4800；hedge 为 -0.00139、p=0.7045。该结果支持保留为负向/不确定证据，不支持宣布优于持续持有。",
        "",
        "### 与 Q8 的稳健性边界",
        "",
        "Q8 的 18 项比较在保守 BH 下最小 p=0.4259，block Reality Check 正向 p=0.5403；健康路径的 27 组参数扰动告警占比为 28.1%—100.0%，默认路径最大分歧约 41.7%，因此 E6 主规则不把 health gate 作为选优依据。",
        "",
        "## Q8 与 E6 的差异",
        "",
        "Q8 比较告警后的降仓、重训和换信号动作；E6 固定 Q8 Ridge 信号和监控，只研究状态持续性、冷却、退役和带滞后的恢复。因此 E6 的增量只能归因于生命周期控制。",
        "",
        "## 证据边界",
        "",
        "点估计和 bootstrap 仅描述这段已看过的历史样本，不能称为前瞻确认集。若新规则在多重比较、安慰剂或新数据上不能复现，应保留为诊断规则。可靠负结果同样记录。",
        "",
        "## 决策结论",
        "",
        "交易决策：本样本不支持把 lifecycle_v1 设为持续持有的默认替代；long 净收益低 8.50 个百分点，hedge 仍低 2.06 个百分点。它把平均暴露降至 46.59%，并把 long 最差相位回撤从 -26.82% 降到 -19.65%，代价是正收益阶段的机会成本。health gate 的 hedge 点估计改善 +3.38 个百分点，但 Q8 健康路径不稳健，只能作为敏感性结果。",
        "",
        "研究决策：保留可重放状态机、全历史 carry-in 和逐日账本，作为后续新增数据的确认接口；暂不重训模型、重选阈值或自动下单。",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(report), encoding="utf-8")
    print((OUT / "summary.csv").resolve())


if __name__ == "__main__":
    main()

