---
name: strategy-lifecycle-control
description: Use when evaluating a frozen quantitative strategy lifecycle with point-in-time state replay, comparing always-on, threshold, downweight, retirement, cooldown, and reactivation rules, and producing out-of-sample returns, opportunity cost, and an auditable state ledger.
quantSkills:
  schema_version: 2.0.0
  organization: quantskills
  organization_url: https://github.com/quantskills
  repository: skill-strategy-lifecycle
  repository_url: https://github.com/quantskills/skill-strategy-lifecycle
  project_type: skill
  license: GPL-3.0-only
  maintainer: dijia702
  collection: strategy-research
  catalog: {category: "05", subcategory: 05.performance-attribution}
  workflow: {primary_stage: evaluation, workflow_stages: [evaluation]}
  tags: [strategy-lifecycle, downweight, retirement, reactivation, state-machine, out-of-sample]
  requires: []
  summary_zh: 在冻结策略输入上评价降权、冷却、退役与再激活规则，并输出可审计状态账本。
  summary_en: Replays a point-in-time strategy lifecycle controller and compares always-on, threshold, downweight, retirement, and reactivation policies.
  status: active
  validation_level: runnable
  maintainer_type: community
  platforms: [cursor, claude-code, codex, hermes, openclaw]
  interface:
    mode: structured
    envelope: {name: quantskills-envelope, version: 1.0.0}
    inputs: [{profile: frozen-strategy-panel, version_range: ">=1.0.0 <2.0.0", required: true}]
    outputs: [{profile: lifecycle-evaluation-result, version: 1.0.0}]
    adapters: [strategy-lifecycle-evaluation]
---

# 策略生命周期控制

本技能用于在冻结策略净值、订单或信号和成熟风险统计的前提下，重放策略状态机，比较：

- 持续启用；
- 简单阈值；
- 降权与冷却；
- 退役；
- 再激活。

所有状态转换只能使用当时已经成熟的数据。技能不会根据未来收益挑选停用月份，也不会重训上游因子或模型。

## 入口

项目入口为 `evo_entry.py`，主流程为 `run_all.py`。在支持项目级运行任务的平台中，从项目版本执行入口；平台应保存运行编号、输入清单、原始控制台输出和结果文件散列。

本技能与 Q8 的分工是：Q8 负责因子、模型和重训；本技能只读取冻结 Q8 输入，研究策略生命周期规则。

## 必要输入

输入合同见 [references/input-contract.md](references/input-contract.md)。至少需要冻结的逐期策略收益、成熟健康指标、策略标识、时间边界、费用假设和状态机参数。私有行情、账户信息、令牌和平台会话不进入仓库。

## 输出

- `summary.csv`：持续启用、简单阈值和生命周期规则的样本外净收益；
- `state_ledger.csv`：逐期状态、暴露、转换原因和成熟数据摘要；
- `opportunity_cost.json`：停用相对持续启用的机会成本；
- `report.html`：中文研究报告；
- `input.json`、`output.json`：入口和运行回执；
- `acceptance.json`：项目侧验收结果。

## 研究边界

状态账本是可重放的研究证据，不是实盘成交保证。退役消融若不改变交易路径，不得宣称退役具有独立交易增量。没有独立未来确认集时，结果只能作为回顾性研究，不能承诺收益或构成投资建议。

## 相关资料

- [输入合同](references/input-contract.md)
- [平台运行记录要求](references/platform-run.md)
- [来源与数据边界](references/source-boundary.md)


数据来源、输入假设、参数限制和风险边界见 eferences/input-contract.md 与 eferences/source-boundary.md。结果仅供研究和教育使用，不构成投资建议，也不承诺收益。

