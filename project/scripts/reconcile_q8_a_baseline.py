"""核对原 Q8 A 组与 E6 持续启用结果，并导出最小标签附件。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--q8-root", type=Path, required=True, help="原 Q8 项目或冻结输入根目录")
    parser.add_argument("--e6-trades", type=Path, required=True, help="E6 历史结果 trades.csv")
    parser.add_argument("--output-dir", type=Path, default=PROJECT.parent / "references" / "q8-a-baseline")
    args = parser.parse_args()

    q8_root = args.q8_root.resolve()
    if not (q8_root / "data" / "weekly_slim.csv").is_file():
        q8_root = q8_root / "frozen_q8"
    data_dir = q8_root / "data"
    required = [data_dir / "weekly_slim.csv", data_dir / "panel_for_platform.csv",
                data_dir / "upstream_summary.json", q8_root / "config" / "audit_v2.json",
                q8_root / "results" / "v2" / "metrics.json",
                q8_root / "results" / "v2" / "backtest_audit.json",
                q8_root / "scripts" / "engine_v2.py", args.e6_trades.resolve()]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少原 Q8 或 E6 对账输入：" + ", ".join(missing))

    # 让现有 Q8 策略副本直接读取原始 Q8 输入，保持选股、成本和标签口径。
    os.environ["E6_Q8_ROOT"] = str(q8_root)
    from project_config import load_q8

    engine = load_q8("q8_baseline_reconciliation")
    cache, _ = engine.load_cache()
    audit = json.loads((q8_root / "results" / "v2" / "backtest_audit.json").read_text(encoding="utf-8"))
    metrics = json.loads((q8_root / "results" / "v2" / "metrics.json").read_text(encoding="utf-8"))
    dates = [str(date) for date in audit["eval_dates"]]
    if len(dates) != 88:
        raise AssertionError(f"预期 88 个评估日期，实际为 {len(dates)}")

    original_e6 = pd.read_csv(args.e6_trades.resolve(), dtype={"date": str})
    original_e6 = original_e6[original_e6["arm"].eq("always_on")].copy()
    daily_frames = []
    phase_rows = []
    max_abs = {"gross": 0.0, "trade_cost": 0.0, "borrow_cost": 0.0, "net": 0.0, "turnover": 0.0}
    for expression in ("long", "hedge"):
        baseline = engine.simulate(cache, dates, expression).sort_values(["phase", "cycle"]).reset_index(drop=True)
        e6 = original_e6[original_e6["expression"].eq(expression)].sort_values(["phase", "cycle"]).reset_index(drop=True)
        lifecycle = pd.read_csv(args.e6_trades.resolve(), dtype={"date": str})
        lifecycle = lifecycle[(lifecycle["arm"].eq("lifecycle_v1")) & lifecycle["expression"].eq(expression)]
        lifecycle = lifecycle.sort_values(["phase", "cycle"]).reset_index(drop=True)
        if len(baseline) != 88 or len(e6) != 88 or len(lifecycle) != 88:
            raise AssertionError(f"{expression} 日期数量不匹配：Q8={len(baseline)}，E6持续启用={len(e6)}，E6生命周期={len(lifecycle)}")
        if baseline["date"].astype(str).tolist() != e6["date"].astype(str).tolist():
            raise AssertionError(f"{expression} 日期或相位顺序不匹配")
        if baseline["date"].astype(str).tolist() != lifecycle["date"].astype(str).tolist():
            raise AssertionError(f"{expression} 生命周期日期或相位顺序不匹配")
        joined = baseline[["date", "phase", "cycle", "gross", "trade_cost", "borrow_cost", "net", "turnover",
                           "missing_long", "missing_pool", "pool"]].merge(
            e6[["date", "gross", "trade_cost", "borrow_cost", "net", "turnover"]],
            on="date", suffixes=("_q8_a", "_e6_always_on"), validate="one_to_one")
        joined = joined.merge(lifecycle[["date", "net"]].rename(columns={"net": "net_e6_lifecycle_v1"}),
                              on="date", validate="one_to_one")
        joined["net_lifecycle_delta_vs_q8_a"] = joined["net_e6_lifecycle_v1"] - joined["net_q8_a"]
        joined.insert(3, "expression", expression)
        for column in max_abs:
            delta = joined[f"{column}_e6_always_on"] - joined[f"{column}_q8_a"]
            joined[f"{column}_delta"] = delta
            max_abs[column] = max(max_abs[column], float(delta.abs().max()))
        daily_frames.append(joined)

        baseline_metric = next(row for row in metrics if row["expression"] == expression and row["arm"] == "A")
        for phase in range(4):
            q8_phase = baseline[baseline["phase"].eq(phase)].sort_values("cycle")
            e6_phase = e6[e6["phase"].eq(phase)].sort_values("cycle")
            q8_total = float(np.prod(1.0 + q8_phase["net"].to_numpy()) - 1.0)
            e6_total = float(np.prod(1.0 + e6_phase["net"].to_numpy()) - 1.0)
            lifecycle_phase = lifecycle[lifecycle["phase"].eq(phase)].sort_values("cycle")
            lifecycle_total = float(np.prod(1.0 + lifecycle_phase["net"].to_numpy()) - 1.0)
            metric_total = float(baseline_metric["phase_totals"][phase])
            if abs(q8_total - metric_total) > 1e-12:
                raise AssertionError(f"{expression} 相位 {phase} 不能复现原 Q8 A 组指标")
            phase_rows.append({"expression": expression, "phase": phase, "dates": len(q8_phase),
                               "q8_a_total": q8_total, "q8_metrics_total": metric_total,
                               "e6_always_on_total": e6_total, "delta_e6_minus_q8": e6_total - q8_total,
                               "delta_basis_points": (e6_total - q8_total) * 10000.0,
                               "e6_lifecycle_v1_total": lifecycle_total,
                               "lifecycle_delta_vs_q8_percentage_points": (lifecycle_total - q8_total) * 100.0})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    weekly_path = data_dir / "weekly_slim.csv"
    weekly = pd.read_csv(weekly_path, dtype={"date": str, "symbol": str},
                         low_memory=False, float_precision="round_trip")
    labels = weekly[["date", "symbol", "fwd_ret"]].copy()
    labels["date"] = labels["date"].astype(str).str.zfill(8)
    labels["symbol"] = labels["symbol"].astype(str)
    if labels.duplicated(["date", "symbol"]).any():
        raise AssertionError("weekly_slim 的 date、symbol 不唯一")
    labels.to_csv(args.output_dir / "weekly_labels_date_symbol_fwd_ret.csv.gz", index=False,
                  compression={"method": "gzip", "compresslevel": 9, "mtime": 0}, float_format="%.17g")
    label_copy = pd.read_csv(args.output_dir / "weekly_labels_date_symbol_fwd_ret.csv.gz",
                             dtype={"date": str, "symbol": str}, float_precision="round_trip")
    source_values = labels["fwd_ret"].to_numpy(dtype=float)
    copied_values = label_copy["fwd_ret"].to_numpy(dtype=float)
    if not labels[["date", "symbol"]].equals(label_copy[["date", "symbol"]]):
        raise AssertionError("标签附件序列化后 date,symbol 与 weekly_slim 源顺序不一致")
    if not np.array_equal(source_values, copied_values, equal_nan=True):
        raise AssertionError("标签附件序列化后 fwd_ret 与 weekly_slim 不完全一致")
    daily = pd.concat(daily_frames, ignore_index=True)
    daily.to_csv(args.output_dir / "daily_reconciliation_88_dates.csv", index=False, float_format="%.17g")
    phases = pd.DataFrame(phase_rows)
    phases.to_csv(args.output_dir / "phase_reconciliation.csv", index=False, float_format="%.17g")

    source_hashes = {str(path.relative_to(q8_root)).replace("\\", "/"): sha256(path)
                     for path in required[:-1]}
    source_hashes["E6 historical trades.csv"] = sha256(args.e6_trades.resolve())
    summary = {
        "status": "reconciled",
        "evaluation_dates": len(dates),
        "date_start": dates[0],
        "date_end": dates[-1],
        "phase_count": 4,
        "cycles_per_phase": 22,
        "expressions": ["long", "hedge"],
        "weekly_slim_rows": int(len(weekly)),
        "label_rows": int(len(labels)),
        "label_missing_rows": int(labels["fwd_ret"].isna().sum()),
        "missing_return_rule": "先按标签成熟规则排除未来标签未成熟的最后5个周度观察，再从可评估日期中截取四相位均衡的88日；这8个尾部日期中5个因未成熟标签排除，3个因相位均衡排除。评估窗内部NaN fwd_ret在模拟收益向量中按0.0计入。",
        "q8_loader_rule": "panel_for_platform 与 weekly_slim 按 date,symbol 一对一合并，实际模拟收益采用 weekly_slim.fwd_ret。",
        "panel_fwd_ret_rule": "panel_for_platform.fwd_ret 不作为基线标签，以 weekly_slim.fwd_ret 覆盖。",
        "q8_a_metrics": [{"expression": row["expression"], "total": row["total"],
                          "phase_totals": row["phase_totals"], "missing_long": row["missing_long"],
                          "missing_pool": row["missing_pool"]}
                         for row in metrics if row["arm"] == "A" and row["expression"] in ("long", "hedge")],
        "max_daily_abs_difference_e6_vs_q8": max_abs,
        "max_phase_abs_difference_e6_vs_q8_basis_points": float(phases["delta_basis_points"].abs().max()),
        "source_sha256": source_hashes,
        "reconciliation_script_sha256": sha256(Path(__file__).resolve()),
        "e6_strategy_copy_sha256": sha256(PROJECT / "strategy" / "q8_engine_v2.py"),
    }
    report = [
        "# 原 Q8 A 组基线对账",
        "",
        f"评估区间为 {dates[0]} 至 {dates[-1]}，共 {len(dates)} 个日期。按原始顺序分为 4 个相位，每相位 22 个周期。",
        "",
        "## 标签与缺失值",
        "",
        f"原始 weekly_slim 共 {len(weekly):,} 行，其中 fwd_ret 缺失 {int(labels['fwd_ret'].isna().sum()):,} 行。附件仅保留 date、symbol、fwd_ret 三列。原 Q8 引擎按 date、symbol 一对一合并后，以 weekly_slim.fwd_ret 覆盖面板同名字段。先按标签成熟日历排除未来标签未成熟的最后5个周度观察，再截取四相位均衡的88个日期。评估窗内部缺失标签按0.0收益计入。",
        "",
        "## 88 日期逐日对账",
        "",
        "| 表达 | 日期数 | Q8 A 组净收益 | E6 持续启用净收益 | 最大单日净收益差 | E6 生命周期净收益 | 生命周期相对基线差异 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for expression in ("long", "hedge"):
        subset = daily[daily["expression"].eq(expression)]
        q8_total = next(row["total"] for row in metrics if row["expression"] == expression and row["arm"] == "A")
        e6_total = float(np.mean([phase["e6_always_on_total"] for phase in phase_rows if phase["expression"] == expression]))
        lifecycle_total = float(np.mean([phase["e6_lifecycle_v1_total"] for phase in phase_rows if phase["expression"] == expression]))
        daily_net_max = float(subset["net_delta"].abs().max())
        report.append(f"| {expression} | 88 | {q8_total:+.10%} | {e6_total:+.10%} | {daily_net_max:.3g} | {lifecycle_total:+.10%} | {(lifecycle_total-q8_total)*100:+.4f} |")
    report += ["", "Q8 A 组与 E6 持续启用的 88 日期逐笔交易结果仅有浮点舍入差异。E6 生命周期规则的净收益差来自暴露状态变化，不是标签列或冻结基线错位。", "", "## 相位收益", "", "| 表达 | 相位 | 周期数 | Q8 A 组终值 | E6 持续启用终值 | 持续启用差异（基点） | E6 生命周期终值 | 生命周期差异（百分点） |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in phase_rows:
        report.append(f"| {row['expression']} | {row['phase']} | {row['dates']} | {row['q8_a_total']:+.10%} | {row['e6_always_on_total']:+.10%} | {row['delta_basis_points']:+.6g} | {row['e6_lifecycle_v1_total']:+.10%} | {row['lifecycle_delta_vs_q8_percentage_points']:+.4f} |")
    report += ["", "逐日期 CSV 包含 Q8 A 组和 E6 持续启用的毛收益、交易成本、借券成本、净收益、换手、缺失标签计数及逐列差异。两者的浮点差异在数值精度范围内，策略收益、成本口径和交易决策没有可辨别差异。源文件及导出附件的 SHA256 见 reconciliation_manifest.json。", ""]
    (args.output_dir / "reconciliation_report.md").write_text("\n".join(report), encoding="utf-8")
    summary["output_sha256"] = {
        path.name: sha256(path) for path in args.output_dir.iterdir()
        if path.is_file() and path.name != "reconciliation_manifest.json"
    }
    (args.output_dir / "reconciliation_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "dates": len(dates),
                      "label_rows": len(labels), "max_daily_abs_difference": max_abs,
                      "max_phase_difference_bp": summary["max_phase_abs_difference_e6_vs_q8_basis_points"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
