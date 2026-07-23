# M3：接口、运行时与演示

## 1. 阶段状态

- 状态：进行中。
- 开始时间：2026-07-23。
- 进入依据：M2 已由项目 owner 人工验收并封存；封存提交 `da4b408` 已推送，GitHub Actions CI #17 通过。
- 规划依据：项目 owner 于 2026-07-23 确认本阶段目标、关键取舍、工作包、风险和验收标准后进入 M3。
- 退出决策：本文第 6 节证据齐备后，由项目 owner 人工决定是否结束 M3 并进入 M4。

## 2. 阶段目标

在 M2 的确定性生产与技能治理闭环上增加接口、受限运行和可视化演示能力：

```text
                         ┌─ REST
调用方 ── Principal ─────┼─ Python SDK ───► FactoryController ───► SQLite / Audit
                         └─ Factory Tool adapter

AgentSpec + 已校验 INLINE 知识 ──► DemoRuntimeAdapter ──► RunResult
                                      │
                                      └─ 仅调用 AgentSpec 明确授权的只读工具
```

M3 验证“同一套工厂能力能否通过三种入口保持相同业务语义，以及标准化 AgentSpec 能否被受限 Runtime 消费”。M3 继续采用生产级工程标准，但仍是 Alpha：它不把静态 Token、固定 Demo Runtime 或单机 SQLite 描述为完整生产部署能力。

## 3. 范围与边界

M3 必须实现：

- `Principal`、角色、认证端口和 Alpha 静态 Bearer Token 适配器；业务 actor 由认证主体产生，不再信任客户端自报身份。
- 最小授权矩阵、稳定 401/403 错误、日志与错误响应中的凭据脱敏。
- `LifecyclePolicy`、`TransitionInstanceCommand`、实例 revision CAS、审计、幂等和 REST 状态迁移接口。
- 异步 Python SDK，覆盖全部公开 REST operation，并保留业务错误码、HTTP 状态、details 与 correlation ID。
- 面向 Agent 的工厂工具适配器：`list_prototypes`、`clone_agent`、`bind_knowledge`、`apply_promotion`、`query_audit_log`。
- `RuntimeAdapter`、`RunRequest`、`RunResult`、运行时知识输入和工具调用契约。
- 可执行 `ToolRegistry`、输入/输出净化、授权与版本核对、超时、脱敏调用记录，以及固定只读 `document-search` handler。
- 默认离线、确定性的 Demo Runtime；可选 OpenAI 官方 SDK 适配器只用于人工演示。
- Gradio 演示界面，通过 SDK 完成生产操作，通过 Runtime Adapter 执行固定 Writer 任务。
- 三入口同构、生命周期、工具安全、运行时、完整 Demo、重启恢复和发布制品测试。

M3 不实现：

- OAuth/OIDC、JWT、多用户目录、Token 轮换、撤销、细粒度租户隔离或公网部署安全承诺。
- shell、动态 Python、`eval`/`exec`、任意文件系统、任意网络访问和 `WRITE_EXTERNAL` 工具。
- Docker 沙箱、分布式执行、多进程调度、任务队列或远程 checkpoint。
- LangGraph、AutoGen、CrewAI 等生产 Runtime 的正式适配器。
- Anthropic 与多模型路由；M3 只保留可替换端口，并最多实现一个可选 OpenAI 演示适配器。
- RETRIEVAL 向量库、长期记忆和对话历史持久化。
- 把真实 LLM 调用加入默认测试、CI 或阶段退出条件。
- Agent 语义质量、模型稳定性或安全公网部署已经得到保证的结论。

## 4. 已确认设计决定

### 4.1 先建立最小可信身份，再实现 Tool adapter

M2 的 `X-Actor-ID` 只是调用方自报的审计标签。M3 引入 `Principal` 和 `Authenticator`，由静态 Bearer Token 适配器在接口边界生成主体；Controller 继续只接收传输无关的 actor 字符串。健康检查保持公开，其他路由按角色授权。

该适配器只解决本地 Alpha 的最小信任来源，不承担用户目录、第三方身份联合、密钥生命周期或公网防护。M4 在此基础上补齐完整安全与回归门禁。

### 4.2 生命周期是 Runtime 演示的前置业务能力

演示不能只在页面内伪造 `RUNNING` 或 `WAITING`。M3.2 先实现领域状态表、显式 transition 命令、expected revision、幂等和审计，再允许 Runtime 演示使用这些状态。Runtime Adapter 不直接修改数据库。

