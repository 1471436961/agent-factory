# Factory Tool Adapter 设计说明

## 1. 目标与边界

M3.4 将五项现有工厂能力映射为可被宿主 Agent Runtime 调用的结构化工具：

- `list_prototypes`
- `clone_agent`
- `bind_knowledge`
- `apply_promotion`
- `query_audit_log`

适配器只负责工具发现、输入校验、权限检查、上下文传播、Application
Service 调用和结果封装。业务规则继续由 `FactoryController` 执行，因此 REST、
Python SDK 和 Factory Tool 不会形成三套业务实现。

M3.4 不实现 MCP Server、模型 function-calling 方言转换、网络监听、Agent 业务
工具执行或 ToolCallRecord 持久化。后两项属于 M3.5 的运行时执行边界。

## 2. 信任边界

模型可见参数与宿主可信上下文必须分离：

```python
class FactoryToolCallContext(FrozenModel):
    request_id: UUID
    correlation_id: UUID
    principal: Principal
    idempotency_key: OptionalIdempotencyKey = None
```

`Principal` 必须由宿主完成认证后注入；适配器不接受 Bearer Token，也不从工具
参数解析 `actor`。`request_id`、`correlation_id` 和 `idempotency_key` 同样不能出现
在模型可见的 `input_schema` 中，防止 Agent 伪造身份或审计上下文。

```text
宿主认证/生成上下文
        |
        v
FactoryToolCallContext（可信） ----+
                                  |
模型生成 arguments（不可信） -----+--> FactoryToolAdapter
                                         |
                                         v
                               AuthorizationPolicy
                                         |
                                         v
                                FactoryController
```

## 3. 工具契约

```python
class FactoryToolDefinition(FrozenModel):
    name: ToolName
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    required_permission: FactoryPermission


class FactoryToolError(FrozenModel):
    code: ErrorCode
    message: str
    details: JsonObject


class FactoryToolResult(FrozenModel):
    request_id: UUID
    correlation_id: UUID
    ok: bool
    output: JsonObject | None = None
    error: FactoryToolError | None = None
```

`FactoryToolResult` 强制满足以下不变量：成功时只能有 `output`，失败时只能有
`error`。工具定义的输入、输出 JSON Schema 均由 Pydantic 模型生成，禁止手写两份
字段定义。

| 工具 | 输入模型 | 输出模型 | 权限 |
| --- | --- | --- | --- |
| `list_prototypes` | `ListPrototypesToolInput` | `Page[AgentPrototype]` | `factory:read` |
| `clone_agent` | `CloneAgentToolInput` | `AgentInstance` | `factory:write` |
| `bind_knowledge` | `BindKnowledgeToolInput` | `AgentInstance` | `factory:write` |
| `apply_promotion` | `ApplyPromotionToolInput` | `AgentInstance` | `factory:write` |
| `query_audit_log` | `QueryAuditLogToolInput` | `Page[AuditEvent]` | `audit:read` |

写工具输入模型继承现有 REST request model，仅补充原本位于 URL path 的资源标识；
原型列表直接继承 application query。审计查询刻意排除 `AuditQuery.actor`，避免工具
Schema 出现任何名为 actor 的模型可见字段，其余筛选约束与 application query 一致。

## 4. 调用流程

```python
class FactoryToolAdapter:
    def definitions(self, principal: Principal) -> tuple[FactoryToolDefinition, ...]: ...

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: FactoryToolCallContext,
    ) -> FactoryToolResult: ...
```

`invoke()` 按固定顺序执行：

1. 在静态注册表中解析工具；未知名称返回 `FACTORY_TOOL_NOT_FOUND`。
2. 使用 `AuthorizationPolicy` 检查可信 `Principal`，且先于详细参数校验。
3. 使用对应 Pydantic input model 校验不可信参数，拒绝额外字段。
4. 将 `correlation_id` 写入 `CorrelationContext`。
5. 构造 application command/query 并调用 `FactoryController`。
6. 使用声明的 output model 再次验证返回值并序列化。
7. 在 `finally` 中使用 `ContextVar` token 恢复进入调用前的关联上下文。

