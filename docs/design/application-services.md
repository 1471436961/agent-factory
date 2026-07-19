# M1.3 Application Services Design Note

## 1. 解决的问题与边界

M1.3 把 M1.1 的不可变领域模型与 M1.2 的 Repository/UoW 组合成可执行生产链。交付范围是原型注册、查询、发布、废弃，知识注册，实例克隆、知识绑定、`AgentSpec` 导出和审计查询。

本阶段不实现 REST DTO、HTTP status 映射、认证、技能 DAG、Evaluator、LLM 调用、工具 handler 或 Agent 任务执行。`FactoryController` 是确定性应用服务，不是 Agent。

## 2. 依赖方向

```text
commands / queries
       |
       v
FactoryController
  |    |      |
  |    |      +--> IdempotencyService / AuditEventFactory
  |    +---------> pure policies and AgentSpecBuilder
  +--------------> UnitOfWorkFactory / Clock / ID / Correlation ports
                         ^
                         |
                  SQLite + system adapters
```

- domain policy 不执行 I/O，也不导入 application 或 infrastructure。
- application 依赖 Protocol，不导入 SQLite、FastAPI 或模型 SDK。
- infrastructure 提供 SQLite UoW、系统端口和 metadata-only `ToolCatalog`。
- `Container` 是 composition root；`build_container()` 只组装对象，`start()` 才执行 migration。

## 3. Controller 构造契约

```python
class FactoryController:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        id_generator: IdGenerator,
        correlation_context: CorrelationContext,
        prototype_policy: PrototypePolicy,
        knowledge_policy: KnowledgeBindingPolicy,
        tool_policy: ToolPolicy,
        spec_builder: AgentSpecBuilder,
        idempotency: IdempotencyService,
        audit_factory: AuditEventFactory,
        max_inline_knowledge_bytes: int,
    ) -> None: ...
```

构造函数只接收 M1.3 实际使用的依赖。`EvaluatorPort`、技能仓储和任务结果仓储在 M2 引入，避免 Controller 提前依赖未实现能力。

## 4. 命令边界

所有命令继承 `FrozenModel`，使用 Pydantic v2 的 `frozen=True`、`extra="forbid"` 和严格字段约束。写命令可带 8 到 128 字符的 `idempotency_key`。

`BindKnowledgeCommand` 的额外不变量：

```python
class BindKnowledgeCommand(FrozenModel):
    instance_id: UUID
    expected_revision: PositiveInt
    selections: Annotated[
        tuple[KnowledgeSelection, ...],
        Field(min_length=1),
    ]
    replace_existing: bool = False
    actor: Actor
    idempotency_key: IdempotencyKey | None = None
```

- selections 不能为空；
- `(slot_name, knowledge_id, version)` 不允许精确重复；
- 跨 selection 的槽位、类型、版本和基数由 `KnowledgeBindingPolicy` 校验。

## 5. 写事务与幂等

除 `export_spec` 外，写操作统一遵循：

```text
Clock.now()
   |
   v
BEGIN IMMEDIATE
   |
   +--> 有 idempotency key：删除过期记录 -> 查询 key
   |       | same operation + same hash -> typed response -> COMMIT
   |       | different operation/hash  -> IDEMPOTENCY_KEY_REUSED -> ROLLBACK
   |       + absent                     -> 继续业务操作
   |
   +--> 读取当前状态 -> pure policy 校验 -> 写业务快照
   +--> append allowlisted audit event
   +--> 保存 typed response JSON + request hash
   +--> COMMIT
```

请求哈希使用 command 的 canonical JSON，排除 `idempotency_key` 本身，但保留 actor 和全部业务参数。只缓存成功结果；异常不写幂等记录。缓存读取后用目标 Pydantic response type 重新校验，坏数据转换为 `REPOSITORY_UNAVAILABLE`。

业务数据、审计事件和幂等响应共享同一个 UoW。任何 policy、repository、correlation 或 commit 异常都由 `__aexit__` 回滚，不允许部分成功。

## 6. 操作规格

| 方法 | 关键前置条件 | 成功写入 | 返回 |
| --- | --- | --- | --- |
| `register_prototype` | output schema 合法；工具存在且权限允许；键唯一 | prototype；registered 事件；`publish=True` 时再写 published 事件 | `AgentPrototype` |
| `publish_prototype` | 当前状态为 DRAFT | CAS 替换为 PUBLISHED；published 事件 | `AgentPrototype` |
| `deprecate_prototype` | 当前状态为 PUBLISHED | CAS 替换为 DEPRECATED；deprecated 事件 | `AgentPrototype` |
| `register_knowledge` | inline checksum 正确且不超过 256 KiB；键唯一 | knowledge；registered 事件 | `DomainKnowledge` |
| `clone_agent` | 原型存在且为 PUBLISHED；工具重新校验 | revision 1 instance；cloned 事件 | `AgentInstance` |
| `bind_knowledge` | revision 匹配；状态允许；最终绑定集合合法 | revision + 1 snapshot/head；每个新 selection 一条 bound 事件 | `AgentInstance` |
| `export_spec` | instance/revision 存在；必填槽、知识引用和工具权限有效 | 首次导出时 spec 与 exported 事件 | `AgentSpec` |
| `query_audit` | query Pydantic 校验通过 | 无 | `Page[AuditEvent]` |