### 4.3 SDK 走 HTTP，Factory Tool adapter 直接复用 Controller

SDK 使用 HTTPX 调用真实 REST 契约，以验证 URL、Header、DTO、错误 envelope、超时和 correlation ID。Factory Tool adapter 直接把 Pydantic 输入转换为现有 application Command 并调用 `FactoryController`，不经过 HTTP，也不访问 Repository。

两条路径刻意不同：SDK 证明远程客户端契约，Tool adapter 证明非 HTTP 入口可以共享相同业务规则。二者都不得重新实现知识槽、revision、晋升或审计策略。

### 4.4 三入口用同一幂等命令证明完全同构

写操作的同构测试使用同一 Principal、同一 application command 和同一 idempotency key。REST 首次执行后，SDK 与 Tool adapter 必须精确重放同一个领域对象，包含服务端生成的 ID、时间和 checksum，并且审计数量不增加。只比较去掉动态字段后的“相似 JSON”不足以证明同构。

### 4.5 离线 Runtime 是验收基线，真实模型只是可选演示

默认 `DeterministicDemoRuntimeAdapter` 使用固定输入和显式知识生成可重复 `RunResult`，不访问网络。可选 OpenAI adapter 只通过官方 SDK 和受控端口调用模型；测试注入 fake gateway，真实调用由人工显式启用，不进入 CI。

### 4.6 Writer 场景优先于可执行 Engineer 场景

M3 固定 Writer 演示，用于展示知识绑定、AgentSpec 消费、生命周期、评估与晋升。真正执行 Engineer 代码需要 shell、文件系统和沙箱，会突破本阶段安全边界；在隔离与权限模型单独验收前不实现。

### 4.7 可执行工具与工厂工具是两个不同边界

Factory Tool adapter 把工厂能力暴露给上层 Agent；`ToolExecutor` 则运行生产出的 AgentSpec 已授权工具。前者转换 application Command，后者执行受限副作用。两者模型、权限与审计不能混用。

## 5. 实施工作包

### M3.1 身份与授权基础（本地提交完成，待推送与远程 CI）

- 定义不可变 `Principal`、稳定角色和 `Authenticator` Protocol。
- 实现配置驱动的静态 Bearer Token 适配器，使用常量时间比较，不把 Token 写入日志、异常、审计或 repr。
- 健康检查保持公开；其余路由使用认证依赖，actor 统一来自 `Principal.subject`。
- 建立最小角色矩阵：读取、生产操作、审计查询和管理操作分别测试允许与拒绝路径。
- 增加 401/403 统一错误 envelope、OpenAPI security scheme 和认证上下文隔离测试。
- 更新 M1/M2 REST 测试，证明业务行为不因身份来源变化而回退。

### M3.2 生命周期与 Runtime 契约（本地提交完成，待推送与远程 CI）

- 实现纯 `LifecyclePolicy` 和完整允许迁移表。
- 增加 `TransitionInstanceCommand`、Controller 事务、实例 snapshot/head CAS、审计与 typed idempotency replay。
- `CREATED -> RUNNING` 前确认当前 revision 可导出 AgentSpec；`FAILED -> RUNNING` 要求显式 retry。
- 增加 REST transition action，并固定非法迁移、空 reason、revision 冲突和终态行为。
- 定义 `RuntimeContextRef`、`RunRequest`、`RunResult`、`ResolvedRuntimeKnowledge` 与 `RuntimeAdapter` Protocol；本工作包不调用模型。

### M3.3 Python SDK（本地提交完成，待推送与远程 CI）

- 实现异步 `AgentFactoryClient`、显式 close 和 async context manager。
- 覆盖健康检查、原型、知识、实例、AgentSpec、技能树、评估、复核、晋升、观察结果、生命周期与审计全部公开 operation。
- 统一 Bearer auth、idempotency key、correlation ID、请求超时和分页参数。
- 非 2xx 响应解析为 `AgentFactoryApiError`，保留业务 code、status、details 和 correlation ID；非标准响应安全降级。
- 使用 ASGITransport 与真实 FastAPI app 进行 SDK 契约测试；通过 operation manifest 与 OpenAPI 比对防止方法遗漏。

### M3.4 Factory Tool adapter（本地门禁已通过，待提交与远程 CI）

