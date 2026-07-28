# Agent Factory 项目路线图

## 1. 项目目标

本项目验证一个具体假设：Agent 的定义、复制、知识绑定、权限和能力评级能否被抽象为运行时无关、可重复、可审计的生产流程。

最终成果必须同时满足：代码可运行、实验可复算、设计可解释、变更可追溯。架构细节以 [`docs/architecture.md`](../architecture.md) 为准。

## 2. 里程碑

| 里程碑 | 核心交付物 | 退出证据 |
| --- | --- | --- |
| M0 | 工程骨架、配置、迁移、基础端口、CI | 应用启动；空库迁移；质量检查通过 |
| M1 | 原型、知识、实例、AgentSpec、仓储与核心 REST | 注册、克隆、绑定、导出及重启重放测试 |
| M2 | 技能 DAG、评估、晋升、降级与最小治理 REST | 决策、并发、回退及 HTTP 重启恢复测试 |
| M3 | 身份边界、生命周期、SDK、Tool adapter、受限 Runtime 与 Demo | 三种入口精确重放同一结果；固定 Demo 完整跑通 |
| M4 | Alpha 安全、回归与发布门禁 | 当前安全边界、OpenAPI/语义快照、事务故障和隔离制品检查通过 |
| M5 | 对照实验与分析 | 原始数据、执行配置和统计结果可复算 |
| M6 | 开源准备 | 新环境按文档在 30 分钟内跑通 Demo |

## 3. 项目级验收原则

- 代码行为必须能由自动化测试或可重复命令证明。
- 设计文档、阶段文档和实现不得互相矛盾。
- 默认测试不访问互联网，不调用真实模型。
- 重要判断必须记录依据、替代方案和已知边界。
- AI 辅助产出必须经过项目 owner 评审，不能替代实验数据或人工结论。

## 4. 当前状态

当前状态：M5.1-M5.5 已完成，M5.6 正式实验尚未启动。M5.5 已完成 Moonshot 迁移、生产依赖冻结、三次经批准的真实 8-run Pilot、35 项外部证据 seal、正式 24-task / 5-repetition / 120:120 preflight、完整 launcher、条件分离盲评包和 clean-commit Manifest。第三次 Pilot 的 FACTORY 4/4 成功、MANUAL 3/4 成功及 1 个 `MOONSHOT_OUTPUT_NOT_JSON_OBJECT` 只通过 FACTORY 兼容性门禁，不形成 H1-H5 结论。正式数据集/计划 checksum 仍为 `e8305386e305e39623ab1e852059148ed319ae63fc180a58288f1ac0a3e14a8e` / `8e8ad93a8cb1b3207580c89917e4af9a6ac0c32c6ab47d83e04c6f04b233e920`。阶段范围见 [`docs/milestones/m5-validation-experiment.md`](../milestones/m5-validation-experiment.md)，执行约束见 [`docs/design/experiment-protocol.md`](../design/experiment-protocol.md)，Pilot 复核见 [`M5.5 Moonshot Pilot 执行与纠偏报告`](../reports/m5.5-moonshot-pilot-review.md)，正式冻结边界见 [`M5.5 正式冻结审批记录`](../reports/m5.5-formal-freeze-approval.md)。

执行边界：项目 owner 已批准正式 Manifest `211275d9312207fef02a8f15ee3f3a86bfe6f31c52337361b9f2666260fb7e1f`、240 次预期请求、480 次最大 attempt、`CNY 12875520 micros` 保守估算与 `CNY 25751040 micros` 绝对硬上限，M5.5 因此结束。由于 `kimi-k2.6` 不是不可变 snapshot，正式报告必须把 provider 别名漂移列为复现限制。本次审批明确不启动 M5.6；live 执行仍需单独授权，并必须 checkout Manifest 的 source commit `f0c75655bd3f8ccd1ce4e662e687fe0d50edc026` 通过环境级验证。

## 5. 最终展示物

- 可运行的 Agent Factory 服务和 Python SDK。
- Writer 或 Engineer 场景的端到端演示。
- 可追溯的原型、知识、技能与审计记录。
- 可复算的验证实验、数据集和分析报告。
- 架构文档、里程碑报告、学习日志和纠偏记录。
