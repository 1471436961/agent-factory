# REST API 设计说明

## 1. 目标与边界

M1.4 将 `FactoryController` 的核心生产链暴露为 FastAPI 接口；M2.6 在同一边界上增加评估套件、技能树、评估、人工复核、晋升和观察结果路由。接口层只负责传输契约、请求上下文、DTO 转换和异常映射；原型状态、知识槽、工具权限、评估、晋升、降级、幂等与 revision 规则仍由 application/domain 层执行。

当前接口不实现用户认证、授权、Python SDK、Tool adapter 或 Agent 任务执行。评估路由只接收外部提交的 case result，不负责执行 Agent。写接口要求 `X-Actor-ID`，但该值只是调用方提供的审计标签，**不构成可信身份**。在认证层完成前，服务不得直接暴露到不可信网络。

## 2. 模块边界

```text
HTTP request
  -> RequestContextMiddleware（请求体上限、correlation）
  -> FastAPI dependency / Pydantic request DTO
  -> route（DTO -> command/query）
  -> FactoryController（业务规则与事务）
  -> response_model / exception handler
  -> HTTP response
```

代码职责：

| 模块 | 职责 |
| --- | --- |
| `contracts.py` | M1/M2 请求、健康检查和统一错误模型 |
| `dependencies.py` | Container/Controller 注入、actor 与幂等 header 解析、command 二次校验 |
| `middleware.py` | correlation 生命周期和请求体大小上限 |
| `errors.py` | 领域错误到 HTTP status 的显式映射及安全错误响应 |
| `routers/*.py` | 路由参数到 application command/query 的机械转换 |
| `main.py` | app factory、lifespan、中间件、异常 handler 与 router 装配 |

## 3. 公共 HTTP 契约

### 3.1 路径与内容类型

- 默认业务前缀：`/api/v1`，可通过 `AGENT_FACTORY_API_PREFIX` 修改。
- JSON 请求使用 `Content-Type: application/json`。
- `/health/live` 和 `/health/ready` 不使用业务前缀。
- OpenAPI 由 FastAPI 根据实际 request/response model 生成。

### 3.2 请求头

| Header | 适用范围 | 约束 | 语义 |
| --- | --- | --- | --- |
| `X-Actor-ID` | 全部写路由 | 必填，trim 后 1-128 字符 | 不可信审计标签，不是认证主体 |
| `Idempotency-Key` | 除规格导出外的写路由 | 可选，8-128 字符 | 相同 operation 与请求重放；不同请求返回 409 |
| `X-Correlation-ID` | 全部请求 | 可选，严格 UUID | 缺失时生成；响应 header、错误体和审计共用 |

`X-Correlation-ID` 在 middleware 中写入 `CorrelationContext`，并在 `finally` 中通过 `ContextVar.reset(token)` 恢复进入请求前的上下文，避免并发请求之间串值。

### 3.3 请求体上限

`AGENT_FACTORY_MAX_REQUEST_BYTES` 默认 1 MiB，最小配置为 1024 bytes。middleware 在调用 FastAPI 前完整读取请求体，累计字节数超限便返回 413，路由和 Controller 不会执行。已声明且超限的 `Content-Length` 会被提前拒绝；chunked body 也执行相同限制。

该实现以“有界 JSON API”为前提，最多缓存 `max_request_bytes` 的请求数据。M1 不支持流式文件上传；未来如引入大文件知识包，应改用对象存储直传或独立 streaming endpoint。

## 4. 请求模型

请求模型继承 `FrozenModel`，因此统一启用 `extra="forbid"`、严格校验和冻结语义。

```python
class RegisterPrototypeRequest(FrozenModel):
    prototype_id: Slug
    version: SemVer
    definition: AgentDefinition
    skill_tree: SkillTreeRef | None = None
    publish: bool = False


class DeprecatePrototypeRequest(FrozenModel):
    reason: str = Field(min_length=1, max_length=1_000)


class CloneAgentRequest(FrozenModel):
    runtime_target: Slug | None = None


class RegisterKnowledgeRequest(DomainKnowledgeDraft):
    pass


class BindKnowledgeRequest(FrozenModel):
    expected_revision: PositiveInt
    selections: Annotated[
        tuple[KnowledgeSelection, ...],
        Field(min_length=1),
    ]
    replace_existing: bool = False


class ExportSpecRequest(FrozenModel):
    revision: PositiveInt | None = None


class RegisterEvaluationSuiteRequest(EvaluationSuiteDraft):
    pass


class RegisterSkillTreeRequest(SkillTreeDraft):
    pass


class EvaluateInstanceRequest(FrozenModel):
    expected_revision: PositiveInt
    suite: EvaluationSuiteRef
    runtime_model: str = Field(min_length=1, max_length=128)
    case_results: Annotated[tuple[SubmittedCaseResult, ...], Field(min_length=1)]


class ReviewEvaluationRequest(FrozenModel):
    decision: ReviewDecision
    comment: str = Field(default="", max_length=2_000)


class PromoteAgentRequest(FrozenModel):
    expected_revision: PositiveInt
    target_node_id: Slug
    evaluation_report_id: UUID
    evaluation_review_id: UUID | None = None
    knowledge_selections: tuple[KnowledgeSelection, ...] = ()


class RecordTaskOutcomeRequest(FrozenModel):
    expected_revision: PositiveInt
    task_id: UUID
    skill_node_id: Slug
    passed: bool
    evaluation_report_id: UUID
```

