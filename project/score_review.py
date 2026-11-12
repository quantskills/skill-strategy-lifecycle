"""以证据复核四项质量分；V与D只保留待验收，不自动正式赋值。"""
import json
from project_config import RESULTS as OUT


def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))


def main():
    acceptance=read('acceptance.json');portability=read('portability_verification.json')
    tests=read('test_results.json');visual=read('report_visual_qa.json');extended=read('extended_validation.json')
    ready=all([acceptance['ok'],portability['ok'],tests['ok'],visual['ok'],extended['status']=='completed'])
    rows=[
        {'item':'完成','max':30,'before':26,'score':29,'evidence':'三组对照、176行账本、全状态、双冻结信号、机会成本和一键中文报告。',
         'remaining':'第二信号同源，多头/对冲不是独立策略；不填补成跨模型验证。'},
        {'item':'方法','max':35,'before':20,'score':28,'evidence':'零中心配对区块、区间、六项BH与最大统计量、87位移、27参数、四消融、36压力，9项统计/状态测试。',
         'remaining':'22周期小样本、无未见确认集和原始时点预注册证明，行情代理仅7个风险观察。'},
        {'item':'平台','max':25,'before':8,'score':18,'evidence':'配置和依赖已交付；新目录、新依赖环境运行同一入口返回0；输入缺失/篡改拒绝；收益及账本指纹一致。',
         'remaining':'本次仅做项目侧迁移兼容验证，保留目标EVO环境外部验收的7分，不冒充已完成。'},
        {'item':'文档','max':10,'before':8,'score':10,'evidence':'中文报告与HTML同源生成；可下载证据；桌面及手机无页面溢出，筛选88行正确，无脚本错误与失效下载。',
         'remaining':'正式质量分由验收方给出；本表内部细则是审慎自评。'},
    ]
    score=sum(r['score'] for r in rows) if ready else None
    result={'scope':'evidence_based_self_assessment_not_official_acceptance','all_local_evidence_passed':ready,
            'before_quality':62,'quality':score,'target_quality':85,'items':rows,
            'value_official':None,'value_candidate_after_acceptance':0.60,'difficulty':None,'points':None}
    (OUT/'scorecard_v2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# E6 逐项复核与85分目标','',
        f'本轮证据自评：**Q={score if score is not None else "待补证"}/100**，原暂评62分。目标85分；正式验收尚未完成。',
        '', '仅采用用户提供的完成30、方法35、平台25、文档10四项权重；内部扣分为证据自评，不冒充官方评分细则。',
        '', '| 分项 | 满分 | 原暂评 | 本轮自评 | 核验与补强 | 保留扣分 |','|---|---:|---:|---:|---|---|']
    lines += [f"| {r['item']} | {r['max']} | {r['before']} | {r['score'] if ready else '待补证'} | {r['evidence']} | {r['remaining']} |" for r in rows]
    lines += ['', '## 逐项证据清单','', '| 核验项目 | 状态 | 证据 |','|---|---|---|']
    lines += [f"| {r['item']} | {'通过' if r['passed'] else '未通过'} | {r['evidence']} |" for r in acceptance['checks']]
    lines += [f"| 独立依赖环境与迁移 | {'通过' if portability['ok'] else '未通过'} | portability_verification.json；项目入口返回码{portability['entry_returncode']} |",
              f"| 浏览器与交互核对 | {'通过' if visual['ok'] else '未通过'} | report_visual_qa.json，桌面/手机截图 |",
              '', '## 新研究结论与价值', '',
              '1. 修正旧统计错误后，生命周期多头净收益仍为15.14%，持续启用23.65%；当前样本不足以支持默认替换基线。',
              '2. 退役消融没有改变任何暴露日期或收益，说明当前退役主要是治理标签；此前把该状态日期的机会成本归因于退役机制本身并不成立。这直接改变后续规则设计决策。',
              '3. 取消半仓恢复、改变降权或缩短健康恢复会改变交易，但不因回顾性收益更高就选为主规则。',
              '4. 第二冻结信号有不同方向的结果，限制了通用化结论；小样本、缺失收益及行情代理边界保留。',
              '', '## 价值与积分边界','',
              'V正式值仍待验收，建议从0.60档讨论；不因自评达到85而自动提高价值档。D的定义和数值尚未给出，正式积分留空。',
              '只有有效交付、正式Q≥60且验收V≥0.60才满足计分门槛。若验收Q=85、D=1、V=0.60，则四舍五入后为51分；这不是已获得积分。',
              '', '## 运行与文档入口','',
              '- [中文HTML报告](report.html) · [完整报告](report.md)',
              '- [项目侧验收](acceptance.json) · [独立环境迁移记录](portability_verification.json)',
              '- [统计与状态测试](test_results.json) · [浏览器核对](report_visual_qa.json)',
              '- [规则消融](ablation.csv) · [统计区间](inference_v2.json) · [步骤记录](run_steps.json)',
              '', 'EVO仅作为运行环境，本轮只说明项目可以从EVO运行，不涉及其实现。未来独立样本与价值验收仍需外部证据。']
    (OUT/'评分复核_v2.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'quality':score,'evidence_ready':ready},ensure_ascii=False))


if __name__=='__main__':main()
