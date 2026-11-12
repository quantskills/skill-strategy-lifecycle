# 因子衰退预警与动态再平衡

这个项目持续观察因子在日历时间上的有效性。它每天计算截面 IC，跟踪 20、60 和 120 日窗口，并结合 CUSUM 与卡尔曼滤波判断因子是否开始衰退。

输出包括健康度、衰退概率、绿黄红预警和建议权重系数。红灯表示近期数据支持降权检查，不能直接证明因子永久失效。

## 输入格式

合并长表需要这些字段。

```text
date,symbol,factor,value,next_return
```

也可以按因子分别保存文件。每个文件包含 `date`、`symbol` 和 `factor_value`，再提供一张含有 `date`、`symbol` 和 `next_return` 的收益表。详细约定见[数据协议](references/data-contract.md)。

收益必须来自下一交易期。历史回看时，程序只使用当时已经可见的数据。

## 安装

需要 Python 3.11 或更高版本。

```powershell
python -m pip install -r requirements.txt
```

## 生成健康报告

```powershell
python scripts\factor_health.py `
  --panel data\factor_panel.parquet `
  --output outputs\health
```

默认采用连续降权。衰退概率越高，建议权重系数越低。若偏好分档处理，可添加 `--weighting step`，分别对应保持、减半和归零。

## 检查参数稳定性

正式使用自动降权前，建议运行敏感性分析。

```powershell
python scripts\robustness_check.py `
  --panel data\factor_panel.parquet `
  --output outputs\robustness
```

这项检查会改变衰退阈值、滤波速度和变点阈值。`stable_across_parameters` 为 `false` 时，保留看板提示，不自动采用建议权重。

## 从收盘价生成基础因子

```powershell
python scripts\prepare_traditional_factors.py `
  --source data\adjusted_close.parquet `
  --output data\traditional_factors.parquet
```

输入至少包含 `date`、`symbol` 和 `close`。脚本会生成五个常见量价因子，并统一滞后一交易日。

## 输出文件

- `health_summary.csv` 保存当前健康度、衰退概率和建议权重
- `health_timeseries.csv` 保存每日 IC、后验 IC、区间和变点
- `warning_validation.csv` 保存预警日期与阈值跌破日期
- `report.md` 提供适合阅读的健康报告
- `state.json` 便于下一次运行或其他程序接入
- `health_trends.png` 展示各因子的变化过程

验证提前量时，需要使用带有确认失效日期的历史因子。普通因子数据可以生成健康报告，但不能据此宣称预警准确率。真实传统因子测试记录见[验收记录](references/real-validation.md)。

## 开发检查

```powershell
python -m pytest -q
python -m compileall -q scripts tests
```

本项目采用 GPL-3.0 许可证。输出用于量化研究，不构成投资建议。