- 为五个已确认工具定义严格 Pydantic 输入/输出模型并导出 JSON Schema。
- Tool 调用上下文携带由宿主认证的 `Principal` 和 correlation ID；工具输入不得提交 actor。
- 每个工具只构造现有 Command/Query 并调用 Controller，不访问 FastAPI、Repository、clock 或 ID generator。
- 领域错误转换为稳定 Tool error envelope，不暴露 traceback 或敏感输入。
- 使用与 REST/SDK 相同的命令和幂等 key 验证精确对象重放与审计不重复。
- 不在 M3 实现 MCP server；MCP 仍是未来协议适配层。

### M3.5 Runtime 与安全工具执行

- 定义 `ToolDefinition`、`RegisteredTool`、`ToolCallRequest`、`ToolCallRecord` 和 `ToolRegistry`。
- `ToolExecutor` 核对 instance/revision、AgentSpec 授权、工具版本和 permission tags，再执行 Pydantic 输入净化、timeout 与输出校验。
- 实现固定只读 `document-search`，只检索显式提供且 checksum 与 AgentSpec 一致的 INLINE 知识；不访问任意路径或网络。
- 工具调用只持久化状态、耗时、错误码及输入/输出哈希；原始参数、结果、知识正文和凭据不得进入审计。
- 实现默认离线 Demo Runtime；可选 OpenAI adapter 通过可替换 gateway 使用官方 SDK，测试只使用 fake gateway。
- 在代码前补充 Runtime/Tool design note，并根据最终持久化需求决定 forward-only migration，不预改既有 migration。

### M3.6 Gradio 演示

- Gradio 只依赖 SDK、Runtime 接口和演示 DTO，不导入 domain、Controller 或 Repository。
- 固定 `technical-writer@1.0.0`、`agent-factory-docs@1.0.0`、`writer-skills@1.0.0` 与 `mid-writer-suite@1.0.0` 数据。
- 从空库完成 Suite/Tree/Prototype/Knowledge 注册、发布、克隆、绑定、Spec 导出、状态迁移、运行、评估、复核或显式晋升及审计查询。
- 页面展示 instance revision、Prototype/Knowledge/SkillTree 来源、active nodes、RunResult 和审计时间线。
- 页面只显示稳定错误码、message 和 correlation ID，不显示 Python traceback、Token、Prompt 或完整知识正文。
- Demo 依赖保持 optional extra；未安装时核心 API、SDK 和测试仍可工作。

### M3.7 同构与退出验收

- 从空文件型 SQLite 通过公开入口完成固定生产与运行主链，关闭并重建应用后恢复状态与审计。
- REST、SDK、Factory Tool adapter 对相同幂等命令返回精确相同对象，且只产生一次业务副作用和审计。
- Runtime 与工具测试覆盖 checksum、未授权、版本不匹配、非法输入、超时、handler 异常、非法输出和脱敏。
- 生命周期测试覆盖全部允许边、主要非法边、终态、重试、并发 CAS、幂等与事务回滚。
- Gradio 使用固定 viewport 做基础截图检查；真实模型模式只做可选人工 smoke test。
- Ruff、mypy strict、pytest、branch coverage、sdist/wheel、optional extras 安装与 GitHub Actions 全部通过。

## 6. 验收标准

- [x] 非健康 REST 路由不再信任 `X-Actor-ID`，actor 来自认证后的 Principal。
- [x] 未配置认证、无凭据、错误凭据和角色不足分别返回稳定 503/401/403，且不泄露 Token。
- [x] 生命周期全部允许迁移和关键非法迁移有纯策略测试。
- [x] 状态变更具有 revision CAS、幂等、审计、失败原子性和并发单胜证据。
- [x] SDK 覆盖全部公开 OpenAPI operation，并完整保留业务错误信息。
- [x] 五个 Factory Tool adapter 只做 DTO/Command 转换，不复制业务策略。
- [x] REST、SDK、Tool adapter 使用相同幂等命令时返回精确相同对象且不重复审计。
- [ ] ToolExecutor 拒绝未授权、版本不匹配和非法输入，超时与失败均有脱敏记录。
- [ ] Demo Runtime 校验 AgentSpec、revision 和知识 checksum，默认运行不访问网络。
- [ ] Gradio 不导入 domain/Repository，并能完成固定 Writer 主链。
- [ ] 关闭并重建应用后，M3 状态、调用记录和审计可恢复。
- [x] M1/M2 全部回归测试继续通过。
- [x] 默认测试和本地质量门禁不需要模型 API key 或互联网访问。
- [x] domain、application 和全项目 branch coverage 分别不低于 90%、85% 和 80%。
- [ ] Ruff、mypy strict、pytest、sdist/wheel、optional extras 与 M3 退出候选 GitHub Actions 全部通过。

