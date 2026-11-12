"""Causal, deterministic strategy lifecycle controller for E6.

The controller consumes a monitor table in date order. It never reads realised
returns; the caller may merge ledger rows with realised trade results afterward.
The transition occurs before the trade on the same decision date, so the
returned exposure is the exposure used by the backtest at that date.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping

import pandas as pd


@dataclass(frozen=True)
class LifecycleParams:
    theta: float
    cooldown_observations: int = 2
    retire_alarm_count: int = 3
    retire_window: int = 4
    recovery_good_observations: int = 3
    downweight: float = 0.5
    reactivation_weight: float = 0.5
    retirement_enabled: bool = True
    reactivation_ramp: bool = True


def _bool(value: object) -> bool:
    return bool(value) if value is not None and not pd.isna(value) else False


def _prefix_hash(values: list[dict]) -> str:
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def replay_lifecycle(
    monitor: pd.DataFrame,
    dates: Iterable[str] | None,
    params: LifecycleParams,
    *,
    monitor_column: str = "monitor",
    health_column: str = "health_alarm",
    alarm_column: str = "ic_alarm",
) -> pd.DataFrame:
    """Replay the pre-registered lifecycle policy on a causal monitor table.

    ``monitor`` must contain one row per decision date and be sorted by date (or
    have a sortable ``date`` column/index). If ``ic_alarm`` is absent it is
    derived as ``monitor < theta``. A missing monitor is treated as no alarm and
    is retained in the ledger via ``monitor_observed=False``; callers should
    report this count and decide whether to delay trading separately.

    The state is initialised to ``FULL`` immediately before the first supplied
    date. State counters are updated using only the current row and prior
    ledger rows. The output is a replayable ledger; realised returns and costs
    are intentionally absent so that no outcome can affect a transition.
    """
    if params.cooldown_observations < 1 or params.recovery_good_observations < 1:
        raise ValueError("cooldown and recovery lengths must be positive")
    if params.retire_alarm_count < 1 or params.retire_window < params.retire_alarm_count:
        raise ValueError("retirement count/window are inconsistent")
    if not 0 <= params.downweight <= 1 or not 0 <= params.reactivation_weight <= 1:
        raise ValueError('exposure must be between zero and one')

    frame = monitor.copy()
    if "date" in frame.columns:
        frame["date"] = frame["date"].astype(str)
        frame = frame.set_index("date", drop=False)
    else:
        frame.index = frame.index.astype(str)
        frame["date"] = frame.index
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise ValueError('duplicate monitor dates')
    wanted = list(map(str, dates)) if dates is not None else list(frame.index)
    if wanted != sorted(set(wanted)):
        raise ValueError('decision dates must be unique and chronological')
    missing = [date for date in wanted if date not in frame.index]
    if missing:
        raise KeyError(f"monitor rows missing for {missing[:3]}")

    state = "FULL"
    alarm_streak = 0
    good_streak = 0
    cooldown_age = 0
    history: list[dict] = []
    rows: list[dict] = []
    for decision_index, date in enumerate(wanted):
        source = frame.loc[date]
        monitor_value = source.get(monitor_column)
        observed = monitor_value is not None and not pd.isna(monitor_value)
        cutoff_value = source.get("monitor_cutoff", source.get("last_label_date"))
        if cutoff_value is None or pd.isna(cutoff_value):
            cutoff = None
        elif isinstance(cutoff_value, float) and cutoff_value.is_integer():
            cutoff = str(int(cutoff_value))
        else:
            cutoff = str(cutoff_value)
        alarm = _bool(source.get(alarm_column)) if alarm_column in source else bool(observed and float(monitor_value) < params.theta)
        health = _bool(source.get(health_column))
        severe = bool(alarm and health)
        if not observed:
            alarm_streak = 0
            good_streak = 0
        elif alarm:
            alarm_streak += 1
            good_streak = 0
        else:
            alarm_streak = 0
            good_streak = good_streak + 1 if not health else 0

        previous = state
        reason = "hold"
        if not observed:
            reason = 'missing_monitor_hold'
        elif state == "FULL":
            if alarm:
                state, reason = "DOWNWEIGHT", "first_alarm"
                cooldown_age = 0
        elif state == "DOWNWEIGHT":
            if severe or alarm_streak >= 2:
                state, reason = "COOLDOWN", "confirmed_or_severe_alarm"
                cooldown_age = 1
            elif not alarm:
                state, reason = "FULL", "alarm_cleared"
        elif state == "COOLDOWN":
            cooldown_age += 1 if previous == "COOLDOWN" else 0
            rolling = sum(1 for x in history[-(params.retire_window - 1):] if x["alarm_ic"])
            retire = alarm and (
                alarm_streak >= params.retire_alarm_count
                or rolling + 1 >= params.retire_alarm_count
            )
            if cooldown_age < params.cooldown_observations:
                reason = "minimum_cooldown"
            elif retire and params.retirement_enabled:
                state, reason = "RETIRED", "persistent_alarm"
            elif good_streak >= params.recovery_good_observations and not health:
                state, reason = ("REACTIVATING" if params.reactivation_ramp else "FULL"), "recovery_gate"
            else:
                reason = "await_recovery_gate"
        elif state == "RETIRED":
            if good_streak >= params.recovery_good_observations and not health:
                state, reason = ("REACTIVATING" if params.reactivation_ramp else "FULL"), "recovery_gate"
        elif state == "REACTIVATING":
            if alarm or health:
                state, reason = "COOLDOWN", "failed_reactivation"
                cooldown_age = 1
            else:
                state, reason = "FULL", "clean_reactivation"
        else:
            raise AssertionError(f"unknown state {state}")

        exposure = {"FULL": 1.0, "DOWNWEIGHT": params.downweight,
                    "COOLDOWN": 0.0, "RETIRED": 0.0,
                    "REACTIVATING": params.reactivation_weight}[state]
        row = {
            "date": date,
            "decision_index": decision_index,
            "monitor_cutoff": cutoff,
            "monitor": None if not observed else float(monitor_value),
            "monitor_observed": observed,
            "state_before": previous,
            "state_after": state,
            "exposure": exposure,
            "alarm_ic": alarm,
            "alarm_health": health,
            "severe": severe,
            "alarm_streak": alarm_streak,
            "good_streak": good_streak,
            "cooldown_age": cooldown_age,
            "transition_reason": reason,
            # Hash the visible monitor prefix as a tamper-evident causal audit
            # anchor. It does not include realised returns.
            "visible_monitor_sha256": _prefix_hash(history + [{
                "date": date, "monitor": None if not observed else float(monitor_value),
                "alarm_ic": alarm, "alarm_health": health,
            }]),
        }
        rows.append(row)
        history.append({"date": date, "monitor": row["monitor"],
                        "alarm_ic": alarm, "alarm_health": health})
    return pd.DataFrame(rows)


__all__ = ["LifecycleParams", "replay_lifecycle"]

