# Alpha 安全、回归与发布门禁设计说明

## 1. 目标

本说明定义 M4 的验证边界。目标不是把 Agent Factory 描述为可直接暴露公网的生产平台，而是让当前本地 Alpha 的公共契约、信任边界、事务不变量和发布制品具备可自动回归的证据。

设计原则：

1. 只测试当前存在的能力和攻击面，不为尚未实现的文件、网络或 shell 工具制造虚假安全结论。
2. 拒绝路径必须证明受保护业务或 handler 未执行，不能只断言状态码。
3. 安全日志、业务审计和排错日志职责分离，三者不得保存凭据或大段不可信内容。
4. 回归快照只冻结公共契约和稳定语义，不冻结无意义的时间、随机 ID 或内部布局。
5. 源码测试、构建归档和隔离安装分别提供不同证据，不允许用其中一项替代全部门禁。

## 2. 信任边界与数据分类

| 边界 | 不可信输入 | 可信来源 | 必须验证 |
| --- | --- | --- | --- |
| REST | Header、path、query、JSON body | Authenticator 生成的 Principal | body 上限、DTO、认证、授权、correlation |
| SDK | 服务端响应、网络错误 | Client 本地 Token 与调用参数 | status、ErrorResponse、Schema、correlation |
| Factory Tool | 模型 arguments | 宿主 FactoryToolCallContext | 工具存在、Principal 权限、Pydantic input |
| Runtime | 模型工具申请、模型最终输出 | 持久化 AgentSpec、当前 head、固定 Registry | revision、checksum、grant、version、input/output |
| SQLite | JSON payload、投影、历史 migration | Repository codec 与 migration history | checksum、projection、外键、事务 |
| 发布制品 | wheel metadata 与 package data | 锁文件、构建配置、CI 清单 | 模块、SQL、extras、entry point、可启动性 |

敏感数据分级：

- S1 凭据：Bearer Token、模型 API key。不得进入响应、日志、审计、异常或快照。
- S2 指令与知识：system prompt、完整知识正文、工具原始参数和结果。默认不得进入日志或业务审计；只允许在明确运行内存中使用。
- S3 来源标识：checksum、revision、实体 ID、工具版本、correlation ID。可进入审计和回归投影。
- S4 公开契约：OpenAPI、JSON Schema、稳定错误码和权限名。必须可快照和评审。

## 3. 测试分层

```text
tests/unit       纯模型、策略、输入输出和错误不变量
tests/integration SQLite、UoW、并发、Runtime、重启恢复
tests/contract   HTTP、OpenAPI、SDK 与依赖方向
tests/security   跨入口拒绝、脱敏、能力清单和默认无外网
tests/regression 公共快照与固定语义投影
```

M4 不迁移已有测试来制造目录数量。`tests/security` 与 `tests/regression` 只承载需要集中表达的新不变量，并通过证据矩阵引用已有覆盖。

## 4. 公共契约快照

### 4.1 OpenAPI

`docs/generated/openapi-v1.json` 使用规范 JSON 编码：UTF-8、键排序、两空格缩进、文件末尾换行。生成器从 `create_app().openapi()` 获取 Schema，不启动网络，不读取真实 Token。

回归测试比较内存 Schema 与快照解析后的对象，同时单独验证：

- SDK manifest 与 OpenAPI method/path 集合相等；
- 健康检查没有 Bearer security requirement；
- 其他公开 operation 均声明 Bearer security；
- ErrorResponse 与 correlation Header 仍属于公开契约。

快照变更提交必须说明：

- PATCH：描述、示例或不改变兼容性的约束修正；
- MINOR：新增向后兼容 operation、可选字段或枚举能力；
- MAJOR：删除/重命名 operation、增加必填字段或改变稳定错误语义。

### 4.2 稳定语义投影

AgentSpec 和审计快照不得简单删除所有动态字段后比较“相似 JSON”。投影必须显式列出保留字段：

- AgentSpec：schema version、revision、prototype/knowledge/tree checksum、active nodes、工具 name/version/permission、output schema 与 spec checksum。
- Audit：event type、entity type、entity revision、actor、correlation 关系和稳定 payload keys；时间与事件 UUID 使用固定测试端口或显式占位。

规范化函数本身需要单元测试。生成两次必须字节一致，任何字段删除都需要评审其是否削弱可追溯性证据。

## 5. API 与认证安全

安全测试必须覆盖：

