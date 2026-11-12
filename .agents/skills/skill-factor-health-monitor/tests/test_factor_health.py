import numpy as np
import pandas as pd
import pytest

from scripts.factor_health import Config, analyze, daily_ic, kalman_random_walk, negative_cusum, recommended_weight, warning_lead_days


def test_kalman_shapes_and_variance():
    mean,var=kalman_random_walk(np.array([.05,.04,.03]),1e-5,4e-4)
    assert len(mean)==3 and np.all(var>0)


def test_cusum_detects_decline():
    values=np.r_[np.full(120,.05),np.linspace(.04,-.03,40)]
    alarms=negative_cusum(values,120,.25,5)
    assert alarms[120:].any()


def test_analysis_outputs_bounded_probability_and_weight():
    dates=pd.date_range("2024-01-01",periods=170); vals=np.r_[np.full(120,.05),np.linspace(.04,-.03,50)]
    ic=pd.DataFrame({"date":dates,"factor":"f1","ic":vals,"n_symbols":100})
    detail,summary=analyze(ic,Config())
    assert 0<=summary.iloc[0].decline_probability<=1
    assert 0<=summary.iloc[0].recommended_weight_multiplier<=1
    assert set(["ic_20","ic_60","ic_120"])<=set(detail)


def test_weight_modes_and_invalid():
    assert recommended_weight(.8,"linear") == pytest.approx(.2)
    assert recommended_weight(.8,"step") == 0
    with pytest.raises(ValueError): recommended_weight(.5,"bad")


def test_daily_ic_requires_cross_section():
    rows=[]
    for d in pd.date_range("2024-01-01",periods=3):
        for i in range(25): rows.append({"date":d,"factor":"f","symbol":str(i),"value":i,"next_return":i})
    assert len(daily_ic(pd.DataFrame(rows)))==3


def test_warning_lead_is_measured_in_trading_days():
    dates=pd.bdate_range("2024-01-01",periods=30)
    detail=pd.DataFrame({"date":dates,"factor":"f","posterior_ic":np.r_[np.full(26,.04),np.linspace(.019,0,4)],"decline_probability":np.r_[np.zeros(10),np.ones(20)*.9],"cusum_alarm":False})
    result=warning_lead_days(detail,baseline_days=10)
    assert result.iloc[0].lead_trading_days>5 and bool(result.iloc[0].passes_5_day_target)
