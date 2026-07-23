# Python SDK 设计说明

## 1. 目标与边界

M3.3 提供公开的异步 `agent_factory.sdk.AgentFactoryClient`，通过 HTTP 消费现有 FastAPI 契约。SDK 负责 URL、Bearer 认证、correlation、幂等 header、DTO 序列化、响应校验和错误转换，不复制 Controller、知识、技能或生命周期策略。

SDK 不直接调用 `FactoryController`，不读取 Repository，不运行 Agent，不自动重试，也不实现同步客户端。M3.3 不新增数据库 migration。

## 2. 包结构

```text
src/agent_factory/sdk/
├── __init__.py       # 公开导出 Client、异常和 REST request DTO
├── client.py         # AgentFactoryClient 与 20 个 operation
├── errors.py         # 稳定、脱敏的 SDK 异常
└── operations.py     # HTTP method/path/auth 的唯一 manifest
```

`agent_factory.sdk` 直接重导出现有 `interfaces.api.contracts` 中的 request model，确保 SDK 和 REST 使用同一 Pydantic 类型，不维护第二套字段定义。

## 3. 客户端构造与所有权

```python
class AgentFactoryClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        api_prefix: str = "/api/v1",
        timeout: float | httpx.Timeout = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None: ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, ...) -> None: ...
    async def close(self) -> None: ...
```

Client 始终创建并拥有内部 `httpx.AsyncClient`；调用方只能注入 transport，不能注入所有权不明的 AsyncClient。`close()` 可重复调用，关闭后继续请求抛出 `AgentFactoryClientClosedError`。

约束：

- `base_url` 必须是含 host 的 HTTP/HTTPS URL，不允许 query、fragment 或 userinfo。
- `api_prefix` 必须是单个规范化绝对路径前缀；根前缀允许写 `/`。
- Token 必须非空，只保存在 `SecretStr` 和 HTTPX header 中，不进入 repr、异常或日志。
- HTTPX `follow_redirects=False`，避免携带 Authorization 自动跨地址重定向。
- timeout 在 Client 级统一设置；M3.3 不增加每个方法的隐式差异。

## 4. Operation manifest

`SDK_OPERATIONS` 是不可变 mapping。每项包含 SDK 方法名、HTTP method、相对路径模板和是否需要认证：

| SDK 方法 | Method | Path | Auth |
| --- | --- | --- | --- |
| `check_liveness` | GET | `/health/live` | 否 |
| `check_readiness` | GET | `/health/ready` | 否 |
| `register_prototype` | POST | `/prototypes` | 是 |
| `list_prototypes` | GET | `/prototypes` | 是 |
| `publish_prototype` | POST | `/prototypes/{prototype_id}/versions/{version}/publish` | 是 |
| `deprecate_prototype` | POST | `/prototypes/{prototype_id}/versions/{version}/deprecate` | 是 |
| `clone_agent` | POST | `/prototypes/{prototype_id}/versions/{version}/instances` | 是 |
| `register_knowledge` | POST | `/knowledge` | 是 |
| `bind_knowledge` | POST | `/instances/{instance_id}/knowledge-bindings` | 是 |
| `export_spec` | POST | `/instances/{instance_id}/spec-exports` | 是 |
| `transition_instance` | POST | `/instances/{instance_id}/transitions` | 是 |
| `register_evaluation_suite` | POST | `/evaluation-suites` | 是 |
| `get_evaluation_suite` | GET | `/evaluation-suites/{suite_id}/versions/{version}` | 是 |
| `register_skill_tree` | POST | `/skill-trees` | 是 |
| `get_skill_tree` | GET | `/skill-trees/{tree_id}/versions/{version}` | 是 |
| `evaluate_instance` | POST | `/instances/{instance_id}/evaluations` | 是 |
| `review_evaluation` | POST | `/evaluation-reports/{report_id}/reviews` | 是 |
| `promote_agent` | POST | `/instances/{instance_id}/promotions` | 是 |
| `record_task_outcome` | POST | `/instances/{instance_id}/task-outcomes` | 是 |
| `query_audit` | GET | `/audit-events` | 是 |

