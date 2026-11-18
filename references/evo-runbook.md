# EVO 运行说明

本项目按 EVO 的项目级运行方式组织。使用仓库版本入口，不需要把压缩包拆成很多文件逐个传入。仓库地址是：

```text
https://github.com/quantskills/skill-strategy-lifecycle
```

## 一次完整运行

1. 在 EVO 中新建或选择有权限的项目，关联上面的私有仓库，并固定提交号或标签。
2. 为该运行配置 Pandadata 连接或环境变量 `PANDADATA_USERNAME`、`PANDADATA_PASSWORD`、`PANDADATA_BASE_URL`；仓库本身不存放私有数据、令牌和会话凭据。
3. 从仓库根目录执行：

   ```powershell
   python project/evo_entry.py
   ```

   若 EVO 将 `project/` 设为工作目录，则执行 `python evo_entry.py`。这两个命令调用相同的项目入口。
4. 入口会先从 Pandadata 取数并写入临时 `project/runtime_data/q8/`，再运行 `run_all.py`。流程包括单元测试、状态机重放、收益/机会成本核算、统计和扩展验证、项目侧验收以及中文 HTML 报告生成。
5. 输入缺失、散列不一致、日期重复或未来信息检查失败时，任务必须失败；不能把本地历史结果复制到 EVO 输出目录。

压缩包不包含 CSV、Parquet 或其他行情/因子数据，因此不会触发 50 MB 数据上传问题。运行时输入仅保留在 EVO 的临时目录，并由 `runtime_manifest.json` 记录来源和哈希。

## EVO 回执清单

平台任务完成后，应把以下信息作为同一份运行证据保存：

- 平台运行编号、项目名称、仓库地址、提交号或标签和运行时间；
- EVO 页面显示的成功或失败状态；
- `python project/evo_entry.py` 的完整原始控制台输出；
- 输入文件清单、大小和 SHA256；
- `project/runs/evo/input.json` 与 `project/runs/evo/output.json`；
- `project/results/` 下结果文件清单和 SHA256；
- 失败时的错误信息和返回码。

`output.json` 中的 `status=succeeded`、返回码 0 以及结果文件存在，只能证明项目入口完成；还必须和 EVO 页面上的运行编号及成功状态相互核对，才能形成平台证据。只上传或下载 HTML、CSV 或压缩包，不能单独证明 EVO 已运行。

## 运行后重点核对

至少核对以下文件：

```text
project/results/summary.csv
project/results/state_ledger.csv
project/results/state_transitions.csv
project/results/opportunity_cost.json
project/results/report.html
project/results/acceptance.json
project/runs/evo/input.json
project/runs/evo/output.json
```

其中 `summary.csv` 用于比较持续启用、简单阈值和生命周期规则；`state_ledger.csv` 用于核对降权、冷却、退役和再激活是否只使用当时可见数据；`opportunity_cost.json` 用于核对停用错过的收益和避免的损失；`report.html` 是中文展示；`acceptance.json` 是项目侧验收。平台回执和这些文件必须逐项对应。

当前仓库只记录项目入口和运行要求；没有把未完成的 EVO 回执写成成功结果。获得真实 EVO 回执前，README 中的历史数字只能称为项目侧回顾性结果。