URI 知识在 M1 只保存来源和调用方提供的 checksum，不主动下载；INLINE 内容按 UTF-8 字节或 canonical JSON 字节检查大小与 checksum。

## 7. 知识绑定算法

1. 读取当前 head；不存在返回 `INSTANCE_NOT_FOUND`。
2. `expected_revision` 不匹配返回 `REVISION_CONFLICT`。
3. RUNNING 返回 `INSTANCE_BUSY`；FAILED/COMPLETED/TERMINATED 返回 `INVALID_STATE_TRANSITION`。
4. 计算本次触碰槽位。未允许替换且槽已有绑定时返回 `KNOWLEDGE_ALREADY_BOUND`。
5. `replace_existing=True` 时删除所有触碰槽的旧选择；未触碰槽保留。
6. 用 `get_many()` 读取最终选择涉及的知识包，policy 校验槽位存在、kind、SemVer、required 和 `max_items`。
7. 新建 `revision + 1` 完整快照并执行 repository compare-and-swap。
8. 未触碰 binding 保留原始 `bound_at/bound_by`；本次 selection 生成新 binding。
9. 每个本次 selection 写一条 `knowledge.bound`，payload 只含引用、checksum、注入模式和 `replaced`。

Controller 不自动重试 revision 冲突，也不合并两个并发命令；调用方必须重新读取 head 后决定下一步。

## 8. AgentSpec 导出

```text
read-only UoW: resolve revision -> spec exists? -> return
                                      |
                                      no
                                      v
write UoW: resolve revision -> double check spec -> revalidate knowledge/tools
                                      |
                                      v
                     build checksummed AgentSpec
                                      |
                         add_if_absent(instance, revision)
                            | true                 | false
                            v                      v
                    append audit + commit    return persisted row
```

Builder 按稳定顺序输出 tools 和 knowledge refs，先用 64 个 `0` 作为 checksum 占位，再对排除 `spec_checksum` 的完整模型计算 SHA-256，并通过 `AgentSpec.model_validate()` 生成最终不可变对象。同一 revision 已有规格时直接返回，不重新生成时间或审计事件。

## 9. 工具元数据边界

M1 `ToolCatalog` 只返回 `ResolvedToolSpec`：名称、版本、描述、输入/输出 JSON Schema 和权限标签。默认目录只注册 `document-search@1.0.0`，权限上限仅允许 `read-only`。

该目录不保存 handler，不执行参数，不访问网络或文件系统。可执行 `ToolRegistry`、输入/输出 Pydantic model、超时和沙箱属于 M3；两者不得混为一个接口，否则生产层会被迫依赖运行时副作用。

## 10. 审计与 correlation

`AuditEventFactory` 为每种 M1 操作提供专用方法，避免调用方自由拼接载荷。知识正文、system prompt、工具参数和 secret 不进入 payload。

一次业务操作只解析一次 correlation ID，全部事件共享该 UUID。接口层尚未设置上下文时由 `IdGenerator` 生成；上下文存在但不是 UUID 时视为内部契约破坏并回滚。M1.4 middleware 负责在请求开始时设置并在 `finally` 中 reset。

## 11. 验证证据与已知限制

- pure policy 单元测试覆盖原型状态、槽位/kind/SemVer/基数、必填槽、工具未知/越权和 checksum 稳定性。
- 幂等单元测试覆盖 typed replay、跨 operation 冲突、过期删除和坏缓存拒绝。
- 文件型 SQLite 集成测试覆盖完整 M1.3 链、重复请求不重复审计、revision 冲突、替换绑定、缺失知识、未绑定禁止导出、失败回滚和旧 binding 溯源保持。
- M1.3 本地完整门禁为 `80 passed`，总分支覆盖率 92%，其中 `application/controller.py` 为 88%；Ruff、mypy strict、sdist/wheel 构建和 wheel 内容检查通过。

本文固定的是 M1.3 application service 的交付边界。REST 错误映射和 API 契约已在 M1.4 实现，正式命名的跨应用重启退出测试由 M1.5 补齐。

当前仍存在的限制：SQLite 写事务串行；幂等响应只在 TTL 内保证重放；URI 知识不下载验证内容；没有认证、授权或工具执行。后续里程碑不得把 `X-Actor-ID` 审计标签描述为可信身份。