## 7. 验收证据矩阵

| 验收面 | 计划证据 |
| --- | --- |
| Principal、认证与授权 | security unit/contract tests |
| 生命周期与 revision | domain unit + Controller concurrency tests |
| SDK 与 REST 等价 | ASGITransport SDK contract tests |
| Factory Tool adapter | schema/unit + cross-adapter integration tests |
| Runtime 契约 | offline adapter unit tests |
| 工具执行安全 | executor failure matrix + persistence tests |
| 三入口同构 | exact idempotent replay integration test |
| Gradio 演示 | deterministic smoke test + viewport screenshot |
| 重启恢复主链 | file-backed SQLite end-to-end test |
| 覆盖率、构建与资源 | CI workflow 与 wheel 内容检查 |

## 8. 已知风险与处理

| 风险 | M3 处理 | 重新评估条件 |
| --- | --- | --- |
| 认证改动影响既有 REST 客户端 | 单独 M3.1、统一依赖、完整回归，不保留双重 actor 真相 | 出现必须兼容的外部客户端 |
| 静态 Token 被误解为生产认证 | 文档标注 Alpha、禁止公网承诺、M4 单独安全验收 | 需要多用户、轮换或第三方身份 |
| SDK 方法随 REST 漂移 | OpenAPI operation manifest 契约测试 | 引入生成式 SDK 后重新评估维护方式 |
| 三入口只做到字段近似相等 | 同 Principal/Command/Key 精确幂等重放 | 某入口确有不同业务语义 |
| Live LLM 造成测试不稳定 | 默认离线 adapter，真实模型仅人工 smoke | 模型调用进入验证实验阶段 |
| ToolExecutor 扩大副作用面 | 只读固定 handler、无动态注册、无文件/网络/shell | 沙箱与权限模型单独通过评审 |
| Gradio 侵入核心层 | 强制经 SDK/Runtime port，增加 import boundary test | Demo 需要专用聚合查询 API |
| M3 范围较大 | M3.1-M3.7 独立设计、测试和提交 | 单个工作包仍难以评审时继续拆分 |

## 9. 阶段报告

当前状态：M3.1-M3.3 已完成本地提交；M3.4 已完成代码、设计说明和完整本地质量门禁、尚待提交；M3 工作包均尚未推送或取得远程 CI 证据。M3.4 提供五项 provider-neutral Factory Tool，通过可信宿主上下文复用现有 Controller，不实现 MCP Server 或 Agent 业务工具执行器。

