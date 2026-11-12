from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass
class Config:
    windows: tuple[int, ...] = (20, 60, 120)
    baseline_days: int = 120
    decline_threshold: float = 0.02
    process_var: float = 1e-6
    observation_var: float = 2.5e-3
    cusum_drift: float = 0.25
    cusum_threshold: float = 5.0
    weighting: str = "linear"


def _read(path:Path)->pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower()==".parquet" else pd.read_csv(path)


def load_panel(path: str | Path, returns_path: str | Path | None=None) -> pd.DataFrame:
    p=Path(path)
    if p.is_dir():
        frames=[]
        for file in sorted([*p.glob("*.parquet"),*p.glob("*.csv")]):
            part=_read(file)
            if {"date","symbol","factor_value"}<=set(part): part=part.rename(columns={"factor_value":"value"})
            if {"date","symbol","value"}<=set(part):
                part=part[["date","symbol","value"]].copy(); part["factor"]=file.stem; frames.append(part)
        if not frames: raise ValueError("因子目录中没有兼容的 CSV/Parquet")
        if returns_path is None: raise ValueError("读取因子目录时必须提供 --returns")
        ret=_read(Path(returns_path)); df=pd.concat(frames,ignore_index=True).merge(ret[["date","symbol","next_return"]],on=["date","symbol"],how="inner")
    else:
        df=_read(p)
        if "factor" not in df and {"date","symbol","next_return"}<=set(df):
            value_cols=[c for c in df if c not in {"date","symbol","next_return"}]
            df=df.melt(id_vars=["date","symbol","next_return"],value_vars=value_cols,var_name="factor",value_name="value")
    needed={"date","symbol","factor","value","next_return"}
    if not needed<=set(df): raise ValueError(f"缺少字段: {sorted(needed-set(df))}")
    df["date"]=pd.to_datetime(df.date); df["value"]=pd.to_numeric(df.value,errors="coerce"); df["next_return"]=pd.to_numeric(df.next_return,errors="coerce")
    return df.dropna(subset=["date","symbol","factor","value","next_return"])


def daily_ic(panel: pd.DataFrame, min_symbols: int = 20) -> pd.DataFrame:
    rows=[]
    for (date,factor),g in panel.groupby(["date","factor"]):
        if g.symbol.nunique()<min_symbols: continue
        value=float(g.value.corr(g.next_return,method="spearman"))
        if np.isfinite(value): rows.append((date,factor,value,g.symbol.nunique()))
    if not rows: raise ValueError("没有满足最小股票数的日度截面")
    return pd.DataFrame(rows,columns=["date","factor","ic","n_symbols"]).sort_values(["factor","date"])


def kalman_random_walk(values: np.ndarray, q: float, r: float) -> tuple[np.ndarray,np.ndarray]:
    if q<=0 or r<=0: raise ValueError("状态和观测方差必须大于零")
    m=float(values[0]); p=r; means=[]; variances=[]
    for y in values:
        pred_p=p+q; k=pred_p/(pred_p+r); m=m+k*(float(y)-m); p=(1-k)*pred_p
        means.append(m); variances.append(p)
    return np.asarray(means),np.asarray(variances)


def negative_cusum(values: np.ndarray, baseline_days:int, drift:float, threshold:float)->np.ndarray:
    out=np.zeros(len(values),dtype=bool); warm=np.asarray(values[:min(baseline_days,len(values))]); mu=float(warm.mean()); sigma=float(warm.std(ddof=1)) or 1e-6; s=0.0
    for i,x in enumerate(values):
        if i<min(baseline_days,len(values)): continue
        z=(float(x)-mu)/sigma; s=min(0.0,s+z+drift)
        if s<=-threshold: out[i]=True; s=0.0
    return out


def recommended_weight(prob: float, mode: str)->float:
    if mode=="linear": return float(np.clip(1-prob,0,1))
    if mode=="step": return 1.0 if prob<.5 else .5 if prob<.8 else 0.0
    raise ValueError("weighting 只支持 linear 或 step")


