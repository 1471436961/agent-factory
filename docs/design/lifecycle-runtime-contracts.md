# M3.2 生命周期与 Runtime 契约设计说明

## 1. 目标与范围

M3.2 为 Agent 实例增加显式、可审计、可并发校验的生命周期迁移，并定义后续 Runtime Adapter 必须遵守的数据契约。

本工作包实现：

- 纯 `LifecyclePolicy` 和封闭状态迁移表。
- `TransitionInstanceCommand`、revision CAS、幂等重放、审计和 REST action。
- 进入 `RUNNING` 前的 AgentSpec 可构建性检查。
- `RuntimeContextRef`、`ResolvedRuntimeKnowledge`、`RunRequest`、`RunResult` 和 `RuntimeAdapter` Protocol。

本工作包不实现 Runtime、模型调用、工具执行、对话记忆、checkpoint、outbox dispatcher 或新持久化表。

## 2. 状态所有权

允许迁移表固定为：

| 当前状态 | 允许目标 | 附加条件 |
| --- | --- | --- |
| `CREATED` | `RUNNING`, `TERMINATED` | 进入 RUNNING 前验证当前快照可构建 AgentSpec |
| `RUNNING` | `WAITING`, `COMPLETED`, `FAILED`, `TERMINATED` | 无 |
| `WAITING` | `RUNNING`, `FAILED`, `TERMINATED` | 进入 RUNNING 前重新验证当前快照 |
| `FAILED` | `RUNNING`, `TERMINATED` | `FAILED -> RUNNING` 必须 `retry=true` |
| `DEGRADED` | `RUNNING`, `TERMINATED` | DEGRADED 只能由 M2 降级引擎产生 |
| `COMPLETED` | 无 | 终态 |
| `TERMINATED` | 无 | 终态 |

通用 transition action 不允许把目标设置为 `DEGRADED`。该状态代表已有观察证据触发配置回退，只能由 `DegradationPolicy` 和 `record_task_outcome()` 产生；允许客户端直接设置会绕过评估报告、观察窗口和审计证据链。

`COMPLETED` 与 `TERMINATED` 都是终态。完成后再改成终止会覆盖成功完成这一业务事实，因此不允许 `COMPLETED -> TERMINATED`。

所有迁移都要求 1-1000 字符的非空 `reason`。`retry=true` 只允许出现在 `FAILED -> RUNNING`；其他迁移携带该标记时返回 `INVALID_STATE_TRANSITION`，避免服务端静默忽略调用方意图。

## 3. 纯生命周期策略

```python
ALLOWED_TRANSITIONS: Mapping[
    InstanceStatus,
    frozenset[InstanceStatus],
]


class LifecyclePolicy:
    def transition(
        self,
        instance: AgentInstance,
        target_status: InstanceStatus,
        *,
        reason: str,
        retry: bool,
        now: datetime,
    ) -> AgentInstance: ...
```

策略只执行状态、reason、retry、revision 和时间规则，不访问 Repository、AgentSpecBuilder 或工具目录。成功时返回新 `AgentInstance`：

- `revision = old.revision + 1`
- `status = target_status`
- `updated_at = now`
- 其他字段保持不变

非法迁移统一抛出 `InvalidStateTransitionError`，details 至少包含 `instance_id`、`from_status`、`to_status` 和稳定 `reason` 标签。

## 4. 进入 RUNNING 的准备检查

`FactoryController.transition_instance()` 在目标为 `RUNNING` 时，对当前 head 执行与 `export_spec()` 相同的结构校验：

1. 重新读取并校验所有知识绑定的版本、checksum 和注入模式。
2. 通过 `ToolPolicy` 解析当前配置声明的全部工具。
3. 使用 `AgentSpecBuilder` 构建临时 AgentSpec，证明当前快照可被标准化。

临时 AgentSpec 不写入 `agent_specs`，也不产生 `spec.exported` 事件。状态迁移会产生新 revision，调用方必须随后显式导出该新 revision 的 AgentSpec。这样保持导出 API 的副作用语义，不会在 transition 内制造隐藏导出。

准备检查、snapshot CAS、审计和幂等记录位于同一写事务。任一步失败时不得留下新 snapshot、审计或幂等成功记录。

## 5. 命令与事务

```python
class TransitionInstanceCommand(FrozenModel):
    instance_id: UUID
    expected_revision: PositiveInt
    target_status: InstanceStatus
    reason: str = Field(min_length=1, max_length=1_000)
    retry: bool = False
    actor: Actor
    idempotency_key: OptionalIdempotencyKey = None
```

Controller 顺序固定为：

```text
typed idempotency replay
  -> load current head
  -> expected_revision check
  -> LifecyclePolicy.transition()
  -> target RUNNING readiness check
  -> save_snapshot(expected_revision=current.revision)
  -> append instance.transitioned audit
  -> store typed idempotency response
  -> commit
```

同一 idempotency key、operation 和请求体返回首次产生的精确 `AgentInstance`，不重复 revision 和审计。相同 expected revision 的并发不同请求最多一个成功，正确性由数据库事务与 `instance_heads.current_revision` CAS 保证，而不是进程内锁。

