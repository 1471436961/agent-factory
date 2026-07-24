# Gradio 演示设计说明

## 1. 目标与边界

M3.6 用固定 Writer 场景证明现有生产、运行、评估和治理能力能够被一个可视化客户端串成完整闭环：

```text
Gradio UI
    |
    v
DemoWorkflow ------> AgentFactoryClient --HTTP--> FastAPI / Controller / SQLite
    |
    +-------------> RuntimeAdapter -------------> ToolExecutor / SQLite
```

Gradio 不是新的业务入口。生产与治理操作必须经过公开 Python SDK，任务执行必须经过 `RuntimeAdapter`。`agent_factory.interfaces.demo` 不得直接导入 domain、`FactoryController`、Repository、SQLite 或 Container。最外层 `agent_factory.demo` 只负责进程装配，不实现业务规则。

本工作包不新增 REST operation、数据库 migration、Runtime HTTP endpoint、数据库清理按钮或真实模型默认路径。

## 2. 固定演示数据

| 对象 | 固定值 |
| --- | --- |
| Prototype | `technical-writer@1.0.0` |
| Knowledge | `agent-factory-docs@1.0.0` |
| Knowledge slot | `product-docs` |
| SkillTree | `writer-skills@1.0.0` |
| EvaluationSuite | `mid-writer-suite@1.0.0` |
| Promotion node | `mid-writer` |
| Runtime | `demo-runtime` |
| Tool | `document-search@1.0.0` |

固定知识正文只存在于 fixture 与 Runtime 输入中。页面显示其版本和 checksum，不显示完整正文。评估套件包含 required terms、JSON Schema 与 tool-called 硬规则，并设置 `require_manual_review=true`。

## 3. 三步工作流

```text
NEW
 |
 | initialize_factory
 v
READY_TO_RUN (instance revision 3, RUNNING)
 |
 | run_and_evaluate
 v
AWAITING_REVIEW (instance revision 4, WAITING)
 |
 | approve_and_promote
 v
PROMOTED (instance revision 5, active node = mid-writer)
```

### 3.1 初始化工厂

固定顺序如下：

1. 检查 API readiness。
2. 注册 EvaluationSuite。
3. 使用真实 suite checksum 注册 SkillTree。
4. 注册 Draft Prototype，再显式发布。
5. 注册 DomainKnowledge。
6. 克隆实例，断言 revision 1 与 `CREATED`。
7. 在绑定前尝试导出 Spec，只接受 `MISSING_KNOWLEDGE_BINDING`；其他结果均视为失败。
8. 绑定 `product-docs`，断言 revision 2。
9. 导出 revision 2 Spec 并校验 Prototype、Knowledge 与 SkillTree 来源。
10. 迁移到 `RUNNING`，断言 revision 3。
11. 导出并保存 revision 3 Spec 快照。

### 3.2 运行并评估

1. 由固定 fixture 与已注册知识响应构造 `ResolvedRuntimeKnowledge`。
2. 将 revision 3 Spec、固定 task ID 和固定输入交给 `RuntimeAdapter`。
3. 只接受 `RunResult.status=completed`；失败时显示稳定 `error_code`。
4. 将实例迁移到 `WAITING`，断言 revision 4。
5. 导出 revision 4 Spec。
6. 把实际 RunResult 正文、结构化输出和已调用工具作为 `SubmittedCaseResult` 提交。
7. 只接受 `REVIEW_REQUIRED`，保存 report ID。

Runtime 输出来自 revision 3，报告绑定 revision 4。revision 4 仅由任务结束后的生命周期迁移产生，Agent 配置、知识、工具和 active nodes 未改变；评估前仍重新导出 revision 4 Spec，让报告 checksum 与当前治理快照一致。

### 3.3 批准并晋升

1. 用户显式点击后提交 `APPROVED` review。
2. 使用当前 revision、report ID 与 review ID 晋升 `mid-writer`。
3. 断言 revision 5、状态仍为 `WAITING`，且 active nodes 精确包含 `mid-writer`。
4. 查询审计并生成脱敏时间线。

评估通过不自动修改实例。独立复核与显式晋升用于保留人工决策权，并使报告、复核、晋升成为三个可审计事实。

## 4. 会话状态与失败恢复

`DemoSession` 是不可变、可 deepcopy 的 Pydantic DTO，至少保存：

- `workflow_id`、当前 phase 与已完成 operation 集合；
- instance ID、当前 revision、report/review ID；
- Spec 和 Runtime 结果的 JSON 快照；
- Prototype、Knowledge、SkillTree、Suite 的版本与 checksum。

