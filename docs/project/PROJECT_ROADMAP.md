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

当前状态：M4.1-M4.5 已完成本地实现与门禁，M4.6 待进入。M4.2 已建立 OpenAPI 与稳定语义快照；M4.3-M4.4 已建立集中安全回归和 15 类写能力事务证据；M4.5 已从全新 wheel 的 minimal 隔离环境启动真实 Uvicorn，经已安装 SDK 完成认证写入，并在第二个进程中从同一 SQLite 恢复，同时验证 optional extras、001-006 migration 和 Token 日志边界。M4.2-M4.5 的远程证据待相关提交推送并在 M4.6 纳入最终 CI 后记录。阶段范围与证据见 [`docs/milestones/m4-quality-security.md`](../milestones/m4-quality-security.md)，工程门禁设计见 [`docs/design/security-regression-gates.md`](../design/security-regression-gates.md)，部署边界见 [`docs/deployment/local-alpha.md`](../deployment/local-alpha.md)。M3 接口、运行时与演示已于 2026-07-24 验收结束，退出候选提交 `d2edef7` 的 GitHub Actions [`CI #20`](https://github.com/1471436961/agent-factory/actions/runs/30079667277) 已通过。M2 已于 2026-07-23 封存，M1 已于 2026-07-21 封存，M0 已于 2026-07-18 验收完成。

## 5. 最终展示物

- 可运行的 Agent Factory 服务和 Python SDK。
- Writer 或 Engineer 场景的端到端演示。
- 可追溯的原型、知识、技能与审计记录。
- 可复算的验证实验、数据集和分析报告。
- 架构文档、里程碑报告、学习日志和纠偏记录。
