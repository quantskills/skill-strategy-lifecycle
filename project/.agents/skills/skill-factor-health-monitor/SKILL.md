---
name: factor-health-monitor
description: Monitor calendar-time factor effectiveness with rolling IC/IR, negative CUSUM change detection, Kalman posterior IC, decay probabilities, and weight-reduction suggestions. Use when the user asks whether a factor is still working now; use factor-decay instead for prediction-horizon decay.
---

# 因子衰退预警与动态再平衡

回答“这个因子现在还行不行”。它跟踪日历时间上的 IC 变化，不替代 `factor-decay` 对不同持有期信号寿命的分析。

## 工作流程

1. 读取当日因子值和下一交易期收益，检查日期、股票数和缺失值。
2. 计算每日截面 Spearman IC，以及 20/60/120 日滚动 IC 与 IR。
3. 用训练基准段确定历史 IC 水平；CUSUM 只从基准段之后开始报警。
4. 用随机游走状态空间模型递推估计真实 IC 及 95% 区间。
5. 计算“真实 IC 比历史基准低至少 0.02”的后验概率。
6. 输出绿/黄/红预警、0–100 健康度和线性或阶梯降权系数。
7. 回测预警时，只用当时可见的数据；记录首次预警到 IC 跌破阈值的提前量。
8. 上线前扰动衰退阈值、滤波速度和 CUSUM 阈值；红灯比例随参数大幅变化时停止自动降权。

```powershell
python scripts\factor_health.py --panel data\factor_panel.parquet --output outputs\health
python scripts\robustness_check.py --panel data\factor_panel.parquet --output outputs\robustness
```

也可读取 `skill-factor-evaluate` 风格的单因子目录：

```powershell
python scripts\factor_health.py --panel data\factors --returns data\next_returns.parquet
```

输入格式见 [数据协议](references/data-contract.md)，统计定义和阈值解释见 [方法说明](references/methodology.md)，实际传统因子结果见 [真实验收](references/real-validation.md)。

## 解释边界

- 红灯表示当前数据更支持“衰退”假设，不等于永久失效。
- 权重系数是研究建议，不直接修改生产组合。
- “提前 5 天”必须在未参与调参的历史失效案例上验证；没有案例数据时只提供可运行的验收程序。
- 未提供人工确认的“已衰退因子”标签时，不得宣称真实预警准确率或提前量达标。
- 数据修订、股票池改变、行业中性化变化也可能造成 IC 变点，应在降权前排查。
