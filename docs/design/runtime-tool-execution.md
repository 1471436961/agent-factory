# Runtime 与安全工具执行设计说明

## 1. 目标与范围

M3.5 在现有 `AgentSpec`、生命周期和 Runtime 契约之上实现最小可执行闭环：

```text
RunRequest
    |
    v
RuntimeAdapter ----> ToolExecutor ----> 固定 ToolRegistry
    |                     |                     |
    |                     |                     v
    |                     |              document-search
    |                     v
    |              ToolCallRecord + AuditEvent
    v
RunResult
```

本阶段只允许固定、只读、无文件和无网络副作用的 `document-search@1.0.0`。
不实现动态插件加载、shell、代码执行、任意文件访问、任意 HTTP 请求或 Docker
沙箱。离线 Runtime 是默认实现；模型 Runtime 与官方 SDK gateway 保持 optional，
不进入默认联网路径。

## 2. 元数据目录与执行注册表

生产层现有 `ToolCatalog` 只暴露 `ResolvedToolSpec`，用于原型注册和 AgentSpec 导出；
它不能持有 handler。M3.5 新增 `ToolRegistry`，只供 Runtime 解析可执行 handler：

```python
class ToolDefinition(ResolvedToolSpec):
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    definition: ToolDefinition
    input_model: type[FrozenModel]
    output_model: type[FrozenModel]
    handler: ToolHandler


class ToolRegistry(Protocol):
    def get(self, name: str, version: str) -> RegisteredTool | None: ...
```

默认 Registry 在构造后不可动态注册。`ToolCatalog` 与 `ToolRegistry` 从同一
`ToolDefinition` 派生元数据；`RegisteredTool` 构造时验证 definition 中的 JSON
Schema 与 Pydantic input/output model 生成的 Schema 完全一致，防止生产授权和执行
校验使用两套定义。

不创建 `tool_definitions` 数据表。handler 是进程内代码资源，数据库不能恢复它；
持久化 definition 会形成代码与数据库双重真相源。

## 3. 调用契约

```python
class ToolCallRequest(FrozenModel):
    call_id: UUID
    task_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    tool_name: Slug
    tool_version: SemVer
    arguments: JsonObject


class ToolExecutionContext(FrozenModel):
    spec: AgentSpec
    knowledge: tuple[ResolvedRuntimeKnowledge, ...]
    actor: Actor
    correlation_id: UUID
```

`tool_version` 与 `agent_spec_checksum` 是旧架构草案缺失的身份字段。没有它们，
Executor 只能按名称查工具，无法证明执行的是 AgentSpec 授权的具体版本，也无法把
调用绑定到不可变 Spec 快照。

`ToolExecutionContext` 强制 knowledge 引用与 AgentSpec 精确一致。handler 只能看到
已验证模型和该上下文，不能读取 Repository、全局凭据或任意宿主状态。

## 4. ToolExecutor 验证顺序

`ToolExecutor.execute()` 使用以下固定顺序：

1. 从持久层读取 `AgentSpec`，确认传入 Spec 是已导出的真实快照。
2. 读取实例 head，确认 revision 仍为当前 revision 且状态为 `RUNNING`。
3. 核对 request 的 instance、revision 和 Spec checksum。
4. 在 Spec 工具列表中按名称查授权；未授权返回 `TOOL_NOT_GRANTED`。
5. 核对 request version、Registry version、permission tags 和完整元数据。
6. 用 RegisteredTool input model 净化 arguments，额外字段一律拒绝。
7. 在 `asyncio.timeout()` 中调用固定 async handler。
8. 用 output model 验证 handler 返回值。
9. 生成只含哈希和状态的 `ToolCallRecord`，与审计事件在同一短事务提交。

输入校验、授权、版本和元数据拒绝记为 `rejected`；handler 领域错误和未知异常记为
`failed`；超时记为 `timed-out`；通过输出校验后记为 `succeeded`。外部任务取消保持
`CancelledError` 向上传播；Alpha 不承诺取消过程中一定能写入终态记录。

## 5. 调用记录与事务

```python
class ToolCallRecord(FrozenModel):
    call_id: UUID
    task_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    tool_name: Slug
    tool_version: SemVer
    status: ToolCallStatus
    arguments_hash: Sha256
    result_hash: Sha256 | None
    error_code: ErrorCode | None
    duration_ms: int
    actor: Actor
    correlation_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
```

状态不变量：

- `succeeded` 必须有 `result_hash`，且不能有 `error_code`。
- 其他终态必须有 `error_code`，且不能有 `result_hash`。
- `completed_at` 不得早于 `started_at`。

新增 forward-only `006_tool_call_records.sql`。表中保存查询投影、`record_json` 和
`record_checksum`，并通过复合外键绑定 `agent_specs(instance_id, revision,
checksum)`。Repository 解码时重新验证所有投影与 checksum。

原始 arguments、handler 输出、Prompt、知识正文和凭据都不持久化，也不进入审计。
相同 `call_id` 再次写入返回稳定冲突；由于不保存结果正文，本阶段不宣称工具结果可
幂等重放。

## 6. document-search

```python
class DocumentSearchInput(FrozenModel):
    query: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)
```

handler 只遍历 `ToolExecutionContext.knowledge` 中 `injection_mode=inline` 的知识。
这些知识在 `ResolvedRuntimeKnowledge` 构造时已经验证正文 checksum，并在 context
构造时与 AgentSpec 引用精确匹配。

检索使用确定性词项重叠评分和稳定 tie-break，不引入向量数据库。结果正文限制长度，
最多返回 `top_k` 项。它是离线演示能力，不代表语义检索质量。

## 7. Offline Demo Runtime

