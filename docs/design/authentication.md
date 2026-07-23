# M3.1 身份与授权设计说明

## 1. 目标与边界

M3.1 用最小可信身份替换 M1/M2 的自报 `X-Actor-ID`。它解决三个问题：

- 写命令中的 actor 必须来自服务端验证过的凭据，不能由请求任意填写。
- Prototype、Suite、Tree 等读接口和审计查询必须具有显式授权边界。
- REST 与未来 Factory Tool adapter 必须共享同一种 `Principal` 和权限语义。

当前实现是单主体、配置驱动的 Alpha 静态 Bearer Token，不是完整生产身份系统。OAuth/OIDC、JWT、多用户目录、Token 轮换、撤销、租户隔离、限流和公网部署安全不属于 M3.1。

## 2. 分层结构

```text
Authorization: Bearer <opaque token>
                │
                ▼
FastAPI HTTPBearer / get_principal
                │
                ▼
Authenticator Protocol ──► StaticBearerAuthenticator
                │                    │
                │                    └─ SHA-256 + compare_digest
                ▼
Principal(subject, roles)
                │
                ▼
AuthorizationPolicy.require(permission)
                │
                ├─ GET factory definitions ──► FACTORY_READ
                ├─ POST business actions ────► FACTORY_WRITE
                └─ GET audit events ─────────► AUDIT_READ
                │
                ▼
Router 将 principal.subject 写入既有 Command.actor
                │
                ▼
FactoryController / AuditEventFactory
```

模块职责：

| 模块 | 职责 |
| --- | --- |
| `application/security.py` | `Principal`、Role、Permission、Authenticator Protocol、纯授权策略 |
| `infrastructure/authentication.py` | 静态 Token 与未配置时的 fail-closed 适配器 |
| `settings.py` | SecretStr Token、主体和角色配置校验 |
| `container.py` | 组装 Principal、Authenticator 和 AuthorizationPolicy |
| `interfaces/api/dependencies.py` | Bearer 解析、401/503、旧 Header 拒绝和路由权限依赖 |
| `interfaces/api/errors.py` | 403 映射、401 `WWW-Authenticate` 与统一错误 envelope |
| `routers/*.py` | 选择读/写/审计权限，并把 `principal.subject` 转为 Command.actor |

认证代码不访问 Repository，不创建业务对象，也不修改 Controller 事务。授权规则不导入 FastAPI，未来 Tool adapter 可以直接复用。

## 3. 身份与权限模型

```python
class FactoryRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    ADMIN = "admin"


class FactoryPermission(StrEnum):
    FACTORY_READ = "factory:read"
    FACTORY_WRITE = "factory:write"
    AUDIT_READ = "audit:read"


class Principal(FrozenModel):
    subject: Actor
    roles: Annotated[frozenset[FactoryRole], Field(min_length=1)]
```

角色矩阵固定为：

| Role | FACTORY_READ | FACTORY_WRITE | AUDIT_READ |
| --- | --- | --- | --- |
| `viewer` | 是 | 否 | 否 |
| `operator` | 是 | 是 | 否 |
| `auditor` | 是 | 否 | 是 |
| `admin` | 是 | 是 | 是 |

多角色 Principal 取权限并集。权限不足抛出传输无关的 `AuthorizationDeniedError(code="AUTHORIZATION_DENIED")`；REST 映射为 403，未来 Tool adapter 可映射为自己的错误 envelope。

M3.1 不新增“reviewer”等细角色。人工复核、晋升和降级均属于 `FACTORY_WRITE`；出现真实职责分离需求后再扩展 permission，而不是在 Router 中散落角色名称判断。

## 4. 静态 Bearer 认证

配置字段：

```python
auth_token: SecretStr | None = Field(
    default=None,
    min_length=32,
    max_length=4_096,
)
auth_subject: Actor = "local-owner"
auth_roles: frozenset[FactoryRole] = frozenset({FactoryRole.ADMIN})
```

`StaticBearerAuthenticator.from_secret()` 立即计算配置 Token 的 SHA-256，只在对象中保存 32-byte digest 和不可变 Principal。每次请求对候选 Token 计算相同 digest，并使用 `hmac.compare_digest()` 比较。这样避免普通字符串提前退出比较，也避免 Authenticator 的 repr 保存明文。

进程中的 `Settings` 仍持有掩码 `SecretStr`，因此 M3.1 不能声称密钥已经进入 HSM 或外部 secret manager。日志、异常、审计、HTTP details 和 Authenticator repr 均不得包含 Token；本地开发可使用未提交的 `.env`，其他环境应通过环境变量或外部 secret manager 注入，均不得提交真实值。