现有 `instance_snapshots`、`instance_heads`、`audit_events` 和 `idempotency_records` 已能保存全部结果，M3.2 不新增 migration。

## 6. 审计契约

`instance.transitioned` 事件字段：

```json
{
  "from_status": "created",
  "to_status": "running",
  "from_revision": 2,
  "to_revision": 3,
  "reason": "start demo runtime",
  "retry": false
}
```

事件的 `entity_revision` 使用迁移后的 revision，actor 只能来自认证后的 `Principal.subject`。reason 最长 1000 字符，可进入审计；不得在 reason 中放置 Token、Prompt、知识正文或用户任务正文。

## 7. REST 契约

```http
POST /api/v1/instances/{instance_id}/transitions
Authorization: Bearer <token>
Idempotency-Key: <optional-key>
Content-Type: application/json
```

```json
{
  "expected_revision": 2,
  "target_status": "running",
  "reason": "start demo runtime",
  "retry": false
}
```

- 权限：`factory:write`
- 成功：`200 AgentInstance`
- revision 冲突：`409 REVISION_CONFLICT`
- 非法状态或 retry 组合：`409 INVALID_STATE_TRANSITION`
- 当前快照无法构建 AgentSpec：既有知识、工具或 `INSTANCE_NOT_READY` 错误
- Pydantic 字段错误：`422 REQUEST_VALIDATION_FAILED`

路由只把 path、body、Principal 和 idempotency header 转换为 `TransitionInstanceCommand`，不复制状态规则。

## 8. Runtime 数据契约

Runtime 契约属于 application 边界，不属于领域生产模型，也不依赖 FastAPI。

```python
class RuntimeContextRef(FrozenModel):
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    runtime_name: Slug
    external_thread_id: str | None = Field(default=None, min_length=1, max_length=256)
    knowledge_namespaces: tuple[Slug, ...] = ()
    created_at: AwareDatetime


class ResolvedRuntimeKnowledge(FrozenModel):
    slot_name: Slug
    knowledge_id: Slug
    version: SemVer
    checksum: Sha256
    injection_mode: InjectionMode
    mime_type: str = Field(min_length=1, max_length=128)
    content: str | JsonObject


class RunRequest(FrozenModel):
    task_id: UUID
    spec: AgentSpec
    input: str = Field(min_length=1, max_length=64_000)
    knowledge: tuple[ResolvedRuntimeKnowledge, ...] = ()
    context_ref: RuntimeContextRef | None = None
    metadata: JsonObject = Field(default_factory=FrozenJsonObject)


class RunResult(FrozenModel):
    task_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    status: RuntimeRunStatus
    content: str = Field(default="", max_length=128_000)
    structured_output: JsonObject | None = None
    tool_call_ids: tuple[UUID, ...] = ()
    runtime_name: Slug
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    started_at: AwareDatetime
    completed_at: AwareDatetime


class RuntimeAdapter(Protocol):
    async def run(self, request: RunRequest) -> RunResult: ...
```

`ResolvedRuntimeKnowledge` 校验 content checksum。`RunRequest` 要求解析知识与 `AgentSpec.knowledge` 一一对应，并要求可选 context 的 instance、revision 和 Spec checksum 与 `spec` 完全一致。`RunResult` 要求完成时间不早于开始时间；failed 必须有 `error_code`，completed 不得有 `error_code`。

M3.2 只保留 `tool_call_ids`，不提前定义 M3.5 的完整 `ToolCallRecord`。后续工具执行层负责持久化脱敏记录，Runtime 结果只引用其 ID。

## 9. 验证矩阵

| 验证面 | 证据 |
| --- | --- |
| 完整状态图 | 每条允许边和全部拒绝组合的参数化单元测试 |
| retry 与终态 | FAILED 重试、意外 retry、COMPLETED/TERMINATED 测试 |
| DEGRADED 所有权 | 通用 transition 不能把目标设为 DEGRADED |
| RUNNING 准备检查 | 缺知识、未知工具、checksum 错误均不产生状态副作用 |
| revision CAS | stale revision 和并发双写最多一个成功 |
| 事务原子性 | snapshot、审计、幂等任一步失败整体回滚 |
| typed idempotency | 精确对象重放且 revision、审计数量不增加 |
| REST | 认证、权限、DTO、错误、correlation 和 OpenAPI |
| 重启恢复 | 文件 SQLite 重建 app 后恢复状态并可重放幂等响应 |
| Runtime 契约 | identity、checksum、知识集合、时间和失败字段校验 |

## 10. 已知限制

- 状态 reason 只保存在审计事件，不复制到 `AgentInstance` 快照。
- transition 不运行 Agent，也不代表 Runtime 已实际启动或停止。
- 工厂与 Runtime 之间暂时没有租约、heartbeat 或远程 checkpoint。
- 进程在状态变为 RUNNING 后崩溃，不会自动判定 FAILED；该恢复策略不属于 M3.2。
- Runtime 契约尚未成为公开 REST API，M3.5 实现工具执行时可做向后兼容的可选字段扩展。