`BindKnowledgeRequest` 与 `PromoteAgentRequest` 都拒绝完全相同的 `(slot_name, knowledge_id, version)` 重复项。槽位是否存在、版本是否匹配和 cardinality 是否有效仍由 application policy 校验。`EvaluateInstanceRequest.expected_revision` 在路由中映射为 `EvaluationSubmission.instance_revision`；客户端不能提交 report ID、Spec checksum、开始/结束时间或最终 decision，这些字段由 Controller 生成。

## 5. 路由清单

| Method | Path | 请求体 | 成功响应 | actor | 幂等键 |
| --- | --- | --- | --- | --- | --- |
| GET | `/health/live` | 无 | 200 `HealthResponse` | 否 | 否 |
| GET | `/health/ready` | 无 | 200 或 503 | 否 | 否 |
| POST | `/api/v1/prototypes` | `RegisterPrototypeRequest` | 201 `AgentPrototype` | 是 | 可选 |
| GET | `/api/v1/prototypes` | query | 200 `Page[AgentPrototype]` | 否 | 否 |
| POST | `/api/v1/prototypes/{id}/versions/{version}/publish` | 无 | 200 `AgentPrototype` | 是 | 可选 |
| POST | `/api/v1/prototypes/{id}/versions/{version}/deprecate` | `DeprecatePrototypeRequest` | 200 `AgentPrototype` | 是 | 可选 |
| POST | `/api/v1/prototypes/{id}/versions/{version}/instances` | `CloneAgentRequest` | 201 `AgentInstance` | 是 | 可选 |
| POST | `/api/v1/knowledge` | `RegisterKnowledgeRequest` | 201 `DomainKnowledge` | 是 | 可选 |
| POST | `/api/v1/instances/{id}/knowledge-bindings` | `BindKnowledgeRequest` | 200 `AgentInstance` | 是 | 可选 |
| POST | `/api/v1/instances/{id}/spec-exports` | `ExportSpecRequest` | 200 `AgentSpec` | 是 | 否 |
| GET | `/api/v1/audit-events` | query | 200 `Page[AuditEvent]` | 否 | 否 |
| POST | `/api/v1/evaluation-suites` | `RegisterEvaluationSuiteRequest` | 201 `EvaluationSuite` | 是 | 可选 |
| GET | `/api/v1/evaluation-suites/{id}/versions/{version}` | 无 | 200 `EvaluationSuite` | 否 | 否 |
| POST | `/api/v1/skill-trees` | `RegisterSkillTreeRequest` | 201 `SkillTree` | 是 | 可选 |
| GET | `/api/v1/skill-trees/{id}/versions/{version}` | 无 | 200 `SkillTree` | 否 | 否 |
| POST | `/api/v1/instances/{id}/evaluations` | `EvaluateInstanceRequest` | 201 `EvaluationReport` | 是 | 可选 |
| POST | `/api/v1/evaluation-reports/{id}/reviews` | `ReviewEvaluationRequest` | 201 `EvaluationReview` | 是 | 可选 |
| POST | `/api/v1/instances/{id}/promotions` | `PromoteAgentRequest` | 200 `AgentInstance` | 是 | 可选 |
| POST | `/api/v1/instances/{id}/task-outcomes` | `RecordTaskOutcomeRequest` | 200 `DegradationCheckResult` | 是 | 可选 |

规格导出使用 POST，因为首次导出会持久化 `AgentSpec` 并追加审计事件，不满足 GET 的 safe method 语义。重复导出同一 revision 由 Controller 返回已持久化快照，不重复写审计。

审计查询支持 `entity_type`、`entity_id`、可重复的 `event_type`、`actor`、`created_from`、`created_to`、`page` 和 `page_size`。时间必须携带时区，页大小范围为 1-100。

M2 不增加通用的 report、review 或 instance GET 路由。当前恢复验证通过各写操作的持久化幂等响应、指定 revision 的 `AgentSpec` 和审计查询完成，避免为测试便利扩大公开 API。

## 6. 路由实现规则

路由不得直接访问 Repository、clock、ID generator 或 migration runner。写路由通过 `validate_command()` 构造 application command，使 path、header 和 body 合并后的跨字段错误仍进入统一的 `REQUEST_VALIDATION_FAILED` 契约。

```python
@router.post(
    "/{instance_id}/knowledge-bindings",
    response_model=AgentInstance,
)
async def bind_knowledge(
    instance_id: UUID,
    body: BindKnowledgeRequest,
    controller: ControllerDep,
    actor: ActorDep,
    idempotency_key: IdempotencyHeader = None,
) -> AgentInstance:
    command = validate_command(
        BindKnowledgeCommand,
        {
            "instance_id": instance_id,
            "expected_revision": body.expected_revision,
            "selections": body.selections,
            "replace_existing": body.replace_existing,
            "actor": actor,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.bind_knowledge(command)
```