## 5. Fail-closed 与 readiness

没有配置 `AGENT_FACTORY_AUTH_TOKEN` 时，Container 使用 `UnavailableAuthenticator`：

| 请求 | 结果 |
| --- | --- |
| `GET /health/live` | 200，证明进程可响应 |
| `GET /health/ready` | 503 `SERVICE_NOT_READY` |
| 任意业务 API | 503 `AUTHENTICATION_NOT_CONFIGURED` |

`Container.start()` 仍然只在 migration 成功后设置内部 `_ready=True`；公开 `Container.ready` 现在要求 `_ready and authenticator.ready`。因此数据库完成迁移但认证缺失时，进程存活却不能进入服务状态。关闭 Container 后 `_ready=False`，不会因认证器仍可用而错误返回 ready。

这一设计比硬编码默认 Token 或自动降级匿名模式更保守。模块级 `app = create_app()` 仍可被导入和启动，便于暴露 liveness 与配置错误，但业务能力不会无认证开放。

## 6. HTTP 契约

FastAPI 使用 `HTTPBearer(auto_error=False, scheme_name="BearerAuth")`，让接口自己生成统一错误 envelope，并在 OpenAPI 中产生 HTTP Bearer security scheme。

| 场景 | HTTP | Error code | 附加 Header |
| --- | ---: | --- | --- |
| 服务端未配置 Token | 503 | `AUTHENTICATION_NOT_CONFIGURED` | 无 |
| 缺少 Bearer | 401 | `AUTHENTICATION_REQUIRED` | `WWW-Authenticate: Bearer` |
| Token 不匹配 | 401 | `AUTHENTICATION_FAILED` | `WWW-Authenticate: Bearer` |
| 权限不足 | 403 | `AUTHORIZATION_DENIED` | 无 |
| 提交旧 `X-Actor-ID` | 400 | `ACTOR_HEADER_NOT_ALLOWED` | 无 |

401/403/503 继续返回 `ErrorResponse`，并复用请求 correlation ID。错误中不回显 Authorization header、候选 Token、配置 Token、主体角色集合或原始请求体。

`X-Actor-ID` 被显式拒绝而不是静默忽略，避免旧客户端误以为该值仍能改变审计身份。成功写命令的 actor 只能来自 `Principal.subject`；现有审计表和事件模型无需 migration。

## 7. 路由授权

- `/health/live`、`/health/ready`：公开，不声明 OpenAPI security。
- Prototype 列表、EvaluationSuite 查询、SkillTree 查询：`FACTORY_READ`。
- 所有生产和治理 POST：`FACTORY_WRITE`。
- AuditEvent 查询：`AUDIT_READ`。

每个公开非健康 operation 都通过具体依赖声明权限，而不是依赖 UI 隐藏按钮。OpenAPI 契约测试遍历全部 GET/POST/PUT/PATCH/DELETE，要求业务 operation 的 `security` 精确等于 `[{"BearerAuth": []}]`。

## 8. 验证证据

- `tests/unit/application/test_security.py` 覆盖 4 × 3 角色矩阵、多角色并集、稳定拒绝和空角色校验。
- `tests/unit/test_authentication.py` 覆盖正确、错误、空 Token、repr 脱敏和未配置适配器。
- `tests/contract/test_api_authentication.py` 覆盖 fail-closed、401/403/503、旧 Header、可信审计 actor、OpenAPI 和双 App Token 隔离。
- M1/M2 REST 主链全部改用 Authorization Bearer，并继续验证重启恢复、幂等和审计。
- M3.1 完整本地回归为 `193 passed`；branch coverage 为 domain 96%、application 93%、total 94%。
- Ruff format/lint 与 mypy strict 对 93 个源码/测试文件通过。
- sdist/wheel 构建通过，wheel 已核对包含 `application/security.py` 和 `infrastructure/authentication.py`。

## 9. 已知限制

- 一个进程配置只对应一个静态 Token 与 Principal，不支持多用户并发身份。
- Token 没有轮换、撤销、过期和服务端持久化机制。
- 认证失败不进入业务审计表；M4 需单独设计安全日志、限流和告警。
- HTTP Bearer 不替代 TLS；明文 HTTP 上发送 Token 不安全。
- 当前授权只保护 FastAPI 路由。M3.4 Tool adapter 必须接收宿主已经认证的 Principal，并复用同一 AuthorizationPolicy。
- 当前服务仍使用单机 SQLite，不能因增加认证而宣称具备生产吞吐、租户隔离或公网部署能力。
