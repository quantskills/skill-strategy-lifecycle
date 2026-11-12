from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from factor_health import Config, analyze, daily_ic, load_panel, warning_lead_days


def run(panel: str, output: str, returns_path: str | None = None) -> dict:
    ic = daily_ic(load_panel(panel, returns_path))
    rows = []
    for threshold in (.01, .02, .03):
        for q in (5e-7, 1e-6, 5e-6):
            for cusum in (4.0, 5.0, 7.0):
                cfg = Config(decline_threshold=threshold, process_var=q, cusum_threshold=cusum)
                detail, summary = analyze(ic, cfg)
                lead = warning_lead_days(detail, baseline_days=cfg.baseline_days)
                rows.append({"decline_threshold": threshold, "process_var": q,
                             "cusum_threshold": cusum, "red_rate": float((summary.alert_level == "红").mean()),
                             "median_probability": float(summary.decline_probability.median()),
                             "median_lead_days": float(lead.lead_trading_days.median()) if lead.lead_trading_days.notna().any() else None,
                             "pass_rate_5_days": float(lead.passes_5_day_target.mean())})
    frame = pd.DataFrame(rows)
    result = {
        "parameter_sets": len(frame),
        "red_rate_range": [float(frame.red_rate.min()), float(frame.red_rate.max())],
        "lead_target_pass_rate_range": [float(frame.pass_rate_5_days.min()), float(frame.pass_rate_5_days.max())],
        "stable_across_parameters": bool(frame.red_rate.max() - frame.red_rate.min() <= .25),
        "note": "Known-decay labels are required before claiming real-world early-warning accuracy.",
    }
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "sensitivity.csv", index=False)
    (out / "robustness.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="因子衰退预警敏感性分析")
    p.add_argument("--panel", required=True); p.add_argument("--returns")
    p.add_argument("--output", default="outputs/robustness")
    a = p.parse_args(); print(json.dumps(run(a.panel, a.output, a.returns), indent=2))
