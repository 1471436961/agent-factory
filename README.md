# Agent Factory

Agent Factory 是一个实验性的 AI Agent 生产治理框架。它位于模型和运行时的上游，将 Agent 的定义、原型、知识绑定、工具权限、技能评级和审计记录表示为可验证、可追溯的工程对象。

当前仓库处于 **Alpha / M5 已进入，当前执行 M5.1 实验协议设计**。M1 核心生产链、M2 技能治理、M3 接口与受限运行时，以及 M4 Alpha 安全、回归与发布门禁均已由项目 owner 验收；M4 退出候选提交 `4a55d73` 的 GitHub Actions [`CI #25`](https://github.com/1471436961/agent-factory/actions/runs/30148036514) 已通过。M5 尚未冻结模型、任务集、预算或价格快照，也未执行真实模型调用。M4 仍不包含公网生产部署能力，当前唯一受支持的部署形态继续是单机、单 Uvicorn、文件型 SQLite 和 loopback。

## 核心边界

- 工厂控制器是确定性代码系统，不依赖 LLM 做内部治理决策。
- 工厂输出运行时无关的 `AgentSpec`，不替代 LangGraph、AutoGen 等运行时。
- 知识绑定保证版本和槽位关系可验证，不保证模型一定正确使用知识。
- 默认执行路径不接入真实 LLM：M3.5 已提供离线 `OfflineDemoRuntimeAdapter`、固定只读 `document-search`、受授权和版本约束的 `ToolExecutor`、脱敏调用记录及可选 OpenAI gateway。M3.6 的 Gradio 页面通过 SDK 完成生产与治理，通过离线 Runtime 执行固定 Writer 任务；它只绑定 loopback，不提供公网部署能力。真实模型不进入默认测试；当前 Runtime 没有租约、heartbeat、checkpoint、进程隔离或任意代码执行能力。多 Agent 协作和分布式基础设施不在当前 Alpha 范围。

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

## 契约快照

M4.2 将 OpenAPI、固定 Writer AgentSpec 和审计时间线作为显式评审的 JSON 快照。日常检查不会修改文件：

```bash
uv run python -m scripts.contract_snapshots --check
```

只有确认契约变化并在提交说明中标注 PATCH、MINOR 或 MAJOR 影响后，才执行：

```bash
uv run python -m scripts.contract_snapshots --write
```

## Alpha 安全回归

M4.3 集中验证当前真实攻击面，包括认证与授权拒绝、actor 单一来源、请求体和 Header 边界、响应安全头、敏感内容脱敏，以及固定只读工具的默认离线能力：

```bash
uv run pytest -q tests/security
```

这些测试只证明当前本地 Alpha 的固定边界，不代表已经实现 OAuth/OIDC、公网限流、TLS、租户隔离、网络/文件工具安全或不可信代码沙箱。

## 事务故障回归

M4.4 使用测试专用 UoW 装饰器在真实文件型 SQLite 事务的实体写入后、审计写入后、幂等写入后和 commit 前注入失败，并验证 revision、审计与重放事实不会部分提交：

```bash
uv run pytest -q \
  tests/integration/test_transaction_fault_injection.py \
  tests/integration/test_tool_execution.py \
  tests/integration/test_migrations.py
```

该证据不模拟断电、磁盘损坏或外部工具副作用回滚，也不能外推到 PostgreSQL 或分布式事务。

## 本地制品 Smoke

M4.5 从全新 sdist/wheel 创建 minimal 与 optional extras 两套隔离环境，并从工作区外启动两次真实 Uvicorn 进程：

```powershell
uv --cache-dir E:/Agent-Factory/.tmp/uv-cache run python -m scripts.local_alpha_smoke --work-root E:/Agent-Factory/.tmp/local-alpha-smoke --uv-cache-dir E:/Agent-Factory/.tmp/uv-cache
```

验收包含 wheel 资源、001-006 migration、entry point、SDK 认证写入、进程重启恢复和 Token 日志扫描。唯一受支持的拓扑及阻断项见[本地 Alpha 部署说明](docs/deployment/local-alpha.md)。

## M5 验证实验

M5 将证据拆分为三类：240 次 Writer 生成只检验结构一致性、知识遗漏和读者适应性；单操作者构建时间只作为探索性工程案例；审计完整性由确定性链路验证。正式任务使用合成知识，pilot 与正式数据隔离，真实模型调用必须在配置、模型、价格和成本上限冻结后再次由项目 owner 明确批准。当前只完成协议基线，不存在可报告的实验结果。

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
- [M4 里程碑](docs/milestones/m4-quality-security.md)
- [M5 里程碑](docs/milestones/m5-validation-experiment.md)
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
- [Alpha 安全、回归与发布门禁设计说明](docs/design/security-regression-gates.md)
- [M4.4 事务与并发故障证据](docs/design/transaction-fault-evidence.md)
- [M5 实验协议设计说明](docs/design/experiment-protocol.md)
- [本地 Alpha 部署说明](docs/deployment/local-alpha.md)
- [学习日志](LEARNING_LOG.md)
- [设计纠偏记录](DECISION_CORRECTIONS.md)

## 协作与归属

项目由仓库 owner 负责需求判断、架构取舍、人工评审和最终验收，AI 工具参与资料核对、方案讨论、代码实现与测试辅助。仓库记录用于保留可复现的证据链，不将 AI 辅助工作表述为未经人工评审的独立人工产出。

## 许可证

仓库当前公开可见，但尚未选择开源许可证。除非后续明确添加许可证，否则公开可见不等于获得复制、修改或再分发授权。
