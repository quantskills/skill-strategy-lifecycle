"""按冻结扩展计划复核；所有新增分析均为回顾性，禁止据此挑选主规则。"""
from __future__ import annotations

import itertools
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from inference import family_test, paired_inference
from project_config import ROOT, Q8, RESULTS as OUT, load_q8, sha256, validate_inputs
from state_machine import LifecycleParams, replay_lifecycle


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def progress(message):
    print(message, flush=True)


def main():
    validate_inputs()
    plan = json.loads((ROOT / 'analysis_plan.json').read_text(encoding='utf-8'))
    q8 = load_q8('q8_extended')
    cache, _ = q8.load_cache()
    trades = pd.read_csv(OUT / 'trades.csv', dtype={'date': str})
    monitor = pd.read_csv(Q8 / 'results/v2/monitor.csv', dtype={'date': str}, float_precision='round_trip')
    theta = float(json.loads((Q8 / 'results/v2/calibration.json').read_text())['theta'])
    params = LifecycleParams(theta=theta)
    dates = sorted(trades.date.unique())
    raw = monitor.assign(health_alarm=False)
    full_ledger = replay_lifecycle(raw, None, params)
    life = full_ledger[full_ledger.date.isin(dates)].copy()
    exposure = dict(zip(life.date, life.exposure))
    always = dict.fromkeys(dates, 1.0)
    baselines = {e: q8.simulate(cache, dates, e, exposure=always) for e in plan['expressions']}
    original = {e: q8.simulate(cache, dates, e, exposure=exposure) for e in plan['expressions']}

    # Six hypotheses, jointly resampled with shared indices; multiplicity is E6's own.
    records, deltas = [], []
    for expression in plan['expressions']:
        b = trades[(trades.expression == expression) & (trades.arm == 'always_on')]
        for arm in plan['test_arms']:
            a = trades[(trades.expression == expression) & (trades.arm == arm)]
            stats = paired_inference(a, b, draws=plan['bootstrap_draws'], block=plan['primary_block'], seed=plan['seed'])
            records.append({'expression': expression, 'arm': arm, **stats})
            deltas.append(stats['cycle_deltas'])
    matrix = np.asarray(deltas).T
    block_results = []
    for block in plan['bootstrap_blocks']:
        family = family_test(matrix, draws=plan['bootstrap_draws'], block=block, seed=plan['seed'])
        for i, row in enumerate(records):
            block_results.append({'expression': row['expression'], 'arm': row['arm'], 'block_cycles': block,
                                  'p_two_sided': family['p_two_sided'][i], 'q_bh': family['q_bh'][i],
                                  'p_max_positive': family['p_max_positive'][i], 'global_p_positive': family['global_p_positive']})
            if block == plan['primary_block']:
                row.update(q_bh=family['q_bh'][i], p_max_positive=family['p_max_positive'][i])
    pd.DataFrame(block_results).to_csv(OUT / 'block_sensitivity.csv', index=False)
    write_json('inference_v2.json', {'plan_sha256': sha256(ROOT/'analysis_plan.json'), 'hypotheses': 6,
                                   'records': records, 'interpretation': '回顾性、小样本依赖假设；不显著不代表等效或无效。'})
    progress('扩展核验：六项比较、置信区间与共同区块多重比较完成')

    # Keep ring schedule durations, counts and exposure values exactly; no outcome-based selections.
    placebo = []
    weights = np.array([exposure[d] for d in dates])
    for shift in range(1, len(dates)):
        schedule = dict(zip(dates, np.roll(weights, shift)))
        for expression in plan['expressions']:
            frame = q8.simulate(cache, dates, expression, exposure=schedule)
            placebo.append({'expression': expression, 'shift': shift, 'total': q8.metrics(frame)['total'],
                            'avg_exposure': float(frame.exposure.mean())})
        if shift % 20 == 0:
            progress(f'扩展核验：位移时点对照 {shift}/{len(dates)-1}')
    pdf = pd.DataFrame(placebo)
    pdf.to_csv(OUT/'schedule_placebo.csv', index=False)
    placebo_summary = {}
    for expression, group in pdf.groupby('expression'):
        observed = q8.metrics(original[expression])['total']
        placebo_summary[expression] = {'observed_total': observed, 'shift_schedules': len(group),
            'fraction_shift_not_better': float((group.total <= observed).mean()),
            'shift_median_total': float(group.total.median()),
            'shift_total_range': [float(group.total.min()), float(group.total.max())]}
    write_json('placebo_summary.json', {'by_expression': placebo_summary,
        'boundary': '位移使用已实现状态路径，是事后时点诊断；不是可部署控制器，不是严格随机化因果检验。环形连续时长不变，线性起点阶段数可能变化。'})

    # One-factor and grid sensitivity, never pick a winning rule.
    sensitivity, state_sensitivity, ablation = [], [], []
    def simulate_policy(label, p, source=raw, out_rows=sensitivity):
        ledger = replay_lifecycle(source, None, p)
        ev = ledger[ledger.date.isin(dates)]
        schedule = dict(zip(ev.date, ev.exposure))
        for expression in plan['expressions']:
            result = q8.metrics(q8.simulate(cache, dates, expression, exposure=schedule))
            out_rows.append({'variant': label, 'expression': expression, 'total': result['total'],
                'delta_vs_always_on': result['total'] - q8.metrics(baselines[expression])['total'],
                'delta_vs_primary': result['total'] - q8.metrics(original[expression])['total'],
                'avg_exposure': result['avg_exposure'], 'worst_phase_mdd': result['worst_phase_mdd'],
                'exposure_changes_vs_primary': int(np.sum(ev.exposure.to_numpy() != weights))})
        return ev

    for cool, retire, recovery in itertools.product(plan['sensitivity']['cooldown'], plan['sensitivity']['retire_count'], plan['sensitivity']['recovery']):
        simulate_policy(f'cool{cool}_retire{retire}_recover{recovery}', replace(params,
            cooldown_observations=cool, retire_alarm_count=retire, recovery_good_observations=recovery))
    pd.DataFrame(sensitivity).to_csv(OUT/'parameter_sensitivity.csv', index=False)
    variants = {'no_retirement': replace(params, retirement_enabled=False),
                'no_reactivation_ramp': replace(params, reactivation_ramp=False),
                'no_initial_downweight': replace(params, downweight=0.0),
                'immediate_recovery': replace(params, recovery_good_observations=1, reactivation_ramp=False)}
    for label, p in variants.items():
        ev = simulate_policy(label, p, out_rows=ablation)
        if label == 'no_retirement':
            assert np.array_equal(ev.exposure.to_numpy(), weights), '退役消融预期须依据实测核对'
    pd.DataFrame(ablation).to_csv(OUT/'ablation.csv', index=False)
    for expression in plan['expressions']:
        ev = replay_lifecycle(raw[raw.date.isin(dates)], None, params)
        result = q8.metrics(q8.simulate(cache, dates, expression, exposure=dict(zip(ev.date, ev.exposure))))
        state_sensitivity.append({'expression': expression, 'initialization': 'evaluation_start_full',
                                  'total': result['total'], 'avg_exposure': result['avg_exposure'],
                                  'delta_vs_primary': result['total']-q8.metrics(original[expression])['total']})
    pd.DataFrame(state_sensitivity).to_csv(OUT/'initial_state_sensitivity.csv', index=False)
    progress('扩展核验：27组参数、4项消融及起始状态敏感性完成')

    stress = []
    schedules = {'always_on': always, 'simple_threshold': dict(zip(dates, np.where(life.alarm_ic, 0., 1.))), 'lifecycle_v1': exposure}
    original_cost = q8.CFG['one_way_cost']
    try:
        for fill, cost_multiple in itertools.product(plan['missing_returns'], [1., 2.]):
            q8.CFG['one_way_cost'] = original_cost * cost_multiple
            for expression in plan['expressions']:
                group=[]
                for arm, schedule in schedules.items():
                    try:
                        m=q8.metrics(q8.simulate(cache, dates, expression, exposure=schedule, missing_fill=fill))
                        group.append({'expression':expression,'arm':arm,'missing_fill':fill,'cost_multiple':cost_multiple,'total':m['total'], 'status':'ok'})
                    except AssertionError as error:
                        group.append({'expression':expression,'arm':arm,'missing_fill':fill,'cost_multiple':cost_multiple,'total':None,'status':str(error)})
                base=next(x['total'] for x in group if x['arm']=='always_on')
                for row in group:
                    row['delta_vs_always_on']=None if row['total'] is None or base is None else row['total']-base
                stress.extend(group)
    finally:
        q8.CFG['one_way_cost']=original_cost
    pd.DataFrame(stress).to_csv(OUT/'execution_stress.csv',index=False)

    # Secondary signal already exists in Q8. No fit; this is not independent market data.
    all_dates=list(cache)
    ic={}
    for date,c in cache.items():
        valid=c['elig'] & np.isfinite(c['ew']) & np.isfinite(c['ret'])
        ic[date]=pd.Series(c['ew'][valid]).corr(pd.Series(c['ret'][valid]),method='spearman') if valid.sum()>30 else np.nan
    ew_monitor, _, ew_theta=q8.mature_frame(pd.Series(ic),all_dates)
    ew_monitor=ew_monitor.reset_index().assign(health_alarm=False)
    if 'date' not in ew_monitor:ew_monitor=ew_monitor.rename(columns={'index':'date'})
    ew_ledger=replay_lifecycle(ew_monitor,None,LifecycleParams(theta=ew_theta))
    ew_ev=ew_ledger[ew_ledger.date.isin(dates)]
    ew_ev.to_csv(OUT/'secondary_signal_ledger.csv',index=False)
    secondary=[]
    ew_scores={d:cache[d]['ew'] for d in dates}
    ew_schedules={'always_on':always,'simple_threshold':dict(zip(ew_ev.date,np.where(ew_ev.alarm_ic,0.,1.))),
                  'lifecycle_v1':dict(zip(ew_ev.date,ew_ev.exposure))}
    for expression in plan['expressions']:
        reference=q8.metrics(q8.simulate(cache,dates,expression,scores=ew_scores,exposure=always))['total']
        for arm,schedule in ew_schedules.items():
            m=q8.metrics(q8.simulate(cache,dates,expression,scores=ew_scores,exposure=schedule))
            secondary.append({'signal':'q8_frozen_equal_weight','expression':expression,'arm':arm,'theta_2024':ew_theta,
                              'total':m['total'],'delta_vs_always_on':m['total']-reference,'avg_exposure':m['avg_exposure'],
                              'boundary':'同源等权冻结信号，回顾性扩展，不是独立模型或确认集'})
    pd.DataFrame(secondary).to_csv(OUT/'secondary_signal_summary.csv',index=False)
    write_json('extended_validation.json',{'status':'completed','retrospective':True,
        'analysis_plan_sha256':sha256(ROOT/'analysis_plan.json'),'hypotheses':len(records),
        'block_lengths':plan['bootstrap_blocks'],'placebo_schedules':87,'parameter_sets':27,'ablations':4,
        'stress_rows':len(stress),'secondary_signal_rows':len(secondary),
        'retirement_ablation_changes_exposure':False,'independent_confirmation_available':False})
    progress('扩展核验全部完成：未修改原主规则或评估月份')


if __name__=='__main__':main()
