# Agent Factory

Agent Factory 是一个实验性的 AI Agent 生产治理框架。它位于模型和运行时的上游，将 Agent 的定义、原型、知识绑定、工具权限、技能评级和审计记录表示为可验证、可追溯的工程对象。

当前仓库处于 **Alpha / M3 进行中**。M1 核心生产链与 M2 技能治理已经项目 owner 验收并封存；M3.1-M3.6 已完成本地提交；M3 工作包均待推送与远程 CI。

## 核心边界

- 工厂控制器是确定性代码系统，不依赖 LLM 做内部治理决策。
- 工厂输出运行时无关的 `AgentSpec`，不替代 LangGraph、AutoGen 等运行时。
- 知识绑定保证版本和槽位关系可验证，不保证模型一定正确使用知识。
- 默认执行路径不接入真实 LLM：M3.5 已提供离线 `OfflineDemoRuntimeAdapter`、固定只读 `document-search`、受授权和版本约束的 `ToolExecutor`、脱敏调用记录及可选 OpenAI gateway。M3.6 的 Gradio 页面通过 SDK 完成生产与治理，通过离线 Runtime 执行固定 Writer 任务；它只绑定 loopback，不提供公网部署能力。真实模型不进入默认测试；当前 Runtime 没有租约、heartbeat、checkpoint、进程隔离或任意代码执行能力。多 Agent 协作和分布式基础设施不在 M3 范围。

## 本地开发

前置条件：Git、uv，以及由 uv 管理的 Python 3.11。

```bash
uv sync --extra dev --extra test
uv run pytest -q
uv run uvicorn agent_factory.interfaces.api.main:app --reload
```

仅在人工模型演示时安装官方 SDK：

```bash
uv sync --extra dev --extra test --extra llm
```

固定 Gradio 演示使用 `demo` extra，并要求 API 与 Demo 读取同一份 `.env` 和空文件型 SQLite：

```bash
uv sync --extra dev --extra test --extra demo

# Terminal A
uv run uvicorn agent_factory.interfaces.api.main:app

# Terminal B
uv run --extra demo agent-factory-demo
```

页面默认地址为 `http://127.0.0.1:7860`。演示使用固定对象 ID，不提供自动覆盖或清库按钮；重复演示应显式选择新的空数据库文件。

启动前在 `.env` 中配置本地 Alpha 身份：

```dotenv
AGENT_FACTORY_AUTH_TOKEN=replace-with-at-least-32-random-characters
AGENT_FACTORY_AUTH_SUBJECT=local-owner
AGENT_FACTORY_AUTH_ROLES=["admin"]
```

默认服务地址为 `http://127.0.0.1:8000`，健康检查端点为 `/health/live` 和 `/health/ready`。未配置 Token 时，live 保持 200，但 ready 和业务路由按 fail-closed 规则返回 503。

## Python SDK

SDK 复用 REST DTO，通过异步 HTTP 调用服务，不直接访问 Controller 或数据库：

```python
from agent_factory.sdk import AgentFactoryClient


async with AgentFactoryClient(
    base_url="http://127.0.0.1:8000",
    token="replace-with-at-least-32-random-characters",
) as client:
    prototypes = await client.list_prototypes(page=1, page_size=20)
```

写操作可显式传入 `idempotency_key` 和 `correlation_id`。SDK 不自动重试；网络结果不确定时，应使用同一幂等键由调用方决定是否重试。

## 文档

- [架构设计](docs/architecture.md)
- [项目路线图](docs/project/PROJECT_ROADMAP.md)
- [M0 里程碑](docs/milestones/m0-foundation.md)
- [M1 里程碑](docs/milestones/m1-core-production-chain.md)
- [M2 里程碑](docs/milestones/m2-skill-governance.md)
- [M3 里程碑](docs/milestones/m3-interfaces-runtime-demo.md)
- [Migration Runner 设计说明](docs/design/migration-runner.md)
- [Domain Contracts 设计说明](docs/design/domain-contracts.md)
- [SQLite Persistence 设计说明](docs/design/sqlite-persistence.md)
- [Application Services 设计说明](docs/design/application-services.md)
- [REST API 设计说明](docs/design/rest-api.md)
- [Authentication 设计说明](docs/design/authentication.md)
- [M2 技能治理设计说明](docs/design/skill-governance.md)
- [生命周期与 Runtime 契约设计说明](docs/design/lifecycle-runtime-contracts.md)
- [Python SDK 设计说明](docs/design/python-sdk.md)
- [Factory Tool Adapter 设计说明](docs/design/factory-tool-adapter.md)
- [Runtime 与安全工具执行设计说明](docs/design/runtime-tool-execution.md)
- [Gradio 演示设计说明](docs/design/gradio-demo.md)
- [学习日志](LEARNING_LOG.md)
- [设计纠偏记录](DECISION_CORRECTIONS.md)

## 协作与归属

项目由仓库 owner 负责需求判断、架构取舍、人工评审和最终验收，AI 工具参与资料核对、方案讨论、代码实现与测试辅助。仓库记录用于保留可复现的证据链，不将 AI 辅助工作表述为未经人工评审的独立人工产出。

## 许可证

仓库当前公开可见，但尚未选择开源许可证。除非后续明确添加许可证，否则公开可见不等于获得复制、修改或再分发授权。