评估路由必须显式嵌套服务端控制的 `instance_id` 和 revision，不能把请求体直接当作持久化报告：

```python
@router.post(
    "/{instance_id}/evaluations",
    response_model=EvaluationReport,
    status_code=HTTPStatus.CREATED,
)
async def evaluate_instance(
    instance_id: UUID,
    body: EvaluateInstanceRequest,
    controller: ControllerDep,
    actor: ActorDep,
    idempotency_key: IdempotencyHeader = None,
) -> EvaluationReport:
    command = validate_command(
        EvaluateInstanceCommand,
        {
            "submission": {
                "instance_id": instance_id,
                "instance_revision": body.expected_revision,
                "suite": body.suite,
                "runtime_model": body.runtime_model,
                "case_results": body.case_results,
            },
            "actor": actor,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.evaluate_instance(command)
```

## 7. 错误契约

所有错误使用同一 envelope：

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "Instance revision no longer matches",
    "details": {
      "expected_revision": 1,
      "actual_revision": 2
    },
    "correlation_id": "00000000-0000-0000-0000-000000000301"
  }
}
```

主要映射：

| HTTP | 代表性错误码 |
| --- | --- |
| 400 | `INVALID_OUTPUT_SCHEMA`, `INVALID_CORRELATION_ID`, `INVALID_CONTENT_LENGTH` |
| 403 | `TOOL_NOT_GRANTED`, `TOOL_PERMISSION_DENIED` |
| 404 | `PROTOTYPE_NOT_FOUND`, `KNOWLEDGE_NOT_FOUND`, `INSTANCE_NOT_FOUND`, `SKILL_TREE_NOT_FOUND`, `SKILL_NODE_NOT_FOUND`, `EVALUATION_SUITE_NOT_FOUND`, `EVALUATION_REPORT_NOT_FOUND`, `ROUTE_NOT_FOUND` |
| 405 | `METHOD_NOT_ALLOWED` |
| 409 | 重复对象、原型/实例/技能状态、review、revision 与 idempotency 冲突 |
| 413 | `KNOWLEDGE_PAYLOAD_TOO_LARGE`, `REQUEST_TOO_LARGE` |
| 422 | Pydantic 请求错误、知识约束、未知工具、技能依赖、suite/report 来源、晋升拒绝或观察证据矛盾 |
| 500 | `INTERNAL_ERROR` |
| 503 | `REPOSITORY_UNAVAILABLE`, `SERVICE_NOT_READY` |

`ERROR_STATUS_BY_CODE` 必须覆盖当前全部 `FactoryError` 直接子类；契约测试以集合相等检查，新增领域错误却未注册 HTTP 映射时测试立即失败。

`RequestValidationError` 只返回 `location`、`message` 和 `type`，不回显原始 input。未知异常在服务端按 correlation ID 记录，客户端只收到固定 `INTERNAL_ERROR`，不得泄露异常文本、SQL、本地路径或密钥。

## 8. 生命周期与 readiness

`create_app(settings)` 每次构造独立 `Container`。构造阶段只组装对象；FastAPI lifespan 启动时执行 migration 并将 Container 标记为 ready，关闭时释放资源。`/health/live` 只证明进程可响应，`/health/ready` 同时检查 Container ready 状态与数据库 ping。

测试必须显式进入 `app.router.lifespan_context(app)`，否则 readiness 应稳定返回 503。这避免测试绕过真实启动协议。

## 9. 验证证据

自动化测试覆盖：

- 真实 SQLite 上的注册、幂等重放、列表、发布、知识注册、克隆、绑定、规格导出、废弃和审计查询。
- 关闭并重建 app 后，使用同一数据库恢复原型、规格和审计；重复导出不新增 `spec.exported`。
- 缺失 actor、未知字段、非法 correlation ID、404、405、领域 404/409、500 脱敏。
- `Content-Length` 与 chunked body 超限，包括下游不会读取 body 的端点。
- correlation response header、错误体和审计事件一致，ContextVar 在请求后恢复。
- 自定义 API prefix、lifespan 前 readiness 503、规格导出只暴露 POST。
- OpenAPI 精确声明 M2 的八个最小路径及唯一允许的方法。
- 真实 HTTP 主链注册 Suite/Tree，注册并克隆绑定技能树的 Engineer 原型，提交评估与最终人工复核，显式晋升，记录三次观察并触发确定性降级。
- 关闭并重建 app 后，精确恢复 Suite、Tree 和降级后 `AgentSpec`；使用原幂等键重放 report、review、promotion 和最终 outcome，响应与首次结果相同且审计总量不变。
- M2 未知字段和 review comment/evidence 不进入 422 错误体；缺失 Suite 返回稳定 `EVALUATION_SUITE_NOT_FOUND`。

当前限制：认证和授权缺失；审计读取尚未受权限保护；评估 evidence 由调用方提供且不可信；请求体采用有界缓冲；SQLite 仍为单进程 Alpha 后端。这些限制必须在部署说明中保留，不能将当前 REST 契约描述为可直接公网部署的生产服务。
