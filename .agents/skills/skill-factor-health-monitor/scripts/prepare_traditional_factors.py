from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def build(source: str, output: str, min_history: int = 120) -> pd.DataFrame:
    path = Path(source)
    data = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    required = {"date", "symbol", "close"}
    if not required <= set(data): raise ValueError(f"缺少字段: {sorted(required-set(data))}")
    data = data[["date", "symbol", "close"]].copy()
    data["date"] = pd.to_datetime(data.date.astype(str)); data["close"] = pd.to_numeric(data.close, errors="coerce")
    data = data.dropna().sort_values(["symbol", "date"]); g = data.groupby("symbol", group_keys=False).close
    ret1 = g.pct_change(fill_method=None); raw = data[["date", "symbol"]].copy()
    for name, days, sign in (("momentum_20",20,1),("momentum_60",60,1),("reversal_5",5,-1)):
        raw[name] = sign * g.pct_change(days, fill_method=None).groupby(data.symbol).shift(1)
    vol = ret1.groupby(data.symbol).rolling(20).std().reset_index(level=0, drop=True)
    raw["lowvol_20"] = -vol.groupby(data.symbol).shift(1)
    high = g.rolling(60).max().reset_index(level=0, drop=True); dd = data.close/high-1
    raw["drawdown_60"] = dd.groupby(data.symbol).shift(1)
    raw["next_return"] = ret1.groupby(data.symbol).shift(-1)
    raw = raw.replace([np.inf,-np.inf],np.nan).dropna(); counts=raw.groupby("symbol").size()
    raw=raw[raw.symbol.isin(counts[counts>=min_history].index)]
    factors=[c for c in raw if c not in {"date","symbol","next_return"}]
    panel=raw.melt(id_vars=["date","symbol","next_return"],value_vars=factors,var_name="factor",value_name="value")
    out=Path(output); out.parent.mkdir(parents=True,exist_ok=True); panel.to_parquet(out,index=False); return panel


if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--source",required=True); p.add_argument("--output",required=True)
    a=p.parse_args(); d=build(a.source,a.output); print({"rows":len(d),"symbols":d.symbol.nunique(),"factors":d.factor.nunique(),"signal_lag_days":1})