- 未配置认证为 503，缺失/错误凭据为 401，有身份但权限不足为 403；
- `X-Actor-ID`、body actor 和模型 Principal 均不能成为第二身份来源；
- Authentication/Authorization 在 Controller 前失败；
- declared `Content-Length` 和 chunked body 使用同一字节上限；
- 非法 content length、correlation、JSON 与额外字段返回稳定脱敏错误；
- 响应携带 `Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`；
- 失败日志只包含事件类型、correlation、凭据是否存在和安全异常类别，不包含 Token、请求体或候选摘要。

M4 只增加结构化安全事件日志，不实现内存限流并将其包装成生产防护。公网限流需要共享状态、代理边界、真实客户端地址策略、告警与容量数据，进入 Productionization 里程碑后单独设计。

## 6. Runtime 与工具安全

当前默认能力清单必须精确为固定只读 `document-search@1.0.0`。测试断言 Registry 没有动态注册接口，默认工具不持有文件、网络、shell 或外部写权限。

Executor 拒绝矩阵继续以以下顺序为权威：

```text
持久化 Spec -> 当前 RUNNING head -> request identity
-> Spec grant -> version -> Registry metadata
-> Pydantic input -> timeout handler -> Pydantic output
```

安全测试除错误码外还要断言：

- 未授权、版本错误和非法输入时 handler 调用计数为零；
- 记录与审计不保存 arguments/output 正文；
- handler/provider 意外异常只暴露稳定错误和异常类型；
- 默认离线 Runtime 与 fake gateway 测试不创建外部 socket。

如果未来注册文件工具，路径规范化、绝对路径、`..`、符号链接和工作区逃逸测试成为前置门禁。如果未来注册网络工具，loopback、link-local、RFC1918、IPv6、DNS rebinding 和重定向测试成为前置门禁。当前没有对应 handler，因此 M4 不宣称这些尚不存在的能力已经安全。

## 7. 事务故障证据

每项写能力在矩阵中记录：operation、主实体、是否改变 revision、审计事件、幂等 operation 名和现有测试。缺口按风险增加以下故障点：

```text
领域计算完成但未写入
实体/snapshot 写入后、审计前
审计写入后、幂等结果前
commit 时
```

断言同时查询：

- 当前 head 与历史 snapshot；
- 主实体或不可变报告；
- AuditEvent；
- IdempotencyRecord；
- 必要的外键和 checksum 投影。

故障注入优先通过测试专用 UoW/Repository wrapper 实现，不在公开 Settings、Command 或 Repository API 中加入 `fail_after_*` 参数。

## 8. 发布与进程 smoke

源码环境和 wheel 环境必须分开：

1. 在工作区完成静态检查、业务测试和 branch coverage。
2. 构建 sdist/wheel并检查归档资源。
3. 从 `uv.lock` 导出不含项目本体的运行依赖。
4. 在工作区内的临时目录创建隔离环境，安装依赖。
5. 使用 `--no-deps` 安装刚构建的 wheel，防止重新解析和 editable 源码遮蔽。
6. 从隔离解释器启动独立 Uvicorn，数据库和日志位于临时目录。
7. 经 loopback TCP 轮询 readiness，使用 SDK 完成认证读取或最小写操作。
8. 正常停止、重启并读取持久化事实，最后强制清理残留进程。

smoke 必须限制启动时长、请求时长和总时长；日志扫描使用测试 Token 原文和已知敏感 fixture。它不访问外部网络，也不验证 TLS、反向代理或公网地址。

本地 C 盘空间不足时，隔离环境、build、pytest 临时目录和 uv cache 必须显式放在仓库的 `.tmp` 或其他 E 盘目录，不新增 C 盘依赖安装。

## 9. CI 门禁

阻断性门禁顺序：

1. `uv sync --locked`；
2. Ruff format/check；
3. mypy strict；
4. 全部分层测试与 branch coverage；
5. domain/application/total 90%/85%/80% 阈值；
6. 契约与回归快照检查；
7. sdist/wheel 构建和资源清单；
8. minimal 与 optional extras 隔离安装；
9. 独立 Uvicorn/loopback HTTP smoke。

外部漏洞数据库、第三方可用性和真实模型调用不进入默认阻断门禁。未来可建立独立定时依赖审计，但其失败必须包含版本、公告和处置结论，不能用不稳定网络结果替代可重复测试。

## 10. 证据边界

M4 通过后可以表述为：

> Agent Factory Alpha 的公共契约、当前安全边界、事务原子性和发布制品经过自动化回归，并能从隔离 wheel 在本地独立进程启动。

不得表述为：

> Agent Factory 已完成公网生产安全、支持多用户/多租户、可执行任意工具，或具备分布式高可用能力。
