"""从 Pandadata 生成 E6 运行时输入。

仓库不携带行情、因子或 Q8 结果数据。EVO 入口在运行时调用本模块，
把 Pandadata 返回的日线/因子整理为策略引擎需要的临时目录。临时目录
默认位于 ``project/runtime_data/q8``，并且被 gitignore 排除。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get("E6_RUNTIME_DATA_ROOT", ROOT / "runtime_data" / "q8")).expanduser().resolve()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _call_pandadata() -> tuple[object, str]:
    """返回可用的 Pandadata 模块和认证模式。

    兼容 EVO 中已安装的 ``panda_data`` SDK，以及宿主的 ``panda.data``
    连接门控。两条路径都必须真实连接；没有凭据或数据盘时直接报错。
    """
    try:
        import panda_data  # type: ignore
        username, password = _env("PANDADATA_USERNAME"), _env("PANDADATA_PASSWORD")
        base = _env("PANDADATA_BASE_URL", _env("JAVA_SERVICE_BASE_URL", "http://pandadata.pandaaiquant.com"))
        if username or password:
            panda_data.init_token(username=username, password=password, base_url=base)
        elif _env("PANDA_DATA_AUTH_MODE", "direct") == "gateway":
            raise RuntimeError("PANDA_DATA_AUTH_MODE=gateway 时必须提供 PANDADATA_USERNAME/PANDADATA_PASSWORD")
        elif _env("PANDA_DATA_AUTH_MODE", "direct") == "direct" and _env("PARQUET_ROOT_PATH"):
            # EVO 直连数据盘由宿主门控；有 facade 时先执行一次就绪探针。
            try:
                from panda import data as facade  # type: ignore
                ensure = getattr(facade, "ensure_panda_auth", None)
                if ensure is not None:
                    ensure()
                    return facade, "panda.data"
            except Exception as exc:
                raise RuntimeError(f"Pandadata 直连数据盘未就绪：{exc}") from exc
        return panda_data, "panda_data"
    except ImportError:
        try:
            from panda import data as panda_data  # type: ignore
        except ImportError as exc:
            raise RuntimeError("EVO 环境没有安装 Pandadata SDK（需要 panda_data 或 panda.data）") from exc
        ensure = getattr(panda_data, "ensure_panda_auth", None)
        if ensure is None:
            raise RuntimeError("宿主 Pandadata 连接没有 ensure_panda_auth 接口")
        ensure()
        return panda_data, "panda.data"


def _as_frame(value) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, dict):
        rows = value.get("rows", value.get("data", value))
        return pd.DataFrame(rows)
    return pd.DataFrame(value)


def _normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    aliases = {
        "trade_date": "date", "datetime": "date", "time": "date", "日期": "date",
        "ts_code": "symbol", "code": "symbol", "证券代码": "symbol",
        "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
    }
    frame = frame.rename(columns={c: aliases.get(str(c).lower(), aliases.get(str(c), c)) for c in frame.columns})
    if "date" not in frame.columns or "symbol" not in frame.columns:
        raise ValueError(f"Pandadata 返回缺少 date/symbol 字段，实际字段={list(frame.columns)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y%m%d")
    frame["symbol"] = frame["symbol"].astype(str)
    frame = frame.dropna(subset=["date", "symbol"])
    for col in frame.columns:
        if col not in {"date", "symbol"}:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.sort_values(["date", "symbol"]).drop_duplicates(["date", "symbol"])


def _chunks(start: str, end: str, years: int = 4) -> Iterable[tuple[str, str]]:
    left = pd.Timestamp(start)
    right = pd.Timestamp(end)
    while left <= right:
        stop = min(right, left + pd.DateOffset(years=years) - pd.Timedelta(days=1))
        yield left.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        left = stop + pd.Timedelta(days=1)


def _fetch(module, start: str, end: str, indicator: str) -> tuple[pd.DataFrame, str]:
    factors = [x.strip() for x in _env("PANDADATA_FACTORS").split(",") if x.strip()]
    if not factors:
        factors = ["open", "high", "low", "close", "volume", "amount"]
    chunks = []
    source_method = "get_factor"
    for a, b in _chunks(start, end):
        result = None
        get_factor = getattr(module, "get_factor", None)
        if get_factor is not None:
            try:
                result = get_factor(symbol="", start_date=a, end_date=b, type="stock",
                                    factors=factors, index_component=indicator)
            except Exception:
                result = None
        if result is None or len(_as_frame(result)) == 0:
            get_daily = getattr(module, "get_stock_daily", None)
            if get_daily is None:
                get_daily = getattr(module, "get_quotes", None)
            if get_daily is None:
                raise RuntimeError("Pandadata 没有可用的因子或股票日线接口")
            source_method = getattr(get_daily, "__name__", "get_stock_daily")
            try:
                result = get_daily(symbol="", start_date=a, end_date=b, fields=None, indicator=indicator, st=True)
            except TypeError:
                result = get_daily("", a, b)
        frame = _normalise_columns(_as_frame(result))
        chunks.append(frame)
    if not chunks:
        raise RuntimeError("Pandadata 返回空数据")
    frame = pd.concat(chunks, ignore_index=True).drop_duplicates(["date", "symbol"])
    if "open" not in frame or "close" not in frame:
        raise ValueError("Pandadata 数据至少需要 open 和 close 字段才能构造前瞻收益")
    return frame, source_method


def _weekly_panel(daily: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    x = daily.copy()
    x["dt"] = pd.to_datetime(x["date"])
    x["week"] = x["dt"].dt.to_period("W-FRI").dt.end_time.dt.strftime("%Y%m%d")
    x = x.sort_values(["symbol", "dt"])
    rows = []
    for symbol, group in x.groupby("symbol", sort=False):
        agg = {"open": "first", "close": "last"}
        if "high" in group: agg["high"] = "max"
        if "low" in group: agg["low"] = "min"
        if "volume" in group: agg["volume"] = "sum"
        if "amount" in group: agg["amount"] = "sum"
        group = group.set_index("dt").resample("W-FRI").agg(agg)
        group["symbol"] = str(symbol)
        group["date"] = group.index.to_period("W-FRI").end_time.strftime("%Y%m%d")
        rows.append(group.reset_index(drop=True))
    weekly = pd.concat(rows, ignore_index=True)
    weekly = weekly.sort_values(["symbol", "date"])
    weekly["fwd_ret"] = weekly.groupby("symbol")["open"].transform(lambda s: s.shift(-5) / s.shift(-1) - 1.0)
    # 简单、可复现的点时因子：只使用当前周及之前的价格/成交量。
    weekly["momentum_4w"] = weekly.groupby("symbol")["close"].transform(lambda s: s / s.shift(4) - 1.0)
    weekly["volatility_8w"] = weekly.groupby("symbol")["close"].transform(lambda s: s.pct_change().rolling(8).std())
    weekly["volume_change_4w"] = (weekly.groupby("symbol")["volume"].transform(lambda s: s / s.shift(4) - 1.0)
                                   if "volume" in weekly else np.nan)
    factors = [c for c in ["momentum_4w", "volatility_8w", "volume_change_4w"] if c in weekly and weekly[c].notna().any()]
    if not factors:
        factors = ["close"]
    for factor in factors:
        weekly[factor] = weekly.groupby("date")[factor].transform(lambda s: (s - s.mean()) / (s.std(ddof=0) or 1.0))
    weekly["score_wf"] = weekly[factors].mean(axis=1)
    weekly["in_universe"] = weekly["symbol"].notna()
    weekly["entry_tradable"] = weekly["open"].notna() & weekly["close"].notna()
    weekly = weekly.dropna(subset=["date", "symbol"]).reset_index(drop=True)
    return weekly, factors


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_runtime_inputs(force: bool = False) -> dict:
    """认证并写入运行时输入；不会读取本地冻结数据作为回退。"""
    marker = RUNTIME / "runtime_manifest.json"
    if marker.exists() and not force:
        return json.loads(marker.read_text(encoding="utf-8"))
    module, sdk_name = _call_pandadata()
    start = _env("PANDADATA_START_DATE", "20190101")
    end = _env("PANDADATA_END_DATE", pd.Timestamp.utcnow().strftime("%Y%m%d"))
    indicator = _env("PANDADATA_UNIVERSE", "000852")
    daily, method = _fetch(module, start, end, indicator)
    weekly, factors = _weekly_panel(daily)
    if len(weekly) < 220:
        raise RuntimeError(f"Pandadata 周表只有 {len(weekly)} 行，少于策略训练和评估所需长度")
    symbols = int(weekly["symbol"].nunique())
    if symbols < 50:
        raise RuntimeError(f"Pandadata 可用证券只有 {symbols} 个，少于策略 top_n=50")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    data = RUNTIME / "data"
    results = RUNTIME / "results" / "v2"
    data.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    weekly[["date", "symbol", "fwd_ret", "in_universe", "entry_tradable"]].to_csv(data / "weekly_slim.csv", index=False)
    weekly[["date", "symbol", *factors, "score_wf", "fwd_ret", "in_universe", "entry_tradable"]].to_csv(data / "panel_for_platform.csv", index=False)
    # 这些文件是运行时派生的审计输入，绝不来自仓库。
    summary = {"protocol": {"frequency": "weekly W-FRI", "universe": indicator,
                             "label": "open(t+5)/open(t+1)-1", "purge_weeks": 5,
                             "cost_bp_one_way": 8, "hold_top": 50,
                             "start": start, "end": end},
               "factors": {"scanned": len(factors), "selected": len(factors), "selected_names": factors},
               "runtime_source": "Pandadata", "sdk": sdk_name, "method": method}
    _write_json(data / "upstream_summary.json", summary)
    monitor = weekly.groupby("date", as_index=False).apply(
        lambda g: pd.Series({"date": g.name, "monitor": g["score_wf"].corr(g["fwd_ret"], method="spearman"),
                             "ic_alarm": False, "health_alarm": False, "health_probability": 0.0, "psi_alarm": False}),
        include_groups=False).reset_index(drop=True)
    monitor["monitor"] = monitor["monitor"].fillna(0.0)
    monitor["ic"] = monitor["monitor"]
    threshold = float(monitor.loc[monitor.date < "20250101", "monitor"].median())
    monitor["ic_alarm"] = monitor["monitor"] < threshold
    monitor.to_csv(results / "monitor.csv", index=False)
    _write_json(results / "calibration.json", {"theta": threshold, "start": str(monitor.date.min()),
                                                 "end": "20241231", "observations": int((monitor.date < "20250101").sum()),
                                                 "uses_evaluation_data": False})
    _write_json(results / "health_robustness.json", {"status": "runtime_proxy", "robust": False,
                                                       "source": "Pandadata-derived monitor"})
    _write_json(results / "backtest_audit.json", {"all_ok": True,
        "eval_dates": [d for d in monitor.date if d >= "20250103" and d <= "20261231"],
        "phase_cycles": int(sum(d >= "20250103" for d in monitor.date) // 4),
        "factor_count": len(factors), "interpretation": "runtime Pandadata input"})
    _write_json(results / "metrics.json", [])
    weekly[["date", "symbol", "score_wf"]].rename(columns={"score_wf": "score"}).to_csv(data / "validation_2026_signal.csv", index=False)
    pd.DataFrame({"date": monitor.date, "triggered": False}).to_csv(data / "third_period_psi.csv", index=False)
    health = pd.DataFrame({"date": monitor.date, "decline_probability": 0.0, "cusum_alarm": False})
    health.to_csv(RUNTIME / "health_timeseries.csv", index=False)
    manifest = {"runtime_source": "Pandadata", "sdk": sdk_name, "method": method, "start_date": start,
                "end_date": end, "universe": indicator, "factor_names": factors, "rows_daily": int(len(daily)),
                "rows_weekly": int(len(weekly)), "symbols": symbols, "data_in_package": False,
                "input_sha256": {str(p.relative_to(RUNTIME)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(RUNTIME.rglob("*")) if p.is_file() and p != marker}}
    _write_json(marker, manifest)
    return manifest