- M2 封存提交：`da4b408 docs: close M2 milestone`。
- M2 封存远程证据：GitHub Actions [`CI #17`](https://github.com/1471436961/agent-factory/actions/runs/29930708726) 通过。
- M3 规划确认时间：2026-07-23。
- M3.1 本地提交：`5af129b feat: establish M3.1 identity boundary`。
- M3.1 代码边界：新增 `Principal`、角色/权限矩阵、`Authenticator` Protocol、静态/未配置认证适配器和 FastAPI 权限依赖；未新增数据库 migration。
- M3.1 信任边界：健康检查公开；未配置 Token 时 live 为 200，ready 与业务路由为 503；写命令 actor 只来自 `Principal.subject`；客户端提交 `X-Actor-ID` 会被拒绝。
- M3.1 契约证据：覆盖未配置认证、缺失/错误凭据、错误 scheme、四角色矩阵、审计主体、OpenAPI security requirement、Token 脱敏和双 app 上下文隔离。
- M3.1 本地测试：`193 passed`，相较 M2 封存基线增加 30 个测试且既有回归继续通过。
- M3.1 覆盖率：domain 96%、application 93%、全项目 94%，均为 branch coverage。
- M3.1 静态门禁：Ruff format/check 通过，mypy strict 通过 93 个 source/test 文件。
- M3.1 构建门禁：sdist 与 wheel 构建通过，wheel 包含新增 security 与 authentication 模块。
- M3.2 代码边界：新增纯 `LifecyclePolicy`、transition command/Controller/REST action、`instance.transitioned` 审计和 `RuntimeContextRef`、`ResolvedRuntimeKnowledge`、`RunRequest`、`RunResult`、`RuntimeAdapter` 契约；未新增数据库 migration。
- M3.2 状态所有权：通用 transition 不得进入 `DEGRADED`，该状态只由 M2 降级证据链产生；`COMPLETED` 与 `TERMINATED` 均为终态。
- M3.2 事务证据：真实 SQLite 测试覆盖 typed idempotency、revision CAS、双 Container 并发单胜、审计失败整体回滚和进程重建后的状态恢复与精确重放。
- M3.2 REST 证据：覆盖认证、授权、非法/伪造输入脱敏、OpenAPI security、幂等重放、重启恢复、显式 Spec 导出及通用迁移不能伪造降级。
- M3.2 运行边界：transition 只记录治理状态，不证明外部 Runtime 已真实启动或停止；本工作包只定义 Runtime 契约，不实现执行器、租约、heartbeat 或 checkpoint。
- M3.2 本地测试：`265 passed`，相较 M3.1 基线增加 72 个测试，M1/M2/M3.1 回归继续通过。
- M3.2 覆盖率：domain 96%、application 94%、全项目 94%，均为 branch coverage。
- M3.2 静态门禁：Ruff format/check 通过，mypy strict 通过 99 个 source/test 文件。
- M3.2 构建门禁：sdist 与 wheel 构建通过；wheel 已核对包含 `application/runtime.py`、`domain/services/lifecycle.py` 和 001-005 全部 migration。
- M3.2 本地提交：`1d9fc94 feat: add M3.2 lifecycle and runtime contracts`。
- M3.3 代码边界：新增公开 `agent_factory.sdk` 包、不可变 20-operation manifest、异步 `AgentFactoryClient` 和 API/Transport/Protocol/Closed 四类稳定异常；未新增数据库 migration。
- M3.3 传输语义：SDK 始终走 HTTP，复用 REST request model，统一 Bearer、timeout、correlation、幂等和分页；不发送 `X-Actor-ID`，不自动重试，不保存共享的 last-response 状态。
- M3.3 契约证据：manifest 与真实 OpenAPI 的 20 个 method/path 精确相等；ASGITransport + FastAPI lifespan + 文件 SQLite 完成全部 operation，并验证自定义 prefix、重复 query key、精确幂等重放和业务错误保真。
- M3.3 安全证据：非标准响应不复制正文，Transport 异常不复制底层文本，Token 不进入 Client repr 或 SDK 异常；2xx 非 JSON/错误 Schema 和 correlation 冲突均作为协议错误拒绝。
- M3.3 本地测试：`287 passed`，相较 M3.2 基线增加 22 个测试，M1-M3.2 回归继续通过。
- M3.3 覆盖率：domain 96%、application 94%、SDK 93%、全项目 94%，均为 branch coverage。
- M3.3 静态门禁：Ruff format/check 通过，mypy strict 通过 106 个 source/test 文件。
- M3.3 构建门禁：sdist 与 wheel 构建通过；wheel 已核对包含四个 `agent_factory/sdk` 模块和 001-005 全部 migration。
- M3.3 本地提交：`fa5326b feat: add M3.3 async Python SDK`。
- M3.4 代码边界：新增 `agent_factory.interfaces.factory_tools` 包与 Container 装配；五个工具只依赖 Controller、`AuthorizationPolicy` 和 `CorrelationContext`，未新增数据库 migration、MCP Server 或 provider 方言。
- M3.4 信任边界：`Principal`、request/correlation ID 和幂等键只存在于宿主提供的 `FactoryToolCallContext`；模型可见 Schema 不含 actor、Principal 或上下文字段，调用鉴权先于详细参数校验。
- M3.4 契约证据：输入/输出 JSON Schema 由 Pydantic 模型生成；统一结果 envelope 覆盖未知工具、输入错误、领域错误、输出漂移和意外异常，非法参数、异常文本与 traceback 不进入结果。
- M3.4 集成证据：真实 SQLite 完成列表、克隆、知识绑定、晋升和审计五项操作；REST、SDK、Factory Tool 使用同一幂等命令返回精确相同 `AgentInstance`，`instance.cloned` 审计仅产生一次。
- M3.4 本地测试：`303 passed`，相较 M3.3 基线增加 16 个测试，M1-M3.3 回归继续通过。
- M3.4 覆盖率：domain 96%、application 94%、Factory Tool 100%、全项目 94%，均为 branch coverage。
- M3.4 静态门禁：Ruff format/check 通过，mypy strict 通过 112 个 source/test 文件。
- M3.4 构建门禁：sdist 与 wheel 构建通过；wheel 已核对包含三个 `agent_factory/interfaces/factory_tools` 模块。
- 未完成能力：Runtime 执行器和 Gradio 仍是后续工作包，不能描述为已有实现。
