"""同一事实源生成中文 Markdown 与浅色 HTML；报告可由主流程重复生成。"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

from project_config import RESULTS as OUT

ARM={'always_on':'持续启用','simple_threshold':'简单阈值','lifecycle_v1':'生命周期规则','lifecycle_health_gate':'健康门槛敏感性'}
EXPR={'long':'多头','hedge':'对冲'}
STATE={'FULL':'满仓','DOWNWEIGHT':'降权','COOLDOWN':'冷却','RETIRED':'退役','REACTIVATING':'再激活'}
VARIANT={'no_retirement':'取消退役标签','no_reactivation_ramp':'取消半仓恢复','no_initial_downweight':'首次告警直接零仓','immediate_recovery':'一次健康即直接恢复'}
REASON={'first_alarm':'首次告警','confirmed_or_severe_alarm':'连续或严重告警','persistent_alarm':'持续告警',
 'recovery_gate':'恢复门槛通过','clean_reactivation':'确认恢复','failed_reactivation':'恢复失败',
 'alarm_cleared':'告警解除','minimum_cooldown':'最短冷却期','await_recovery_gate':'等待恢复','hold':'保持','missing_monitor_hold':'监控缺失，禁止恢复'}


def read(name):return pd.read_csv(OUT/name,dtype={'date':str})
def js(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def pct(x):return '—' if pd.isna(x) else f'{float(x)*100:+.2f}%'
def esc(x):return html.escape(str(x))


def display(frame, percentages=(), maps=None):
    result=frame.copy()
    for c in percentages:result[c]=result[c].map(pct)
    for c,mapping in (maps or {}).items():result[c]=result[c].map(mapping)
    return result


def table(headers,rows,attrs=''):
    return '<div class="table-wrap"><table '+attrs+'><thead><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in r)+'</tr>' for r in rows)+'</tbody></table></div>'


def curve(trades):
    series=[]
    for arm,color in zip(['always_on','simple_threshold','lifecycle_v1'],['#2878d4','#b46b10','#16824a']):
        frame=trades[(trades.expression=='long')&(trades.arm==arm)]
        values=np.mean([np.r_[1.,np.cumprod(1+g.sort_values('cycle').net.to_numpy())] for _,g in frame.groupby('phase')],axis=0)
        series.append((ARM[arm],color,values))
    lo=min(float(x.min()) for _,_,x in series)-.02;hi=max(float(x.max()) for _,_,x in series)+.02
    parts=['<svg viewBox="0 0 900 270" role="img" aria-label="多头四相位账户净值均值">']
    for tick in np.linspace(lo,hi,5):
        y=225-(tick-lo)/(hi-lo)*195
        parts.append(f'<line x1="55" x2="860" y1="{y}" y2="{y}" stroke="#e5e9ef"/><text x="7" y="{y+4}" font-size="12" fill="#4b5867">{tick:.2f}</text>')
    for i,(label,color,values) in enumerate(series):
        points=' '.join(f'{55+j/(len(values)-1)*805:.1f},{225-(v-lo)/(hi-lo)*195:.1f}' for j,v in enumerate(values))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/><text x="{65+i*220}" y="18" fill="{color}" font-size="13">{label}</text>')
    return ''.join(parts)+'<text x="55" y="253" font-size="12">起点</text><text x="755" y="253" font-size="12">第22个账户周期</text></svg>'


def main():
    summary=read('summary.csv');trades=read('trades.csv');ledger=read('state_ledger.csv');sections=[]
    def add(title,paragraphs=(),frame=None,columns=None):
        entry={'title':title,'paragraphs':list(paragraphs)}
        if frame is not None:
            entry['headers']=[label for key,label in columns]
            entry['rows']=[[str(row[key]) for key,label in columns] for row in frame.to_dict('records')]
        sections.append(entry)
    def total(expression,arm):return float(summary[(summary.expression==expression)&(summary.arm==arm)].total.iloc[0])
    base=total('long','always_on');primary=total('long','lifecycle_v1');delta=primary-base
    add('1. 做了什么与研究贡献',[
        '复用 Q8 冻结 Ridge 信号、可交易池、前50只选股、2024年校准阈值、标签成熟规则、交易成本与四相位账户。E6只研究完整生命周期，不重训模型，不事后挑选停用月份。',
        '持续启用、简单阈值和新规则三组对照外，新增六项统计比较、87个时点位移对照、27组参数、四项消融、初始状态及执行压力实验。',
        '本轮贡献：从状态归因推进到机制消融，发现退役标签没有独立交易作用；修正旧自助法，避免把不显著解释为无效；将配置、输入指纹、报告与验证纳入一键流程。'])
    add('2. 怎么做：状态与时间边界',[
        '主要路径：满仓（100%）→ 降权（50%）→ 冷却（0）→ 退役（0）→ 再激活（50%）→ 满仓（100%）。告警解除和恢复失败另有分支。',
        '首次告警降权，连续告警进入冷却；冷却观察数为2，持续告警满足3/4窗口时标记退役；连续3次健康后半仓再激活，再观察一次无告警后恢复满仓。',
        '只读决策时已成熟的数据，先固定状态再计收益。评估前回放全历史并承接状态，起点为退役；监控缺失不能累计健康次数或触发恢复。',
        '评估期2025-01-03至2026-09-11，共88个周度决策日。四个重叠相位各22周期，统计抽样以22个共同时间周期为单位，不把88行当独立样本。',
        '扩展计划在已查看历史样本之后固定，留存文件指纹，不冒充历史预注册。新增实验均为回顾性复核，没有独立未见确认集。'])
    sd=display(summary,['total','delta_vs_always_on','avg_exposure','worst_phase_mdd','cost_sum_mean'],{'expression':EXPR,'arm':ARM})
    add('3. 样本外净收益与风险',[
        f'多头持续启用{pct(base)}，生命周期{pct(primary)}，相差{pct(delta)}。净收益是四相位账户终值收益平均，已扣成本，非年化。',
        '平均暴露降至46.59%；最差相位回撤从-26.82%降至-19.65%，改善7.17个百分点。当前样本不支持直接替换基线。'],sd,
        [('expression','表达'),('arm','策略'),('total','净收益'),('delta_vs_always_on','相对持续启用'),('avg_exposure','平均暴露'),('worst_phase_mdd','最差相位回撤'),('cost_sum_mean','成本均值')])
    infer=[]
    for r in js('inference_v2.json')['records']:
        infer.append({'expression':EXPR[r['expression']],'arm':ARM[r['arm']],'effect':pct(r['terminal_delta']),
            'ci':' 至 '.join(pct(x) for x in r['terminal_delta_ci95']),'p':f"{r['bootstrap_p_two_sided']:.4f}",
            'q':f"{r['q_bh']:.4f}",'maxp':f"{r['p_max_positive']:.4f}"})
    add('4. 修正统计检验与置信区间',[
        '旧版直接使用围绕样本均值的重采样分布计算p值，现已停用，旧结果保留审计。新版以零中心配对循环区块分布检验均值差，并从原始配对样本给出95%描述置信区间。',
        '六项假设共同使用相同抽样索引，保留四相位同周期关联；主区块长4，另报2和6周期敏感性。对本项目六项比较执行BH调整及共同区块最大统计量单侧检验。',
        '终值差区间宽且含零，未提供稳定正增益证据。不显著不等于等效、普遍无效或显著更差；小样本区间依赖弱平稳假设，不是预测区间。'],pd.DataFrame(infer),
        [('expression','表达'),('arm','策略'),('effect','终值差'),('ci','终值差95%区间'),('p','均值差双侧p'),('q','BH调整值'),('maxp','正增益最大统计量p')])
    state=display(read('state_attribution.csv'),['mean_exposure','net_sum','signed_opportunity_cost_sum','missed_gain_gross_sum','avoided_loss_gross_sum'],{'expression':EXPR,'state':STATE})
    add('5. 状态账本与停用机会成本',[
        '账本176行，多头和对冲各88行。逐行保留状态、暴露、成熟截止日、告警、转换原因、可见前缀与信号指纹、成本、收益、满仓反事实和机会成本。',
        '机会成本＝持续启用净收益－生命周期净收益。下表是逐期四相位差值累加，不是终值收益差。多头累加38.10%，不能与终值差8.50个百分点混用。',
        '退役占38个决策周，对应多头逐期机会成本累加61.87个百分点；这是该状态日期的归因，不是退役机制本身造成损失的因果估计。'],state,
        [('expression','表达'),('state','状态'),('observations','决策周数'),('mean_exposure','暴露'),('net_sum','逐期净收益和'),('signed_opportunity_cost_sum','逐期机会成本和'),('missed_gain_gross_sum','错过毛收益和'),('avoided_loss_gross_sum','避免毛损失和')])
    trans=display(read('state_transitions.csv'),['exposure'],{'state_before':STATE,'state_after':STATE,'transition_reason':REASON})
    add('6. 实际状态转换与恢复',[
        '评估期实际转换20次，降权、冷却、退役、再激活和恢复满仓各4次；起点承接的退役不计为期内新进入事件。'],trans,
        [('date','日期'),('state_before','转换前'),('state_after','转换后'),('exposure','暴露'),('transition_reason','原因')])
    ep=display(read('episode_attribution.csv'),['net_sum','opportunity_cost_sum'],{'expression':EXPR,'right_censored':{True:'是',False:'否'}})
    ep['states']=ep.states.map(lambda s:' → '.join(STATE[x] for x in s.split('>')))
    add('7. 停用阶段与右删失',['五个非满仓阶段，四个完成，一个在样本尾部未完成。首段包括评估前承接状态。'],ep,
        [('expression','表达'),('episode_id','阶段编号'),('start','开始'),('end_inclusive','结束'),('states','状态路径'),('net_sum','逐期净收益和'),('opportunity_cost_sum','逐期机会成本和'),('right_censored','尾部未完成')])
    ab=display(read('ablation.csv'),['total','delta_vs_primary','avg_exposure'],{'variant':VARIANT,'expression':EXPR})
    add('8. 消融：哪些规则真正改变交易',[
        '取消退役标签后，继续停留在冷却，多头和对冲暴露、成本及收益完全不变。冷却与退役均为零仓位且共享恢复门槛，因此当前退役只有治理标签作用，没有独立交易增量。',
        '取消半仓恢复、首次告警直接零仓或一次健康即恢复会改变实际交易。这些是回顾性机制诊断，不据表中最好结果选优或修改主规则。'],ab,
        [('variant','消融实验'),('expression','表达'),('total','净收益'),('delta_vs_primary','相对主规则'),('avg_exposure','平均暴露'),('exposure_changes_vs_primary','暴露改变日期数')])
    placebo=[]
    for e,r in js('placebo_summary.json')['by_expression'].items():
        placebo.append({'expression':EXPR[e],'count':r['shift_schedules'],'total':pct(r['observed_total']),
            'median':pct(r['shift_median_total']),'rank':pct(r['fraction_shift_not_better'])})
    add('9. 时点、参数与初始状态复核',[
        '穷举87个非零循环位移，保持暴露取值、计数及环形持续时长。低于主规则的位移占比仅是描述排名，不是严格随机化检验p值；位移使用已实现路径，不能部署，线性起点阶段数可能变化。',
        '27组参数覆盖冷却1/2/3、退役计数2/3/4、恢复2/3/4，完整结果全部交付，不挑选赢家。另比较评估起点满仓与全历史承接状态。'],pd.DataFrame(placebo),
        [('expression','表达'),('count','位移数量'),('total','主规则净收益'),('median','位移净收益中位数'),('rank','不优于主规则的位移占比')])
    stress=display(read('execution_stress.csv'),['missing_fill','total','delta_vs_always_on'],{'expression':EXPR,'arm':ARM})
    add('10. 缺失收益与执行压力',[
        '保持信号、阈值与日期不变，比较缺失收益填0、-50%、-100%以及交易成本1倍和2倍，共36项结果。缺失填-100%是压力边界，不能当成真实缺失收益估计。'],stress,
        [('expression','表达'),('arm','策略'),('missing_fill','缺失收益假设'),('cost_multiple','交易成本倍数'),('total','净收益'),('delta_vs_always_on','相对持续启用')])
    secondary=display(read('secondary_signal_summary.csv'),['total','delta_vs_always_on','avg_exposure'],{'expression':EXPR,'arm':ARM})
    add('11. Q8协作与第二冻结信号',[
        'Q8负责因子、模型重训、备选信号与冻结基线；E6负责状态持续性、冷却、退役、恢复、账本和机会成本。双方通过冻结文件指纹衔接。E6没有重训Q8模型。',
        '第二信号复用Q8已有因子等权分数，仅用2024成熟数据校准自身阈值。它是同源回顾性扩展，不是独立模型或独立确认集，不并入主六项检验。多头和对冲也是同一信号的两种表达。',
        '扩展结果有正有负，表明规则效果依赖信号和表达，不能推广为通用收益增益。'],secondary,
        [('expression','表达'),('arm','等权冻结信号策略'),('total','净收益'),('delta_vs_always_on','相对自身持续启用'),('avg_exposure','平均暴露')])
    scenes=pd.DataFrame([
        ('持续启用','趋势延续且信号健康','保留完整暴露，不能推断当前行情'),('简单阈值','需要快速可逆处置','可能反复开关，需核对误报成本'),
        ('降权','早期告警尚未确认','半仓等待确认；场景仍需验证'),('冷却','连续恶化后的短暂停用','须有恢复规则，不保证提高收益'),
        ('退役','持续失效的治理标记','先定义区别于冷却的真实动作'),('再激活','健康恢复后的分步复核','速度与反弹成本需新样本确认')],columns=['state','scene','boundary'])
    add('12. 三项技能与适用行情边界',[
        '因子健康监控：复用Q8冻结健康告警和健康路径稳定性证据。27组健康路径参数扰动不稳健，健康门槛只作敏感性，不据此选优。',
        '市场状态监控：内置行情框架及示例快照，本次没有调用实时行情接口。历史标签来自Q8成熟数据代理，风险扩张7次、趋势扩张32次、震荡混合49次；样本不足以充分验证各状态适用性。',
        '策略诊断：对八组表达和规则运行收益、回撤、IC、换手及分行情诊断，用于人工复核。下表为待验证的使用场景，不冒充数据已经证明的最优行情。'],scenes,
        [('state','策略或状态'),('scene','候选场景'),('boundary','边界')])
    acceptance=js('acceptance.json') if (OUT/'acceptance.json').exists() else None
    portability=js('portability_verification.json') if (OUT/'portability_verification.json').exists() else None
    status=f"项目侧{acceptance['passed']}/{acceptance['total']}项通过" if acceptance else '项目侧验收待执行'
    migration='已在独立依赖环境、迁移路径下完成研究流程复现。' if portability and portability.get('ok') else '迁移复现证据单独记录，不把当前环境运行当作目标环境验收。'
    add('13. EVO运行能力与交付',[
        '项目可以从EVO运行，包含配置文件、依赖清单、冻结输入指纹、一键入口和项目侧输入/输出记录。'+migration,
        'EVO只作为运行环境；本报告只说明运行能力，本地不需要启动EVO常驻服务。',
        status+'。逐步日志、测试结果、输入指纹和产物索引一同交付。'])
    add('14. 验证、决策与后续确认',[
        '核验覆盖冻结基线、成熟监控重建、未来原始IC扰动不影响过去监控、未来信息不变性、成本恒等式、终值独立重算、机会成本归因及统计反例。',
        '仍缺独立未见样本、历史参数预注册证明与真实退出成交核对。小样本、同源信号、健康路径敏感性、行情代理和缺失收益口径都限制外推。',
        '决策贡献：保持持续启用基线，不直接上线新规则；保留降权和分步恢复作为复核接口。若保留退役，先定义不同于冷却的实际动作，再验证独立增量。',
        '计划冻结后至少积累52个新增周度观察并等待标签成熟，沿用同一对照，不根据本轮最好月份或参数改写主结论。V由验收者填写，D未定义，暂不计算正式积分。'])
    downloads=[('完整报告','report.md'),('收益汇总','summary.csv'),('状态账本','state_ledger.csv'),('统计区间','inference_v2.json'),('规则消融','ablation.csv'),
        ('参数敏感性','parameter_sensitivity.csv'),('初始状态敏感性','initial_state_sensitivity.csv'),('时点位移对照','schedule_placebo.csv'),('执行压力','execution_stress.csv'),
        ('第二冻结信号','secondary_signal_summary.csv'),('区块敏感性','block_sensitivity.csv'),('项目侧验收','acceptance.json'),('步骤执行记录','run_steps.json')]
    now=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    title='策略降权、退役与再激活是否优于持续持有'
    lead=f'生命周期多头净收益{pct(primary)}，持续启用{pct(base)}。当前证据不支持默认替换基线；新增消融表明退役标签本身没有改变交易。'
    md=['# E6：'+title,'',lead,''];body=[]
    for i,s in enumerate(sections):
        md+=['## '+s['title'],''];body.append(f'<section id="s{i+1}"><h2>{esc(s["title"])}</h2>')
        for text in s['paragraphs']:md.extend([text,'']);body.append('<p>'+esc(text)+'</p>')
        if 'rows' in s:
            md+=['| '+' | '.join(s['headers'])+' |','|'+'|'.join(['---']*len(s['headers']))+'|']
            md+=['| '+' | '.join(x.replace('|','/') for x in r)+' |' for r in s['rows']];md.append('')
            body.append(table(s['headers'],s['rows']))
        if i==2:body.append('<div class="panel">'+curve(trades)+'<p class="muted">四相位净值均值，按账户周期对齐，仅作研究展示。</p></div>')
        body.append('</section>')
    md+=['## 证据下载','']+[f'- [{name}]({path})' for name,path in downloads]+['',f'生成于{now}（北京时间）。']
    (OUT/'report.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    detail=display(ledger,['exposure','net','opportunity_cost'],{'expression':EXPR,'state_after':STATE,'transition_reason':REASON})
    cols=[('date','日期'),('expression','表达'),('state_after','状态'),('exposure','暴露'),('monitor_cutoff','成熟截止'),('transition_reason','原因'),('net','净收益'),('opportunity_cost','逐期机会成本')]
    rows=[[str(r[c]) for c,label in cols] for r in detail.to_dict('records')]
    nav=''.join(f'<a href="#s{i+1}">{esc(s["title"].split(". ",1)[1])}</a>' for i,s in enumerate(sections))
    links=''.join(f'<a href="{path}" download>{name}</a>' for name,path in downloads)
    css='''body{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#18212b;background:#f7f8fa;line-height:1.8}*{box-sizing:border-box}h1{font-size:32px;line-height:1.4;margin-bottom:8px}h2{margin-top:34px;font-size:23px}p{margin:10px 0 14px}.lead{font-size:19px;background:#e9f2ff;border-left:5px solid #2878d4;padding:16px 20px;border-radius:10px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card,.panel{background:white;border:1px solid #dfe5ec;border-radius:12px;padding:16px}.num{font-size:25px;font-weight:700;margin-top:6px}.table-wrap{overflow:auto;border:1px solid #dfe5ec;border-radius:10px;margin:15px 0}table{width:100%;border-collapse:collapse;background:white;font-variant-numeric:tabular-nums;min-width:700px}th,td{padding:10px;border-bottom:1px solid #e5e9ef;text-align:left;vertical-align:top}th{background:#eef2f6}tr:hover td{background:#f5f9ff}.muted{color:#4b5867;font-size:13px}a{color:#175caa}nav{display:flex;gap:8px 20px;flex-wrap:wrap;font-size:13px;margin:20px 0}svg{width:100%;height:auto}.links{display:flex;flex-wrap:wrap;gap:8px 18px}footer{border-top:1px solid #dfe5ec;margin:30px 0;padding-top:15px;color:#4b5867;font-size:12px}select{padding:7px;border:1px solid #dfe5ec;border-radius:6px}summary{cursor:pointer;color:#175caa}@media(max-width:700px){body{padding:0 16px;margin:24px auto}.cards{grid-template-columns:repeat(2,1fr)}h1{font-size:26px}th,td{font-size:13px;padding:8px}}'''
    script="document.querySelector('#expression').addEventListener('change',function(){document.querySelectorAll('#ledger tbody tr').forEach(r=>{r.hidden=this.value!=='全部'&&r.cells[1].textContent!==this.value})});"
    document=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>E6｜{title}</title><style>{css}</style></head><body><h1>{title}</h1><p class="muted">E6 · Q8协作研究 · 可从EVO运行</p><p class="lead">{esc(lead)}</p>
<div class="cards"><div class="card">生命周期多头净收益<div class="num">{pct(primary)}</div><div class="muted">相对持续启用{pct(delta)}</div></div><div class="card">平均暴露<div class="num">46.59%</div><div class="muted">回撤改善7.17个百分点</div></div><div class="card">扩展复核<div class="num">87 / 27 / 4</div><div class="muted">位移对照 / 参数组 / 消融</div></div><div class="card">退役独立交易变化<div class="num">0 个日期</div><div class="muted">状态标签与实际动作应区分</div></div></div>
<nav>{nav}</nav>{''.join(body)}<h2>15. 状态账本交互核对</h2><details><summary>展开176行账本</summary><label>表达 <select id="expression"><option>全部</option><option>多头</option><option>对冲</option></select></label>{table([label for c,label in cols],rows,'id="ledger"')}</details>
<h2>16. 证据下载</h2><div class="panel links">{links}</div><footer>生成于{now}（北京时间）。历史回顾研究，质量分与价值档位需验收确认。</footer><script>{script}</script></body></html>'''
    (OUT/'report.html').write_text(document,encoding='utf-8');print(OUT/'report.html')


if __name__=='__main__':main()
