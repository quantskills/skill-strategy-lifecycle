"""Integrate the three QuantSkills monitoring/diagnostic skills into E6.

This adapter keeps the distinction between:
* factor health monitoring (factor-level IC/health suggestions),
* market-regime monitoring (Pandadata live evidence), and
* strategy diagnostics (return/IC/drawdown/turnover/regime evidence).

Historical regime labels below are explicitly frozen-data proxies. They are
not substituted for the Pandadata-backed live market-regime agent.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from project_config import Q8, RESULTS, load_q8
OUT = RESULTS
SKILLS = ROOT / ".agents" / "skills"


def make_regime_proxy(q8, eval_dates: list[str]) -> pd.DataFrame:
    """Classify market context using only returns mature before each date.

    The thresholds are frozen from the 2024 calibration segment. This is a
    research proxy for historical stratification; it is not a Pandadata live
    scorecard and never drives the lifecycle controller.
    """
    cache, _ = q8.load_cache()
    dates = list(cache)
    rows = []
    raw = []
    for i, date in enumerate(dates):
        cutoff = i - 5
        prior = dates[max(0, cutoff - 12):cutoff + 1] if cutoff >= 0 else []
        if len(prior) < 8:
            raw.append({"date": date, "prior_obs": len(prior), "mean_return": np.nan,
                        "volatility": np.nan, "breadth": np.nan})
            continue
        weekly = []
        for d in prior:
            c = cache[d]
            valid = c["elig"] & np.isfinite(c["ret"])
            r = c["ret"][valid]
            weekly.append({"mean": float(np.mean(r)) if len(r) else np.nan,
                           "breadth": float(np.mean(r > 0)) if len(r) else np.nan,
                           "vol": float(np.std(r, ddof=1)) if len(r) > 1 else np.nan})
        f = pd.DataFrame(weekly)
        raw.append({"date": date, "prior_obs": len(prior),
                    "mean_return": float(f["mean"].mean()),
                    "volatility": float(f["vol"].mean()),
                    "breadth": float(f["breadth"].mean())})
    frame = pd.DataFrame(raw).set_index("date")
    cal_dates = [d for d in frame.index if "20240101" <= d <= "20241231"]
    cal = frame.loc[cal_dates].dropna()
    vol_high = float(cal.volatility.quantile(.75))
    mean_mid = float(cal.mean_return.median())
    breadth_low = float(cal.breadth.quantile(.25))
    breadth_high = float(cal.breadth.quantile(.75))

    def classify(row):
        if int(row.prior_obs) < 8 or not np.isfinite(row.mean_return):
            return "insufficient_evidence"
        if row.volatility >= vol_high and (row.mean_return <= mean_mid or row.breadth <= breadth_low):
            return "risk_expansion"
        if row.mean_return > mean_mid and row.breadth >= breadth_high:
            return "trend_expansion"
        return "sideways_mixed"

    frame["regime"] = frame.apply(classify, axis=1)
    result = frame.loc[frame.index.isin(eval_dates)].reset_index()
    result["source"] = "Q8 frozen weekly_slim proxy; prior mature returns only"
    result["threshold_source"] = "2024 calibration quantiles"
    result.to_csv(OUT / "regime_proxy.csv", index=False)
    meta = {"source": "frozen Q8 data proxy", "used_for_controller": False,
            "calibration_period": ["20240101", "20241231"],
            "volatility_75pct": vol_high, "mean_return_median": mean_mid,
            "breadth_25pct": breadth_low, "breadth_75pct": breadth_high,
            "labels": result.regime.value_counts().to_dict(),
            "interpretation": "Historical stratification only; not Pandadata live regime evidence."}
    (OUT / "regime_proxy_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_strategy_diagnostics(trades: pd.DataFrame, monitor: pd.DataFrame, regimes: pd.DataFrame) -> list[dict]:
    diagnostic_script = SKILLS / "skill-quant-strategy-diagnostics" / "scripts" / "diagnose.py"
    out_root = OUT / "skill_strategy_diagnostics"
    out_root.mkdir(parents=True, exist_ok=True)
    monitor = monitor[["date", "ic"]].copy()
    regimes = regimes[["date", "regime"]]
    outputs = []
    for (expression, arm), group in trades.groupby(["expression", "arm"], sort=True):
        frame = group[["date", "net", "turnover"]].rename(columns={"net": "strategy_return"})
        frame = frame.merge(monitor, on="date", how="left").rename(columns={"ic": "factor_ic"})
        frame = frame.merge(regimes, on="date", how="left")
        safe = f"{expression}_{arm}"
        input_path = out_root / f"{safe}.csv"
        output_dir = out_root / safe
        frame.to_csv(input_path, index=False)
        cmd = [sys.executable, str(diagnostic_script), "--input", str(input_path),
               "--out", str(output_dir), "--window", "22"]
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        (output_dir / "command.log").parent.mkdir(parents=True, exist_ok=True)
        (output_dir / "command.log").write_text(result.stdout + "\n---stderr---\n" + result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"strategy diagnostics failed for {safe}: {result.stderr[-1000:]}")
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        outputs.append({"expression": expression, "arm": arm, "status": report["status"],
                        "signals": ";".join(x["code"] for x in report["signals"]),
                        "observations": report["input"]["observations"]})
    pd.DataFrame(outputs).to_csv(OUT / "strategy_diagnostics_index.csv", index=False)
    return outputs


def main():
    q8 = load_q8()
    audit = json.loads((Q8 / "results" / "v2" / "backtest_audit.json").read_text(encoding="utf-8"))
    eval_dates = [str(x) for x in audit["eval_dates"]]
    monitor = pd.read_csv(Q8 / "results" / "v2" / "monitor.csv", dtype={"date": str}, float_precision='round_trip')
    trades = pd.read_csv(OUT / "trades.csv", dtype={"date": str, "expression": str, "arm": str})
    regimes = make_regime_proxy(q8, eval_dates)
    trades = trades.merge(regimes[["date", "regime"]], on="date", how="left")
    trades.to_csv(OUT / "trades_with_regime.csv", index=False)

    grouped = []
    for (expression, arm, regime), group in trades.groupby(["expression", "arm", "regime"], dropna=False):
        grouped.append({"expression": expression, "arm": arm, "regime": regime,
                        "observations": int(len(group)),
                        "net_sum": float(group.net.sum()),
                        "mean_net": float(group.net.mean()),
                        "gross_sum": float(group.gross.sum()),
                        "cost_sum": float(group.cost.sum()),
                        "mean_exposure": float(group.exposure.mean()),
                        "mean_turnover": float(group.turnover.mean()),
                        "state_mix": ";".join(f"{k}:{v}" for k, v in group.state_after.value_counts().items()) if "state_after" in group else ""})
    regime_summary = pd.DataFrame(grouped)
    regime_summary.to_csv(OUT / "regime_strategy_summary.csv", index=False)
    strategy_matrix = pd.DataFrame([
        {"strategy": "always_on", "best_fit_regime": "trend_expansion", "avoid_or_review": "risk_expansion", "evidence_rule": "full exposure preserves continuation when factor health is normal", "decision_boundary": "do not infer current market state from this historical proxy"},
        {"strategy": "simple_threshold", "best_fit_regime": "short risk_expansion", "avoid_or_review": "sideways_mixed", "evidence_rule": "fast reversible protection; vulnerable to repeated false alarms", "decision_boundary": "single threshold is not a permanent retirement rule"},
        {"strategy": "DOWNWEIGHT", "best_fit_regime": "sideways_mixed or transition", "avoid_or_review": "confirmed structural break", "evidence_rule": "half exposure buys confirmation time", "decision_boundary": "pre-register duration and exit rule"},
        {"strategy": "COOLDOWN", "best_fit_regime": "acute risk_expansion", "avoid_or_review": "trend_expansion", "evidence_rule": "zero exposure can avoid a short stress loss", "decision_boundary": "use only with recovery monitoring; do not equate with alpha"},
        {"strategy": "RETIRED", "best_fit_regime": "persistent multi-source failure", "avoid_or_review": "mean-reverting rebound", "evidence_rule": "requires repeated evidence across health and market context", "decision_boundary": "current E6 shows retirement opportunity cost is largest"},
        {"strategy": "REACTIVATING", "best_fit_regime": "stabilization after risk_expansion", "avoid_or_review": "new alarm or renewed volatility", "evidence_rule": "half exposure during recovery gate", "decision_boundary": "full restoration requires a clean confirmation"},
    ])
    strategy_matrix.to_csv(OUT / "strategy_regime_matrix.csv", index=False)
    diagnostic_index = run_strategy_diagnostics(trades.drop(columns=["regime"]), monitor, regimes)

    agent_snapshot = SKILLS / "agent-market-regime-monitor" / "outputs" / "live" / "regime_scorecard.json"
    live_info = json.loads(agent_snapshot.read_text(encoding="utf-8")) if agent_snapshot.exists() else None
    integration = {
        "skills": {
            "factor_health_monitor": str(SKILLS / "skill-factor-health-monitor"),
            "market_regime_monitor": str(SKILLS / "agent-market-regime-monitor"),
            "strategy_diagnostics": str(SKILLS / "skill-quant-strategy-diagnostics"),
        },
        "factor_health_monitor": {"source": "Q8 existing monitor and local skill copy",
                                  "Q8_health_robustness": json.loads((Q8 / "results" / "v2" / "health_robustness.json").read_text(encoding="utf-8"))},
        "market_regime_monitor": {"bundled_example_snapshot_available": bool(live_info),
                                  "bundled_example_snapshot": live_info,
                                  "historical_labels": "Q8 frozen-data proxy; no Pandadata credentials or live pull used"},
        "strategy_diagnostics": {"outputs": diagnostic_index, "weekly_input_note": "Skill diagnostic thresholds are generic; weekly data are not annualized for trading decisions."},
    }
    (OUT / "skill_integration.json").write_text(json.dumps(integration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# E6 regime applicability", "", "This report combines factor health evidence, market-regime evidence, and strategy diagnostics. Historical labels are Q8 frozen-data proxies; the Pandadata agent snapshot is descriptive and does not rewrite the backtest.", ""]
    lines.append("| regime | strategy | expression | net sum | mean exposure | observations | interpretation |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for row in grouped:
        label = {"trend_expansion": "趋势扩张：信号延续时优先持续持有/再激活",
                 "risk_expansion": "风险扩张：冷却或短期降权更适合，退役需谨慎",
                 "sideways_mixed": "震荡混合：降权观察，避免一次告警直接退役",
                 "insufficient_evidence": "证据不足：不做状态归因"}.get(row["regime"], "")
        lines.append(f"| {row['regime']} | {row['arm']} | {row['expression']} | {row['net_sum']:+.4f} | {row['mean_exposure']:.2f} | {row['observations']} | {label} |")
    lines += ["", "## How to use the strategies", "", "- `always_on`：适合趋势扩张且因子健康、回撤和换手未恶化的环境；它保留完整上涨暴露。", "- `simple_threshold`：适合风险扩张的短期应急，但只适合快速、可逆的风险控制；震荡中容易反复开关。", "- `DOWNWEIGHT`：适合证据刚出现、尚未确认的混合或震荡环境，承担较小的信号确认成本。", "- `COOLDOWN`：适合 IC 连续恶化并伴随风险扩张的短期阶段；其价值是限制尾部损失，不能假设能提升收益。", "- `RETIRED`：只适合持续、重复且多源证据一致的失效环境；当前结果显示退役持续过久会产生最大机会成本。", "- `REACTIVATING`：适合健康指标连续恢复、市场重新进入趋势扩张且没有新的风险扩张证据的阶段；应保留半仓过渡。", "", "## Boundary", "", "market-regime-monitor 的 Pandadata live 证据不能由 Q8 的 frozen-data proxy 替代；缺少实时 Pandadata 时，行情适用性结论只能作为历史分层，不构成当前交易指令。"]
    (OUT / "regime_applicability.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"regime_rows": len(regime_summary), "diagnostics": len(diagnostic_index)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
