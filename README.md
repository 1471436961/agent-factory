# Agent Factory

Agent Factory 是一个实验性的 AI Agent 生产治理框架。它位于模型和运行时的上游，将 Agent 的定义、原型、知识绑定、工具权限、技能评级和审计记录表示为可验证、可追溯的工程对象。

当前仓库处于 **Alpha / M2**。M1 核心生产链已于 2026-07-21 通过项目 owner 人工验收并封存。M2.1-M2.5 已实现技能/评估契约、治理持久化、确定性评估、显式晋升、观察窗口与自动降级；M2.6 已完成治理 REST 路由、重启恢复主链和本地质量门禁，提交、远程 CI 和项目 owner 阶段验收仍待完成。

## 核心边界

- 工厂控制器是确定性代码系统，不依赖 LLM 做内部治理决策。
- 工厂输出运行时无关的 `AgentSpec`，不替代 LangGraph、AutoGen 等运行时。
- 知识绑定保证版本和槽位关系可验证，不保证模型一定正确使用知识。
- 当前实现不接入真实 LLM，也不运行 Agent 任务；评估输入仍由外部提交。Gradio、Python SDK、Tool adapter、多 Agent 协作和分布式基础设施不在当前已完成范围。

## 本地开发

前置条件：Git、uv，以及由 uv 管理的 Python 3.11。

```bash
uv sync --extra dev --extra test
uv run pytest -q
uv run uvicorn agent_factory.interfaces.api.main:app --reload
```

默认服务地址为 `http://127.0.0.1:8000`，健康检查端点为 `/health/live` 和 `/health/ready`。

## 文档

- [架构设计](docs/architecture.md)
- [项目路线图](docs/project/PROJECT_ROADMAP.md)
- [M0 里程碑](docs/milestones/m0-foundation.md)
- [M1 里程碑](docs/milestones/m1-core-production-chain.md)
- [M2 里程碑](docs/milestones/m2-skill-governance.md)
- [Migration Runner 设计说明](docs/design/migration-runner.md)
- [Domain Contracts 设计说明](docs/design/domain-contracts.md)
- [SQLite Persistence 设计说明](docs/design/sqlite-persistence.md)
- [Application Services 设计说明](docs/design/application-services.md)
- [REST API 设计说明](docs/design/rest-api.md)
- [M2 技能治理设计说明](docs/design/skill-governance.md)
- [学习日志](LEARNING_LOG.md)
- [设计纠偏记录](DECISION_CORRECTIONS.md)

## 协作与归属

项目由仓库 owner 负责需求判断、架构取舍、人工评审和最终验收，AI 工具参与资料核对、方案讨论、代码实现与测试辅助。仓库记录用于保留可复现的证据链，不将 AI 辅助工作表述为未经人工评审的独立人工产出。

## 许可证

仓库当前公开可见，但尚未选择开源许可证。除非后续明确添加许可证，否则公开可见不等于获得复制、修改或再分发授权。
