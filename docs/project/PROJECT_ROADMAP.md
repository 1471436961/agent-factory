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

当前状态：M5 已于 2026-07-25 经项目 owner 确认进入，M5.1-M5.4 已实现，M5.5 已进入且 M5.5.4 OpenAI 实验 gateway 与离线契约测试已实现。仓库级 `experiments` package 已提供严格模型、冻结 Writer fixture、240 项确定性执行计划、MANUAL/FACTORY 渲染与公平性检查、不可变产物仓库、fake gateway、受 live gate 保护的 OpenAI gateway、有限重试、断点恢复、只读 journal 校验、离线评分、评分 Manifest、task 配对聚合、确定性 bootstrap、命题阈值判定、content-addressed 报告包和一键离线复算命令；M5.5.1-M5.5.4 新增冻结契约、Git/运行环境采集、必需输入清单、独立 8-run Pilot fixture、Pilot/Formal 全身份隔离、确定性预算预检和 Responses API 离线契约测试。正式数据集 checksum 为 `e8305386e305e39623ab1e852059148ed319ae63fc180a58288f1ac0a3e14a8e`，正式计划 checksum 为 `8e8ad93a8cb1b3207580c89917e4af9a6ac0c32c6ab47d83e04c6f04b233e920`；Pilot 数据集/计划 checksum 分别为 `4651ae511935d2c9e1312b67fcb568669e4ea993f37059939249e9e83255d9aa` 和 `ed7a237fd48280e6fcefda742a6336f70cf58170362800e5897ab7db43eb480d`。离线候选已固定 `gpt-4.1-mini-2025-04-14`、OpenAI SDK `2.46.0`、官方价格来源和 `$0.051815` 硬上限，但尚未在 clean commit 上生成正式 freeze manifest，未读取 API key，也未调用真实模型；adapter 通过 mock 测试不等于批准真实费用支出。阶段范围见 [`docs/milestones/m5-validation-experiment.md`](../milestones/m5-validation-experiment.md)，执行约束见 [`docs/design/experiment-protocol.md`](../design/experiment-protocol.md)。M4 已于 2026-07-25 封存，退出候选提交 `4a55d73` 的 GitHub Actions [`CI #25`](https://github.com/1471436961/agent-factory/actions/runs/30148036514) 已通过；该结论只覆盖本地 Alpha 的既定边界，不包含公网生产部署。M3 已于 2026-07-24 封存，M2 已于 2026-07-23 封存，M1 已于 2026-07-21 封存，M0 已于 2026-07-18 验收完成。

## 5. 最终展示物

- 可运行的 Agent Factory 服务和 Python SDK。
- Writer 或 Engineer 场景的端到端演示。
- 可追溯的原型、知识、技能与审计记录。
- 可复算的验证实验、数据集和分析报告。
- 架构文档、里程碑报告、学习日志和纠偏记录。