每个写操作使用 `workflow_id + operation name` 派生的稳定 idempotency key；task ID 使用 UUID5 派生。每完成一个子操作立即更新 session。工作流捕获异常后连同最新 session 返回，因此重试会跳过已完成操作，不会重复克隆、绑定、执行 Runtime 或晋升。

`gr.State` 只保存当前浏览器会话的控制状态。刷新页面后 UI 状态可以丢失，SQLite 中的实例、Spec、工具记录和审计仍是事实来源。M3.6 不实现跨刷新恢复，也不通过猜测数据库状态重建未完成会话。

固定 ID 要求使用空数据库。对象已存在但不属于当前 workflow 时返回稳定冲突，不提供自动覆盖或删除。

## 5. 错误与脱敏

页面错误 DTO 只包含：

```python
class DemoErrorView(FrozenModel):
    code: str
    message: str
    correlation_id: UUID | None = None
```

映射规则：

| 来源 | 页面 code | 页面内容 |
| --- | --- | --- |
| `AgentFactoryApiError` | 原业务 code | 稳定 message 与 correlation ID |
| SDK transport/protocol error | 稳定 SDK code | 通用 message 与 correlation ID |
| Runtime failed result | Runtime `error_code` | 通用执行失败 message |
| 非法 UI 阶段 | `DEMO_INVALID_PHASE` | 当前允许的下一步 |
| 未知异常 | `DEMO_INTERNAL_ERROR` | 通用 message；日志只记录异常类型 |

页面和日志不得包含 Bearer Token、system prompt、完整知识正文、原始 SDK response、异常文本或 traceback。RunResult 仅显示身份字段、状态、Runtime 名称、tool call 数量及去除 `Verified knowledge` 正文后的摘要。审计时间线只显示时间、事件类型、实体类型、实体 ID、revision 与 correlation ID，不显示 payload。

## 6. 进程与配置

API 与 Gradio 作为两个本地进程运行，共享同一文件型 SQLite 配置：

```text
Terminal A: uv run uvicorn agent_factory.interfaces.api.main:app
Terminal B: uv run --extra demo agent-factory-demo
```

Demo launcher 从既有 `Settings` 读取数据库和静态 Bearer Token，并从 `DemoSettings` 读取 API base URL、端口与 timeout。启动时必须满足：

- Bearer Token 已配置；
- API base URL 指向 loopback host；
- Gradio 固定绑定 `127.0.0.1`；
- `share=false`、`show_error=false`、`enable_monitoring=false`、`strict_cors=true`；
- composition root 完成 migration 后才创建页面。

共享 SQLite 只支持当前串行本地演示，不构成多进程任务调度或公网部署能力。Gradio optional extra 未安装时，导入核心 API、SDK 和 Runtime 不得失败；只有调用 Demo launcher 时才给出稳定的缺失依赖提示。

## 7. 页面信息架构

页面是工作台而非产品落地页，包含以下全宽区域：

1. 紧凑标题与当前 phase/revision 状态栏。
2. 三个顺序动作按钮，只有当前合法动作可用。
3. Prototype、Knowledge、SkillTree、Suite 来源表。
4. 脱敏 RunResult 摘要。
5. 审计时间线。
6. 仅在失败时可见的稳定错误区。

页面不提供任意 Prompt、知识、工具或模型参数输入，防止固定验收场景退化为另一个未治理的 Agent playground。

## 8. 验证矩阵

| 风险 | 自动化或人工证据 |
| --- | --- |
| UI 越过 SDK/Runtime 边界 | AST import-boundary test |
| 固定 fixture 漂移 | request DTO validation 与 checksum test |
| 中途失败导致重复副作用 | partial-session retry integration test |
| 非法步骤顺序 | phase transition unit test |
| 错误或敏感内容泄漏 | SDK/Runtime/unexpected error redaction tests |
| 演示只在内存中成功 | 文件 SQLite + FastAPI lifespan + SDK + Runtime integration test |
| revision 或来源不一致 | 每一步精确 revision/checksum assertion |
| 人工复核被绕过 | report 必须 `REVIEW_REQUIRED`，无 review 晋升失败 |
| Gradio optional extra 侵入核心 | core import test 与 demo-extra smoke test |
| 发布制品遗漏 Demo | wheel 内容检查与安装后 import smoke test |
| 布局溢出或遮挡 | 桌面和移动 viewport 截图检查 |

## 9. 已知限制

- UI session 刷新后不恢复；数据库事实不受影响。
- 不支持多个用户同时操作固定 ID 演示。
- 不持久化 RunResult；页面关闭后只能从工具记录与审计证明执行发生过。
- Offline Writer 输出用于验证工程契约，不用于证明内容质量。
- 静态 Bearer Token、单机 SQLite 和 Gradio 本地服务均不是公网生产部署方案。
