# Strategy Lifecycle Control

[简体中文](README.md) | English

This QuantSkills research skill replays a point-in-time strategy state machine and compares always-on exposure, simple thresholds, downweighting, cooldown, retirement, and reactivation. It records state transitions, exposure, costs, and the opportunity cost of turning the strategy off.

## Run the research

1. Configure an authorized Pandadata connection in EVO, or provide the environment variables described in [the input contract](references/pandadata-input-contract.md).
2. Run `python run_e6.py` from the repository root. The runtime builds the input tables and requires sufficient history before evaluating them.
3. Inspect the state ledger, cost and opportunity reports, and acceptance output under `project/results/`. See the [runbook](references/evo-runbook.md) and [canonical skill instructions](SKILL.md).

The frozen historical comparison showed lower exposure and some drawdown reduction, with lower cumulative net return than continuous exposure. That sample is retrospective. The research does not validate live fills or authorize trading. The incorporated upstream skills retain their own source and license boundaries.

Released under [GPL-3.0-only](LICENSE). QuantSkills release maintainer: [abgyjaguo](https://github.com/abgyjaguo).
