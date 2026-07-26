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

当前状态：M5.1-M5.4 已实现，M5.5 执行前生产依赖闭环已完成。`experiments` package 已提供冻结 Writer/Pilot fixture、确定性执行计划、MANUAL/FACTORY 公平渲染、不可变 journal、OpenAI Responses gateway、离线评分分析、可验证报告和受控 live launcher。canonical Pilot Manifest 绑定 source commit `e76adc778300b73b5973920fbaaa72275501db8d`、CPython `3.11.15`、OpenAI SDK `2.46.0`、完整 `agent_factory` 生产代码、153 项输入和 `$0.051815` 硬上限；内部 checksum 为 `58afac123924e0604ec4067f0492781e7115a97b6c14900aee5bcff8fcd05713`。M5.5.5 与 M5.5.7 Manifest 已保留为历史文件，不能用于执行。正式数据集/计划 checksum 为 `e8305386e305e39623ab1e852059148ed319ae63fc180a58288f1ac0a3e14a8e` / `8e8ad93a8cb1b3207580c89917e4af9a6ac0c32c6ab47d83e04c6f04b233e920`；Pilot 为 `4651ae511935d2c9e1312b67fcb568669e4ea993f37059939249e9e83255d9aa` / `ed7a237fd48280e6fcefda742a6336f70cf58170362800e5897ab7db43eb480d`。当前尚未读取真实 API key、调用模型或产生费用。阶段范围见 [`docs/milestones/m5-validation-experiment.md`](../milestones/m5-validation-experiment.md)，执行约束见 [`docs/design/experiment-protocol.md`](../design/experiment-protocol.md)。

执行边界：生产依赖闭环 Manifest 已满足源码、环境声明、计划和预算冻结前置条件。下一步必须由项目 owner 独立确认固定模型 `gpt-4.1-mini-2025-04-14`、完整 8-run Pilot 和最多 `$0.051815` 费用；未获得该批准前不得读取真实 API key 或启动 provider 调用。Pilot 通过评审并冻结正式 Manifest 后，M5.5 才能结束。

## 5. 最终展示物

- 可运行的 Agent Factory 服务和 Python SDK。
- Writer 或 Engineer 场景的端到端演示。
- 可追溯的原型、知识、技能与审计记录。
- 可复算的验证实验、数据集和分析报告。
- 架构文档、里程碑报告、学习日志和纠偏记录。
