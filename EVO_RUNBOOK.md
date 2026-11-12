# EVO 运行说明

本项目可以从 EVO 运行。安装 `requirements.txt` 所列依赖后执行 `evo_entry.py`，即可生成策略汇总、状态账本、状态转换、机会成本、验证记录和中文 HTML。

EVO 只作为运行环境；研究逻辑、参数和数据口径均保存在本项目中。冻结Q8位置在 `project_config.json` 配置，或用 `E6_Q8_ROOT` 指定；自包含交付包已包含必要输入。输入指纹不匹配时停止。运行完成后可在项目目录查看输出文件。本地不需要启动 EVO 常驻服务。

项目入口输入/输出保存在 `runs/evo/input.json` 与 `output.json`，步骤记录在 `results/run_steps.json`。这些是项目侧记录，不自动等同于目标环境或价值验收。可迁移交付步骤见 `README.md`。

本次真实入口尝试记录见 EVO_PLATFORM_RUN_RECORD.md 与 uns/evo/platform_attempt_20261112.json。该记录明确区分平台观察与本地准备结果。
