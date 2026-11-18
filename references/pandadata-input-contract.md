# Pandadata 运行时输入契约

E6 的交付包不携带行情、因子、冻结 Q8 结果或压缩数据。EVO 进入 `run_e6.py` 后，默认执行以下顺序：

1. 从环境变量读取 Pandadata 连接信息并完成认证；
2. 按 `PANDADATA_START_DATE`、`PANDADATA_END_DATE` 和 `PANDADATA_UNIVERSE` 获取股票日线/因子；
3. 在运行目录生成周频面板、前瞻收益、点时评分和监控文件；
4. 记录来源、日期范围、字段、行数和文件哈希；
5. 再执行 E6 的策略状态机和报告流程。

## 必需字段

Pandadata 返回的数据需要能映射为：

- `date`：交易日期；
- `symbol`：证券代码；
- `open`、`close`：用于周频标签和收益；
- 可选 `high`、`low`、`volume`、`amount`；
- 至少一个可计算的数值因子。没有指定因子时，运行适配器从价格和成交量构造动量、波动率和量能变化。

周频标签按 `open(t+5) / open(t+1) - 1` 构造，成熟期为 5 个周度观察；训练、校准和评估边界来自 `project/strategy/audit_v2.json`。状态转换只读取已经成熟的监控值，不能使用未来收益选择停用月份。

## 认证与环境变量

推荐在 EVO 的数据连接或环境变量中提供：

```text
PANDADATA_USERNAME
PANDADATA_PASSWORD
PANDADATA_BASE_URL
PANDADATA_START_DATE=20190101
PANDADATA_END_DATE=运行日
PANDADATA_UNIVERSE=000852
PANDADATA_FACTORS=可选的 Pandadata 因子名,逗号分隔
E6_DATA_MODE=pandadata
```

若宿主使用直连数据盘，可由 `panda.data.ensure_panda_auth()` 完成门控；若使用网关模式，必须提供用户名、密码和服务地址。认证失败、数据为空、字段不足或历史长度不足时，入口直接失败，不回退到仓库内的旧数据。

## 运行产物

所有取数结果只写入 `project/runtime_data/q8/`，该目录被 `.gitignore` 排除。运行清单 `runtime_manifest.json` 记录 Pandadata SDK、接口、日期范围、因子名、行数与输入哈希；`data_in_package` 必须为 `false`。EVO 输出中的输入回执和结果回执只记录元数据和哈希，不保存行情明细。

## 可比性边界

运行时 Pandadata 数据是本次实验的输入，不得把旧的冻结 Q8 数字冒充为新的 Pandadata 结果。若需复核旧结果，应显式设置 `E6_DATA_MODE=frozen` 并提供受控的外部输入目录；该目录不应上传到本项目仓库或交付压缩包。