`OfflineDemoRuntimeAdapter` 实现现有 `RuntimeAdapter`：

- runtime 名称固定为 `demo-runtime`；若 Spec runtime target 或 context runtime name
  不匹配，返回稳定失败结果。
- 当 Spec 授权 `document-search` 时，通过 ToolExecutor 执行一次检索。
- 生成固定 Writer 结构 `{title, body}`，再用 AgentSpec `output_schema` 校验。
- 工具错误或输出 Schema 不匹配转换为 `RunResult(status=failed)`，不抛出模型异常。
- 不修改实例状态，不持久化 RunResult，不访问网络。

离线输出只用于证明 Runtime、Tool、Spec、知识与审计可以闭环，不作为内容质量实验
结果。

## 8. 可选模型 Gateway

模型 Runtime 依赖 provider-neutral `ModelGateway`，`start(ModelInvocation)` 返回隔离的
`ModelSession`，每次 `next(tool_results)` 只能返回一个工具调用或一个最终结构化结果。
默认最多 4 轮，构造参数限制为 1-8 轮；运行期间 model name 必须稳定，provider call ID
不得重复。模型提出的工具名与参数不能直接调用 handler，Runtime 必须为其生成内部 UUID，
补齐当前 instance/revision/Spec checksum 和 Spec 中的工具版本，再进入同一
`ToolExecutor`。工具失败终止运行，只有通过 Pydantic 输出校验的结果才能回传模型。

`OpenAIResponsesGateway` 位于 optional 模块，`llm` extra 只安装当前已实现的官方
`openai>=2,<3` SDK。映射依据 2026-07-23 核对的官方
[Function calling](https://developers.openai.com/api/docs/guides/function-calling) 与
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) 文档：

- 使用 Responses API，固定 `parallel_tool_calls=False`，每轮最多一个函数调用。
- 固定 `store=False`，本地保存并回放 response output item 与
  `function_call_output`，不依赖 provider 服务端会话状态。
- provider 工具声明使用 `strict=False`。工厂支持的 Draft 2020-12 Schema 不保证属于
  provider strict subset；真正的输入安全边界是 `ToolExecutor` 的 Pydantic 再校验。
- 最终响应请求 JSON object；gateway 解析后，Runtime 再以 AgentSpec
  `output_schema` 执行本地 Draft 2020-12 校验。
- provider 异常统一转换为 `MODEL_GATEWAY_FAILED`，不复制 SDK 异常文本。

`create_openai_gateway()` 只惰性导入固定包名 `openai`；不接受动态 provider 模块名。
默认 Container 不构造该 client、不读取 API key、不访问网络。默认测试注入 fake
gateway；provider DTO、异常和凭据不得进入 application/domain 或稳定错误 envelope。

## 9. 错误与脱敏

新增稳定错误包括：

- `TOOL_CONTEXT_MISMATCH`
- `TOOL_UNAVAILABLE`
- `TOOL_VERSION_MISMATCH`
- `TOOL_DEFINITION_MISMATCH`
- `TOOL_INPUT_VALIDATION_FAILED`
- `TOOL_OUTPUT_VALIDATION_FAILED`
- `TOOL_TIMEOUT`
- `TOOL_EXECUTION_FAILED`
- `TOOL_CALL_ALREADY_EXISTS`
- `MODEL_GATEWAY_FAILED`
- `MODEL_PROTOCOL_INVALID`
- `MODEL_TURN_LIMIT_EXCEEDED`

Pydantic 错误只保留 location、message、type。未知 handler/provider 异常只记录异常
类型与稳定错误码，不复制异常文本、输入、结果或 traceback。

## 10. 已知限制

- `asyncio.timeout()` 是协作式取消，不能隔离恶意同步阻塞代码。Alpha 只注册本项目
  自己实现且输入有界的 async handler。
- 当前只读 handler 在执行后写记录；记录事务失败会使调用整体失败。开放外部写工具前
  必须增加执行前 reservation、outbox 或具备幂等语义的外部操作。
- 已持久化的重复 `call_id` 会在 handler 前被拒绝；并发首次请求仍可能同时执行到最终
  唯一约束，因而开放外部写工具前仍必须增加 reservation 或外部幂等键。
- ToolCallRecord 只保存哈希，不能用于恢复原始输出。
- lexical search、离线 Writer 和 fake gateway 只验证工程契约，不验证模型语义质量。
- Docker、网络 allowlist、文件工作区隔离和第三方插件注册不属于 M3.5。

## 11. 验证矩阵

| 风险 | 自动化证据 |
| --- | --- |
| Catalog/Registry 漂移 | Pydantic Schema 与 resolved metadata 精确相等 |
| 伪造或陈旧 Spec | 持久化 Spec、实例 head、revision 和 RUNNING 状态检查 |
| 未授权或版本漂移 | Executor rejection matrix |
| 参数或输出不可信 | strict input/output model tests |
| handler 超时/异常 | timeout、FactoryError、unexpected、cancellation tests |
| 敏感数据泄漏 | record、audit、日志和异常字符串断言 |
| 记录不一致 | migration FK/CHECK、projection、checksum、事务回滚测试 |
| 重启丢失 | 文件 SQLite 重建 Container 后读取 ToolCallRecord 与审计 |
| Runtime 越权 | offline/model Runtime 只能调用 Spec 中工具 |
| 隐式联网 | 默认测试使用 offline/fake gateway，网络调用计数为零 |
| provider 协议漂移 | 结构 fake 覆盖 function call、结果回放、并行调用拒绝、非法 JSON 与异常归一化 |
| 发布包漏资源 | wheel 检查 Runtime、Tool、repository 与 `006` migration；`llm` extra 锁定安装 |
