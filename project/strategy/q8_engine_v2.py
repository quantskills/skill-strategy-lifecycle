"""Project-local, auditable engine. Upstream skills remain unmodified."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# E6 carries this strategy implementation locally.  The frozen input root is
# configurable so the copied engine can run inside an EVO project package
# without importing Q8's Python module at runtime.
DATA = Path(os.environ.get("E6_STRATEGY_DATA_ROOT", ROOT / "frozen_q8" / "data")).resolve()
OUT = ROOT / "results" / "v2"
CFG_PATH = Path(os.environ.get("E6_STRATEGY_CONFIG", ROOT / "strategy" / "audit_v2.json")).resolve()
CFG = json.loads(CFG_PATH.read_text(encoding="utf-8"))
OUT.mkdir(parents=True, exist_ok=True)


def data_file(name):
    path = DATA / name
    if path.is_file():
        return path
    for suffix in (".xz", ".gz"):
        packed = DATA / (name + suffix)
        if packed.is_file():
            return packed
    return path


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def recorded_cli(cmd, directory, metadata):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / "input.json", {**metadata, "argv": cmd, "entry_sha256": sha(cmd[1])})
    (directory / "cmd.txt").write_text(subprocess.list2cmdline(cmd) + "\n", encoding="utf-8")
    process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT, env={**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8",
                      "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
    (directory / "stdout.log").write_text(process.stdout + "\n--- stderr ---\n" + process.stderr, encoding="utf-8")
    save(directory / "status.json", {"exit_code": process.returncode, "ok": process.returncode == 0})
    if process.returncode:
        raise RuntimeError(f"CLI failed: {directory}: {process.stderr[-2000:]}")


def load_cache():
    factors = json.loads((DATA / "upstream_summary.json").read_text(encoding="utf-8"))["factors"]["selected_names"]
    weekly = pd.read_csv(data_file("weekly_slim.csv"), dtype={"date": str, "symbol": str,
        "in_universe": "boolean", "entry_tradable": "boolean"}, low_memory=False)
    raw = pd.read_csv(data_file("panel_for_platform.csv"), dtype={"date": str, "symbol": str})
    assert not weekly.duplicated(["date", "symbol"]).any()
    assert not raw.duplicated(["date", "symbol"]).any()
    raw = raw.drop(columns="fwd_ret").merge(weekly, on=["date", "symbol"], validate="one_to_one")
    cache = {}
    for date, group in raw.groupby("date", sort=True):
        x = group[factors].to_numpy(dtype=float)
        cache[date] = {"sym": group.symbol.to_numpy(), "X": x,
            "score": group.score_wf.to_numpy(dtype=float),
            "ew": group[factors].mean(axis=1).to_numpy(dtype=float),
            "ret": group.fwd_ret.to_numpy(dtype=float),
            "elig": (group.in_universe.fillna(False) & group.entry_tradable.fillna(False)).to_numpy(dtype=bool)}
    return cache, factors


def mature_frame(series, dates):
    """Pure point-in-time IC monitor, suitable for leakage regression checks."""
    rows = []
    for i, date in enumerate(dates):
        available = series.iloc[:max(0, i - CFG["maturity_observations"] + 1)].dropna()
        rows.append({"date": date, "ic": series[date],
            "monitor": available.iloc[-13:].mean() if len(available) >= 13 else np.nan,
            "last_label_date": available.index[-1] if len(available) else None})
    frame = pd.DataFrame(rows).set_index("date")
    calibration = frame.loc[CFG["calibration_start"]:CFG["calibration_end"], "monitor"].dropna()
    assert len(calibration) > 20 and max(calibration.index) < CFG["eval_start"]
    theta = float(calibration.median())
    frame["ic_alarm"] = frame.monitor.lt(theta)
    return frame, calibration, theta


def monitors(cache):
    dates = list(cache)
    ic = {}
    for date, c in cache.items():
        valid = c["elig"] & np.isfinite(c["score"]) & np.isfinite(c["ret"])
        ic[date] = pd.Series(c["score"][valid]).corr(pd.Series(c["ret"][valid]), method="spearman") if valid.sum() > 30 else np.nan
    frame, calibration, theta = mature_frame(pd.Series(ic), dates)
    detail = pd.read_csv(ROOT / "runs/r01_monitor_entry/health/health_timeseries.csv")
    detail["date"] = pd.to_datetime(detail.date).dt.strftime("%Y%m%d")
    detail = detail.set_index("date")
    state = detail.decline_probability.ge(.8) | detail.cusum_alarm.eq(True)
    frame["health_alarm"] = state.reindex(frame.index).ffill().fillna(False).astype(bool)
    frame["health_probability"] = detail.decline_probability.reindex(frame.index).ffill()
    # PSI is an already-recorded contemporaneous diagnostic, not a refitted threshold.
    psi = pd.read_csv(DATA / "third_period_psi.csv", dtype={"date": str}).set_index("date").triggered
    frame["psi_alarm"] = psi.reindex(frame.index).fillna(0).astype(bool)
    frame.to_csv(OUT / "monitor.csv")
    save(OUT / "calibration.json", {"theta": theta, "start": min(calibration.index),
        "end": max(calibration.index), "observations": len(calibration), "uses_evaluation_data": False,
        "exit_threshold_equals_entry": True, "latch_extra_delay": 0,
        "health_baseline_end": detail.index[119], "health_cli": "runs/r01_monitor_entry/health_entry"})
    return frame


class Models:
    """Sufficient statistics avoid concatenating training matrices for each seed."""
    def __init__(self, cache):
        self.cache, self.dates, self.models = cache, list(cache), {}
        self.parts = []
        for c in cache.values():
            ok = np.isfinite(c["X"]).all(axis=1) & np.isfinite(c["ret"])
            x, y = c["X"][ok], c["ret"][ok]
            self.parts.append((len(x), x.sum(axis=0), float(y.sum()), x.T @ x, x.T @ y))

    def fit(self, date):
        if date in self.models:
            return self.models[date]
        i = self.dates.index(date)
        end = i - CFG["maturity_observations"]  # excludes date i-5: same conservative platform fold
        start = end - CFG["train_observations"]
        assert start >= 0
        parts = self.parts[start:end]
        n = sum(p[0] for p in parts)
        sx, sy = sum(p[1] for p in parts), sum(p[2] for p in parts)
        xx, xy = sum(p[3] for p in parts), sum(p[4] for p in parts)
        mean_x, mean_y = sx / n, sy / n
        w = np.linalg.solve(xx - np.outer(sx, sx) / n + np.eye(len(sx)), xy - sx * sy / n)
        self.models[date] = (w, mean_x, mean_y)
        return self.models[date]

    def predictions(self, starts, eval_dates):
        starts = set(starts)
        active, output = None, {}
        for date in eval_dates:
            if date in starts:
                active = self.fit(date)
            if active is not None:
                w, xm, ym = active
                output[date] = (self.cache[date]["X"] - xm) @ w + ym
        return output


def episodes(state):
    """Half-open events; unfinished last episode is explicitly right-censored."""
    events, active = [], None
    for i, (date, value) in enumerate(state.items()):
        if value and active is None:
            active = (date, i)
        if not value and active is not None:
            events.append({"start": active[0], "end_exclusive": date,
                "duration_observations": i - active[1], "right_censored": False})
            active = None
    if active is not None:
        events.append({"start": active[0], "end_exclusive": None,
            "duration_observations": len(state) - active[1], "right_censored": True})
    return events


def turnover(current, previous):
    keys = current.keys() | previous.keys()
    return float(sum(abs(current.get(k, 0.0) - previous.get(k, 0.0)) for k in keys))


def max_drawdown(returns):
    wealth = np.r_[1.0, np.cumprod(1 + np.asarray(returns))]
    return float(np.min(wealth / np.maximum.accumulate(wealth) - 1))


def simulate(cache, eval_dates, expression, scores=None, exposure=None, alarm=None, missing_fill=0.0):
    """Four independent phase accounts. Trades include old names and drifted weights."""
    rows = []
    for phase in range(4):
        old_long, old_short = {}, {}
        for cycle, date in enumerate(eval_dates[phase::4]):
            c = cache[date]
            # Common pool depends only on current information, never future label presence.
            pool = c["elig"] & np.isfinite(c["score"]) & np.isfinite(c["X"]).all(axis=1)
            ids = np.flatnonzero(pool)
            values = c["score"] if scores is None or date not in scores else scores[date]
            assert np.isfinite(values[ids]).all() and len(ids) >= CFG["top_n"]
            picks = ids[np.lexsort((c["sym"][ids], -values[ids]))[:CFG["top_n"]]]
            e = 1.0 if exposure is None else exposure.get(date, 1.0)
            lw = {str(c["sym"][j]): e / CFG["top_n"] for j in picks}
            sw = {str(c["sym"][j]): e / len(ids) for j in ids} if expression == "hedge" else {}
            lt, st = turnover(lw, old_long), turnover(sw, old_short)
            rr = np.where(np.isfinite(c["ret"]), c["ret"], missing_fill)
            gross = float(e * rr[picks].mean() - (e * rr[ids].mean() if expression == "hedge" else 0))
            trade_cost = CFG["one_way_cost"] * (lt + st)
            borrow = CFG["borrow_annual"] * 4 / 52 * e if expression == "hedge" else 0.0
            net = gross - trade_cost - borrow
            assert net > -1, "Account bankrupt; stop rather than compound invalid returns"
            rows.append({"date": date, "phase": phase, "cycle": cycle, "expression": expression,
                "gross": gross, "trade_cost": trade_cost, "borrow_cost": borrow,
                "cost": trade_cost + borrow, "net": net, "turnover": lt + st, "exposure": e,
                "alarm": bool(alarm.get(date, False)) if alarm is not None else False,
                "missing_long": int((~np.isfinite(c["ret"][picks])).sum()),
                "missing_pool": int((~np.isfinite(c["ret"][ids])).sum()), "pool": len(ids)})
            # Mark existing holdings to end-of-period prices and account NAV before next rebalance.
            old_long = {str(c["sym"][j]): lw[str(c["sym"][j])] * (1 + rr[j]) / (1 + net) for j in picks}
            old_short = {str(c["sym"][j]): sw[str(c["sym"][j])] * (1 + rr[j]) / (1 + net) for j in ids} if sw else {}
    return pd.DataFrame(rows)


def cycle_series(frame):
    return frame.groupby("cycle").net.mean().sort_index()


def metrics(frame):
    totals, sharpes, drawdowns = [], [], []
    for _, phase in frame.groupby("phase"):
        r = phase.sort_values("cycle").net.to_numpy()
        totals.append(float(np.prod(1 + r) - 1))
        sharpes.append(float(r.mean() / r.std(ddof=1) * np.sqrt(13)) if len(r) > 2 and r.std(ddof=1) else np.nan)
        drawdowns.append(max_drawdown(r))
    return {"cycles": int(frame.cycle.nunique()), "phase_count": len(totals),
        "total": float(np.mean(totals)), "phase_totals": totals,
        "sharpe_mean": float(np.nanmean(sharpes)), "worst_phase_mdd": float(min(drawdowns)),
        "cost_sum_mean": float(frame.cost.sum() / 4), "avg_exposure": float(frame.exposure.mean()),
        "missing_long": int(frame.missing_long.sum()), "missing_pool": int(frame.missing_pool.sum())}


def platform_refits(cache, factors, models, starts):
    cli = ROOT / ".agents/skills/skill-ml-factor-ensemble/scripts/ml_ensemble.py"
    records = []
    dates = list(cache)
    for date in sorted(set(starts)):
        i = dates.index(date)
        end = i - 5
        train_dates = dates[end - 156:end]
        directory = ROOT / "runs/v2/refits" / date
        directory.mkdir(parents=True, exist_ok=True)
        window = directory / "panel_window.csv.gz"
        frames = []
        for d in dates[end - 156:i + 1]:
            c = cache[d]
            f = pd.DataFrame(c["X"], columns=factors)
            f.insert(0, "symbol", c["sym"])
            f.insert(0, "date", d)
            f["fwd_ret"] = c["ret"]
            frames.append(f)
        pd.concat(frames, ignore_index=True).to_csv(window, index=False, compression={"method": "gzip", "compresslevel": 1})
        del frames
        cmd = [sys.executable, str(cli), "--panel-csv", str(window), "--factors", ",".join(factors),
               "--model", "ridge", "--horizon", "5", "--train-window", "161", "--step", "4",
               "--embargo", "0", "--out", str(directory / "oos_scores.csv"), "--report", str(directory / "report.md")]
        recorded_cli(cmd, directory, {"date": date, "train_first": train_dates[0], "train_last": train_dates[-1],
            "train_observations": len(train_dates), "gap_observations": i - dates.index(train_dates[-1]),
            "input": str(window.relative_to(ROOT)), "input_sha256": sha(window),
            "origin": "project-local unchanged copy of QuantStudio skill version 51bc5513cc950b21ceb8dd755082922ffc49bae0"})
        scores = pd.read_csv(directory / "oos_scores.csv", dtype={"symbol": str}).set_index("symbol").score
        w, xm, ym = models.fit(date)
        c = cache[date]
        predicted = pd.Series((c["X"] - xm) @ w + ym, index=c["sym"])
        both = pd.DataFrame({"cli": scores, "fast": predicted}).dropna()
        error = float((both.cli - both.fast).abs().max())
        rho = float(both.cli.corr(both.fast, method="spearman"))
        assert error < 1e-8 and rho > .999999
        rec = {"date": date, "exit_code": 0, "spearman": rho, "max_abs_diff": error,
               "train_last": train_dates[-1], "gap_observations": 6, "rows": len(both)}
        save(directory / "verification.json", rec)
        records.append(rec)
        print(f"[refit] {date}: rho={rho:.9f} error={error:.2g}", flush=True)
    save(OUT / "platform_refits.json", {"all_ok": True, "records": records})
    return records


def main():
    cache, factors = load_cache()
    dates = list(cache)
    # Exclude unknown outcomes solely using label-maturity calendar, not realized returns.
    available = [d for i, d in enumerate(dates) if d >= CFG["eval_start"] and i + 5 < len(dates)]
    eval_dates = available[:len(available) // 4 * 4]
    monitor = monitors(cache)
    models = Models(cache)
    # The comparison branch can request a new one-week-label baseline. The
    # authoritative v2 config leaves this false and keeps the frozen upstream score.
    base_scores = {d: cache[d]["score"] for d in eval_dates}
    if CFG.get("one_week_label", False):
        base_scores = models.predictions(eval_dates[::CFG.get("baseline_refit_step_observations", 4)], eval_dates)
    ic = monitor.ic_alarm.to_dict()
    health = monitor.health_alarm.to_dict()
    psi = monitor.psi_alarm.to_dict()
    starts_ic = [e["start"] for e in episodes(monitor.loc[eval_dates, "ic_alarm"])]
    starts_health = [e["start"] for e in episodes(monitor.loc[eval_dates, "health_alarm"])]
    refs = platform_refits(cache, factors, models, starts_ic + starts_health)
    ridge_ew = {d: cache[d]["ew"] for d in eval_dates}
    refit_ic = dict(base_scores)
    refit_ic.update(models.predictions(starts_ic, eval_dates))
    refit_health = dict(base_scores)
    refit_health.update(models.predictions(starts_health, eval_dates))
    switch = lambda state: {d: cache[d]["ew"] if state[d] else base_scores[d] for d in eval_dates}
    half = lambda state: {d: .5 if state[d] else 1. for d in eval_dates}
    specifications = {
        "A": (base_scores, None, None), "B": (base_scores, {d: .5 for d in eval_dates}, None),
        "A_ew": (ridge_ew, None, None), "C_ic": (base_scores, half(ic), ic),
        "D_ic": (refit_ic, None, ic), "F_ic": (switch(ic), None, ic),
        "C_health": (base_scores, half(health), health), "D_health": (refit_health, None, health),
        "F_health": (switch(health), None, health), "C_psi": (base_scores, half(psi), psi)}
    all_rows, summary, stress = [], [], []
    for expression in ("long", "hedge"):
        for arm, (scores, exposure, alarm) in specifications.items():
            frame = simulate(cache, eval_dates, expression, scores, exposure, alarm)
            frame["arm"] = arm
            all_rows.append(frame)
            summary.append({"expression": expression, "arm": arm, **metrics(frame)})
            stressed = simulate(cache, eval_dates, expression, scores, exposure, alarm, missing_fill=-1.)
            stress.append({"expression": expression, "arm": arm, **metrics(stressed)})
        print(f"[backtest] {expression}: 10 arms completed", flush=True)
    pd.concat(all_rows).to_csv(OUT / "trades.csv", index=False)
    save(OUT / "metrics.json", summary)
    save(OUT / "missing_return_sensitivity.json", stress)
    # Same random schedules for expressions; 50 seeds; sufficient-statistic fits reused.
    rng = np.random.default_rng(CFG["seed"])
    placebo = []
    n_alarm = int(monitor.loc[eval_dates, "ic_alarm"].sum())
    for seed in range(CFG["placebo_seeds"]):
        random_days = set(rng.choice(eval_dates, size=n_alarm, replace=False))
        random_starts = sorted(rng.choice(eval_dates, size=len(starts_ic), replace=False))
        random_state = {d: d in random_days for d in eval_dates}
        random_refit = dict(base_scores)
        random_refit.update(models.predictions(random_starts, eval_dates))
        for expression in ("long", "hedge"):
            for arm, sc, ex in (("C_ic", base_scores, half(random_state)),
                                 ("F_ic", switch(random_state), None), ("D_ic", random_refit, None)):
                result = simulate(cache, eval_dates, expression, sc, ex)
                placebo.append({"seed": seed, "expression": expression, "arm": arm, "total": metrics(result)["total"]})
        if (seed + 1) % 10 == 0:
            print(f"[placebo] {seed + 1}/{CFG['placebo_seeds']} seeds", flush=True)
    pd.DataFrame(placebo).to_csv(OUT / "placebo.csv", index=False)
    # Equivalence with the archived EW signal is a source alignment, not proof of OOS factor selection.
    stored = pd.read_csv(DATA / "validation_2026_signal.csv", dtype={"date": str, "symbol": str})
    ew = pd.concat([pd.DataFrame({"date": d, "symbol": c["sym"], "score_ew": c["ew"]}) for d, c in cache.items()])
    aligned = stored.merge(ew, on=["date", "symbol"])
    rho = float(aligned.score.corr(aligned.score_ew, method="spearman"))
    assert rho > .999999
    save(OUT / "backtest_audit.json", {"all_ok": True, "eval_dates": eval_dates,
        "excluded_tail": [d for d in dates if d >= CFG["eval_start"] and d not in eval_dates],
        "excluded_for_phase_balance": available[len(eval_dates):],
        "excluded_for_label_maturity": [d for i, d in enumerate(dates) if d >= CFG["eval_start"] and i + 5 >= len(dates)],
        "phase_cycles": len(eval_dates) // 4, "factor_count": len(factors),
        "refit_count": len(refs), "ew_archive_spearman": rho, "ew_overlap": len(aligned),
        "plan_sha256": sha(ROOT / "config/audit_v2.json"),
        "interpretation": "retrospective corrected historical experiment, not untouched future validation",
        "refit_placebo": "50 seeds, same number of update dates; offline null, not executable predictive policy"})


if __name__ == "__main__":
    main()