`definitions()` 只返回当前 Principal 有权调用的工具，并按名称排序。工具发现与
真实调用使用同一权限矩阵，但调用时仍必须再次鉴权，不能把“未展示”当作安全措施。

## 5. 命令映射与幂等

写工具将 `context.principal.subject` 映射为 command 的 `actor`。有效幂等键按以下
优先级确定：

1. 使用宿主显式传入的 `context.idempotency_key`；
2. 缺省时生成 `tool:{tool_name}:{request_id}`。

显式键允许 REST、SDK 和 Factory Tool 对同一业务命令做精确重放；缺省键保证宿主
对同一个 `request_id` 重试不会重复生产。读工具不消费幂等键。

命令映射如下：

```text
CloneAgentToolInput    -> CloneAgentCommand
BindKnowledgeToolInput -> BindKnowledgeCommand
ApplyPromotionToolInput -> PromoteAgentCommand
```

`list_prototypes` 和 `query_audit_log` 直接构造现有 query 模型。适配器不得访问
Repository 或自行开启事务。

## 6. 错误与日志

- Pydantic 输入错误返回 `TOOL_INPUT_VALIDATION_FAILED`，`details.errors` 只保留
  `location`、`message`、`type`，不回显原始参数。
- `FactoryError` 保留稳定的 `code`、`message` 与结构化 `details`。
- 未知工具返回 `FACTORY_TOOL_NOT_FOUND`。
- Controller 返回值不满足声明模型时返回 `TOOL_OUTPUT_VALIDATION_FAILED`。
- 其他异常统一返回 `INTERNAL_ERROR`，不暴露异常文本、堆栈或原始参数。
- `asyncio.CancelledError` 不属于 `Exception`，保持取消语义向上传播。

意外异常只记录工具名、请求 ID、关联 ID 和异常类型，不记录 `exc_info`，避免恶意
异常文本或参数进入日志。

## 7. 装配与依赖方向

`Container` 在 composition root 中创建并公开一个 `FactoryToolAdapter`。适配器只依赖：

- `FactoryController`
- `AuthorizationPolicy`
- `CorrelationContext`

它不依赖 FastAPI、SDK、SQLite、Clock 或 IdGenerator。由此可在同一进程内复用现有
业务服务，同时保持接口层可替换。

## 8. 验证矩阵

| 风险 | 自动化证据 |
| --- | --- |
| Schema 漂移 | 五个 definition 的 schema 与 Pydantic 模型精确对应 |
| 身份伪造 | input schema 不含 Principal、actor 和上下文字段 |
| 越权调用 | 四种角色的发现与调用权限矩阵测试 |
| 参数泄漏 | 非法输入与意外异常的脱敏断言 |
| 上下文污染 | 嵌套 correlation 设置后恢复原值 |
| 重复生产 | 默认幂等与显式跨入口重放测试 |
| 接口绕过业务规则 | 真实 SQLite 集成测试通过 Controller 完成五项操作 |
| 打包遗漏 | wheel 内容检查包含 Factory Tool package |

## 9. 已知限制与备选方案

- Alpha 只定义 transport-neutral 工具契约；不同模型供应商的 schema 子集兼容由后续
  provider adapter 解决。
- 当前返回完整领域对象。若未来响应体过大，可增加稳定的 Tool DTO，但不能让某个
  provider 方言反向污染 application contract。
- 本阶段不新增 tool-call 审计表。Controller 已记录业务状态变化；独立的调用尝试、
  latency 和失败记录应在 M3.5 统一建模，避免提前形成两套工具审计语义。
- 可选方案是为每个工具建立独立类。当前五个工具共享相同鉴权、校验、上下文和错误
  流程，静态注册表更少重复；当工具需要独立生命周期或外部资源时再拆分。