def analyze(ic: pd.DataFrame, config:Config)->tuple[pd.DataFrame,pd.DataFrame]:
    detail=[]; summary=[]
    for factor,g in ic.groupby("factor",sort=True):
        g=g.sort_values("date").copy(); vals=g.ic.to_numpy(); means,vars_=kalman_random_walk(vals,config.process_var,config.observation_var)
        base_n=min(config.baseline_days,len(vals)); baseline=float(np.mean(vals[:base_n])); cutoff=baseline-config.decline_threshold
        probs=norm.cdf((cutoff-means)/np.sqrt(vars_)); probs[:base_n]=0.0; alarms=negative_cusum(vals,config.baseline_days,config.cusum_drift,config.cusum_threshold)
        g["posterior_ic"]=means; g["ci_low"]=means-1.96*np.sqrt(vars_); g["ci_high"]=means+1.96*np.sqrt(vars_); g["decline_probability"]=probs; g["cusum_alarm"]=alarms
        for w in config.windows:
            g[f"ic_{w}"]=g.ic.rolling(w,min_periods=max(5,w//2)).mean(); g[f"ir_{w}"]=g.ic.rolling(w,min_periods=max(5,w//2)).mean()/g.ic.rolling(w,min_periods=max(5,w//2)).std(ddof=1)
        prob=float(probs[-1]); health=int(np.clip(round(100*(1-prob)),0,100)); level="红" if prob>=.8 or alarms[-1] else "黄" if prob>=.5 else "绿"
        summary.append({"factor":factor,"health_score":health,"decline_probability":prob,"alert_level":level,"recommended_weight_multiplier":recommended_weight(prob,config.weighting),"current_posterior_ic":float(means[-1]),"baseline_ic":baseline,"latest_cusum_alarm":bool(alarms[-1])})
        detail.append(g)
    return pd.concat(detail,ignore_index=True),pd.DataFrame(summary)


def warning_lead_days(detail:pd.DataFrame, ic_floor:float=.02, probability_threshold:float=.8, baseline_days:int=120)->pd.DataFrame:
    rows=[]
    for factor,g in detail.groupby("factor"):
        g=g.sort_values("date").copy(); eligible=np.arange(len(g))>=baseline_days
        persistent=g.decline_probability.rolling(10,min_periods=10).mean()>=probability_threshold; corroborated=g.cusum_alarm & (g.decline_probability.rolling(5,min_periods=5).mean()>=.5)
        warning=g.loc[eligible & (persistent|corroborated),"date"]; breach=g.loc[eligible & (g.posterior_ic<ic_floor),"date"]
        lead=math.nan
        if len(warning) and len(breach):
            first_breach=breach.iloc[0]; earlier=warning[warning<first_breach]
            if len(earlier):
                warning_date=earlier.iloc[0]; lead=float(((g.date>warning_date)&(g.date<first_breach)).sum())
        rows.append({"factor":factor,"warning_date":str(warning.iloc[0].date()) if len(warning) else None,"breach_date":str(breach.iloc[0].date()) if len(breach) else None,"lead_trading_days":lead,"passes_5_day_target":bool(np.isfinite(lead) and lead>5)})
    return pd.DataFrame(rows)


def write_outputs(detail:pd.DataFrame,summary:pd.DataFrame,out:Path,config:Config)->None:
    out.mkdir(parents=True,exist_ok=True); detail.to_csv(out/"health_timeseries.csv",index=False,encoding="utf-8-sig"); summary.to_csv(out/"health_summary.csv",index=False,encoding="utf-8-sig")
    (out/"state.json").write_text(json.dumps({"config":asdict(config),"latest":summary.to_dict("records")},ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# 因子健康度报告","",f"更新日期：{detail.date.max().date()}","","| 因子 | 健康度 | 衰退概率 | 预警 | 建议权重系数 |","|---|---:|---:|---|---:|"]
    for r in summary.itertuples(): lines.append(f"| {r.factor} | {r.health_score} | {r.decline_probability:.1%} | {r.alert_level} | {r.recommended_weight_multiplier:.2f} |")
    lines += ["","> 黄/红灯表示统计上的衰退迹象，不等同于因子永久失效。调整权重前应检查数据修订、股票池变化、成本和市场结构。"]
    (out/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    n=len(summary); fig,axes=plt.subplots(n,1,figsize=(10,max(3,3*n)),squeeze=False)
    for ax,(factor,g) in zip(axes[:,0],detail.groupby("factor")):
        g=g.sort_values("date"); x=pd.to_datetime(g.date).to_numpy(); ax.plot(x,g.ic,label="daily IC",alpha=.35); ax.plot(x,g.posterior_ic,label="posterior IC"); ax.fill_between(x,g.ci_low,g.ci_high,alpha=.2); alarms=g[g.cusum_alarm]; ax.scatter(alarms.date,alarms.posterior_ic,color="red",s=20,label="CUSUM"); ax.set_title(str(factor)); ax.legend(loc="best")
    fig.tight_layout(); fig.savefig(out/"health_trends.png",dpi=140); plt.close(fig)


def run(panel:str,output:str,config:Config,returns_path:str|None=None)->dict:
    ic=daily_ic(load_panel(panel,returns_path)); detail,summary=analyze(ic,config); out=Path(output); write_outputs(detail,summary,out,config); lead=warning_lead_days(detail,baseline_days=config.baseline_days); lead.to_csv(out/"warning_validation.csv",index=False,encoding="utf-8-sig")
    return {"factors":len(summary),"red":int((summary.alert_level=="红").sum()),"output":str(out)}


def main(argv=None)->int:
    p=argparse.ArgumentParser(description="因子衰退预警与动态再平衡")
    p.add_argument("--panel",required=True); p.add_argument("--returns",help="因子目录模式下的次日收益文件"); p.add_argument("--output",default="outputs/factor-health"); p.add_argument("--weighting",choices=["linear","step"],default="linear")
    a=p.parse_args(argv); print(json.dumps(run(a.panel,a.output,Config(weighting=a.weighting),a.returns),ensure_ascii=False)); return 0


if __name__=="__main__": raise SystemExit(main())
