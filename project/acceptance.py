"""项目侧验收；不将本地检查冒充目标平台验收或独立确认。"""
import json
from html.parser import HTMLParser

import numpy as np
import pandas as pd

from project_config import ROOT,Q8,RESULTS as OUT,DATA_MODE,validate_inputs,load_q8,sha256


def main():
    checks=[]
    def record(name,passed,evidence):
        checks.append({'item':name,'passed':bool(passed),'evidence':evidence})
    inputs=validate_inputs();record('Pandadata运行输入完整性',inputs['ok'],
        f"来源={inputs.get('runtime_source','外部冻结输入')}；运行时目录已生成且不进入交付包")
    summary=pd.read_csv(OUT/'summary.csv');trades=pd.read_csv(OUT/'trades.csv',dtype={'date':str})
    ledger=pd.read_csv(OUT/'state_ledger.csv');attr=pd.read_csv(OUT/'state_attribution.csv')
    totals=[]
    for (expression,arm),g in trades.groupby(['expression','arm']):
        value=np.mean([np.prod(1+p.sort_values('cycle').net.to_numpy())-1 for _,p in g.groupby('phase')])
        expected=summary[(summary.expression==expression)&(summary.arm==arm)].total.iloc[0]
        totals.append(abs(value-expected)<1e-12)
    record('三组对照及敏感性终值独立重算',all(totals),'逐相位连乘后平均；未调用 Q8 metrics')
    record('逐笔成本恒等式',np.allclose(trades.net,trades.gross-trades.trade_cost-trades.borrow_cost,atol=1e-12),'全部交易行')
    record('机会成本逐期公式',np.allclose(ledger.opportunity_cost,ledger.full_net_counterfactual-ledger.net,atol=1e-12),'账本反事实减实际净收益')
    for e,g in ledger.groupby('expression'):
        record(f'{e}状态归因核对',abs(g.opportunity_cost.sum()-attr[attr.expression==e].signed_opportunity_cost_sum.sum())<1e-12,'状态分解之和等于逐期机会成本之和；不同于终值差')
    mon=pd.read_csv(Q8/'results/v2/monitor.csv',dtype={'date':str},float_precision='round_trip').set_index('date')
    q8=load_q8('q8_acceptance')
    replay,cal,theta=q8.mature_frame(mon.ic,list(mon.index))
    record('成熟监控独立重建',np.allclose(replay.monitor,mon.monitor,equal_nan=True,atol=1e-12),'从存档逐期 IC 重建成熟滚动监控')
    # Mutate the raw IC series after an evaluation cutoff, then rebuild monitors.
    cutoff=mon.index.get_loc(sorted(trades.date.unique())[44])
    edited=mon.ic.copy();edited.iloc[cutoff:]=999.
    altered,_,_=q8.mature_frame(edited,list(mon.index))
    record('未来原始IC扰动不影响过去监控',np.allclose(replay.monitor.iloc[:cutoff],altered.monitor.iloc[:cutoff],equal_nan=True,atol=1e-12),'重建监控后比对；区别于只扰动状态输入')
    tests=json.loads((OUT/'test_results.json').read_text())
    record('统计反例与状态边界测试',tests['ok'] and tests['tests']>=9,str(tests))
    inf=json.loads((OUT/'inference_v2.json').read_text())
    record('六项比较及多重比较',len(inf['records'])==6 and all(0<=r['q_bh']<=1 and r['q_bh']+1e-12>=r['bootstrap_p_two_sided'] for r in inf['records']),'区块长2/4/6；BH及共同最大统计量')
    ext=json.loads((OUT/'extended_validation.json').read_text())
    record('时点、参数、消融与执行压力覆盖',ext['placebo_schedules']==87 and ext['parameter_sets']==27 and ext['ablations']==4 and ext['stress_rows']==36,'扩展计划已全部执行')
    record('扩展计划版本绑定',inf['plan_sha256']==sha256(ROOT/'analysis_plan.json')==ext['analysis_plan_sha256'],'计划不冒充历史预注册')
    record('退役消融诚实归因',not ext['retirement_ablation_changes_exposure'],'移除退役标签未改变交易路径，不宣称独立收益贡献')
    parser=HTMLParser();content=(OUT/'report.html').read_text(encoding='utf-8');parser.feed(content)
    record('中文展示与核心章节',all(x in content for x in ['样本外','机会成本','再激活','置信区间','Q8','EVO']),'HTML已解析，中文为主要展示语言')
    record('研究运行入口与配置交付',all((ROOT/n).is_file() for n in ['requirements.txt','project_config.json','evo_entry.py','analysis_plan.json','pandadata_runtime.py']),'Pandadata运行入口与项目配置齐全')
    result={'ok':all(x['passed'] for x in checks),'checks':checks,'passed':sum(x['passed'] for x in checks),'total':len(checks),
            'scope':'项目侧本地验收，不代表目标环境实际验收或价值验收',
            'pending_external':['未来独立样本确认','目标 EVO 环境验收（如验收方要求）','V 和 D 正式赋值']}
    (OUT/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':result['ok'],'passed':result['passed'],'total':result['total']},ensure_ascii=False))
    if not result['ok']:raise SystemExit(1)


if __name__=='__main__':main()