业务 path 在发送时添加可配置 `api_prefix`；健康检查不添加。路径变量必须逐段 URL encode。契约测试把默认前缀下 manifest 的 method/path 集合与真实 OpenAPI 精确比较。

## 5. 请求契约

所有方法都接受可选 `correlation_id: UUID | None`。缺失时 Client 使用 `uuid4()` 生成，并发送 `X-Correlation-ID`。写操作按 REST 契约接受可选 `idempotency_key`；`export_spec` 不接受幂等键，因为 Controller 已按 instance revision 做确定性导出。

请求体统一使用：

```python
request.model_dump(mode="json")
```

SDK 不发送 `X-Actor-ID`。认证 operation 使用内部 Bearer Token；健康检查也不主动发送 Authorization。查询参数忽略 `None`，枚举发送 `.value`，时间发送带时区 ISO 8601，审计 `event_type` 使用重复 query key。

M3.3 不做自动重试。连接在响应丢失前后都可能失败，SDK 无法判断服务端是否已提交事务；调用方必须使用同一 idempotency key 显式重试。

## 6. 响应与错误契约

成功响应必须先解析 JSON，再由声明的 Pydantic response model 执行 `model_validate()`。缺失 correlation header、header 与请求 correlation 不一致、非法 JSON 或 Schema 漂移均抛出 `AgentFactoryProtocolError`，不得返回未校验 dict。

异常层次：

```python
class AgentFactorySdkError(Exception): ...

class AgentFactoryApiError(AgentFactorySdkError):
    status_code: int
    code: str
    message: str
    details: JsonObject
    correlation_id: UUID

class AgentFactoryTransportError(AgentFactorySdkError):
    correlation_id: UUID
    cause_type: str

class AgentFactoryProtocolError(AgentFactorySdkError):
    status_code: int
    correlation_id: UUID

class AgentFactoryClientClosedError(AgentFactorySdkError): ...
```

标准非 2xx 响应解析 `ErrorResponse`，并验证 body/header/request 三处 correlation 一致。非标准错误响应转换为固定 `SDK_HTTP_ERROR`，保留 status 与本次请求 correlation，但不复制 response body、Header 或底层异常文本。Transport/Protocol 异常也只保留安全类型和 correlation，不保存 Token、请求体或响应正文。

## 7. 并发与状态

HTTPX AsyncClient 可被同一 event loop 中的并发任务复用。每次调用的 correlation、幂等键、query 和 body 都是局部变量；Client 不提供 `last_response` 或 `last_correlation_id` 这类共享可变状态。`close()` 与正在执行的请求并发不属于支持场景，调用方必须先结束请求再关闭。

## 8. 验证矩阵

| 验证面 | 自动化证据 |
| --- | --- |
| 20 个 operation 完整性 | manifest 与真实 OpenAPI method/path 集合精确相等 |
| DTO 共享 | SDK 公开导出与 REST request model 是同一类型对象 |
| 真实 HTTP 语义 | ASGITransport + FastAPI lifespan 完成全部 operation |
| 幂等与 correlation | 精确对象重放、审计不增加、请求/响应 ID 一致 |
| 分页与多值 query | Prototype page 与重复 audit event_type 查询 |
| 错误保真 | 401/403/404/409 的 code/status/details/correlation |
| 非标准错误 | HTML/空 body 不泄露正文，返回固定 SDK_HTTP_ERROR |
| 协议漂移 | 2xx 非 JSON、错误 Schema、correlation 缺失/冲突 |
| Transport | connect/timeout 只暴露安全 cause type，不自动重试 |
| 生命周期 | context manager、显式 close、重复 close、关闭后拒绝请求 |
| 配置 | 自定义 API prefix、base URL 与 timeout |
| 凭据安全 | repr、异常字符串和错误属性中不出现 Token |

## 9. 已知限制

- SDK 与 Server 必须使用兼容版本；Alpha 不实现协议版本协商。
- ASGITransport 不验证 DNS、TLS、反向代理或公网部署。
- M3.3 不支持同步 API、流式响应、大文件上传、自动分页迭代器或自动重试。
- operation manifest 防止接口遗漏，但业务语义仍依赖端到端断言。
