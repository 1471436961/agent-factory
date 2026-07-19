# M1：核心生产链

## 1. 阶段状态

- 状态：进行中。
- 开始时间：2026-07-18。
- 进入依据：项目 owner 确认 M0 的本地验收、远程 CI 和提交证据通过。
- 退出决策：本文第 5 节验收证据齐备后，由项目 owner 人工决定是否进入 M2。

## 2. 目标

交付第一个可持久化、可审计、可通过自动化测试重放的 Agent 生产闭环：

```text
注册原型 -> 发布原型 -> 注册知识 -> 克隆实例
         -> 绑定知识 -> 导出 AgentSpec -> 查询审计记录
```

M1 完成后，项目应能证明“Agent 定义、来源、知识版本和导出规格可由确定性代码治理”，但不宣称 Agent 的语义输出质量已被验证。

## 3. 范围与边界

M1 必须实现：

- `AgentDefinition`、`AgentPrototype`、`DomainKnowledge`、`AgentInstance` 和 `AgentSpec` 的 Pydantic v2 模型与不变量。
- 原型注册、发布、废弃，知识注册，实例克隆、知识绑定和 `AgentSpec` 导出。
- Prototype、Knowledge、Instance snapshot/head、AgentSpec、Audit 和 Idempotency 的仓储端口与 SQLite 实现。
- 写操作的事务边界、审计事件、幂等处理和实例 revision 乐观并发控制。
- 核心生产链所需的最小 REST 路由和稳定错误码，路由只做 DTO 转换并复用 `FactoryController`。
- 单元、SQLite 集成、API 契约和完整生产链测试。

M1 不实现：

- 真实 LLM 调用、`RuntimeAdapter` 的生产实现或 Agent 任务执行。
- 技能 DAG、评估、晋升、降级和观察期，这些属于 M2。
- Python SDK、Tool adapter、Gradio 和完整对外接口覆盖，这些属于 M3。
- 多进程 migration、PostgreSQL、分布式锁、向量数据库和用户认证。

## 4. 实施批次

### M1.1 领域契约

- 建立 `FrozenModel`、`Slug`、`SemVer`、canonical JSON 与 SHA-256 公共工具。
- 实现 Agent、原型、知识、绑定、实例和 Spec 模型。
- 校验重复工具/知识槽、SemVer 范围、知识基数、JSON Schema 和 checksum。
- 建立领域异常类型与稳定错误码，领域层不引入 FastAPI。

### M1.2 端口与持久化

- 定义 repository protocol、`UnitOfWork` 和 `UnitOfWorkFactory`。
- 实现 SQLite 行到 Pydantic 快照的双向转换，JSON 使用 canonical 序列化。
- 保证 instance snapshot 与 head 原子更新；`expected_revision` 不匹配时整个事务回滚。
- 实现审计事件与业务写入同事务提交，不允许“有业务数据、无审计记录”。

### M1.3 应用服务

- 实现 `register_prototype`、`publish_prototype`、`deprecate_prototype` 和 `register_knowledge`。
- 实现 `clone_agent`、`bind_knowledge` 和 `export_spec`。
- 同一幂等键与同一请求必须重放原响应；同一键与不同请求必须返回幂等冲突。
- 导出前重新校验必填知识槽与工具权限；同一 instance revision 的 Spec 只生成并持久化一次。

### M1.4 核心 REST 契约

- 为上述操作提供最小 FastAPI 路由，HTTP 层不复制业务规则。
- 将领域异常映射为稳定的 HTTP status、`error.code`、`message`、`details` 和 `correlation_id`。
- 为请求输入、输出快照、错误响应与 readiness 回归建立 API 契约测试。

### M1.5 闭环验收

- 实现 `test_register_clone_bind_export`，从空库完成整条生产链。
- 增加原型状态、知识槽不匹配、缺失必填绑定、checksum 冲突、幂等冲突和 revision 冲突的失败路径测试。
- 执行本地质量门禁、打包验证和 GitHub Actions，将命令、结果与远程运行链接写入阶段报告。

## 5. 验收标准

- [ ] 所有 M1 Pydantic 模型的有效输入与边界失败路径有单元测试。
- [ ] 只有 `PUBLISHED` 原型可克隆，实例保留不可变的原型 ID、版本和 checksum。
- [ ] 知识绑定强制校验槽位、kind、SemVer 范围、注入模式和基数。
- [ ] 实例每次变更产生 `revision + 1` 的完整快照，并可按历史 revision 读取。
- [ ] 未绑定必填知识时 `export_spec` 失败；绑定完整后返回结构稳定且持久化的 `AgentSpec`。
- [ ] 每个成功写操作至少产生一条同事务审计事件，回滚时业务数据与审计数据均不留存。
- [ ] 幂等重放、幂等冲突和 revision 冲突有可重复的自动化证据。
- [ ] 核心 REST 路由与应用服务返回同构对象，已知领域异常不泄露堆栈、SQL 或本地路径。
- [ ] `test_register_clone_bind_export` 从空 SQLite 数据库通过。
- [ ] `uv run ruff format --check src tests`、`uv run ruff check src tests`、`uv run mypy src tests` 和 `uv run pytest -q` 全部通过。
- [ ] wheel/sdist 构建成功，GitHub Actions 在 M1 退出提交上通过。

## 6. 已知风险与处理

| 风险 | 影响 | M1 处理 | 备选方案/触发条件 |
| --- | --- | --- | --- |
| 模型和业务规则过多 | 单次变更难评审 | 按 M1.1-M1.5 分批实现，每批配套测试 | 若批次仍过大，按聚合根再拆分 |
| JSON 快照无法依赖 SQLite 列约束校验内部字段 | 坏数据可能延迟到读取时暴露 | 写入前 Pydantic 校验，读取时严格反序列化 | 出现跨对象高频查询时拆分结构化列 |
| 事务、幂等与审计耦合 | 部分成功会破坏可追溯性 | 由 Unit of Work 统一提交或回滚 | 引入外部消息系统时再评估 outbox |
| 同一实例并发绑定 | 可能覆盖新快照 | `expected_revision` + compare-and-swap | 多进程高冲突成为实测瓶颈时评估 PostgreSQL |
| M1 提前实现 REST 可能扩大范围 | 延迟核心业务闭环 | 仅实现闭环验收所需路由 | SDK、Tool、Demo 和其他路由统一留到 M3 |

## 7. 阶段报告

当前状态：M1 进行中；M1.3 应用服务已完成本地与远程验收，下一个工作包为 M1.4 核心 REST 契约。

- M1.1 完成时间：2026-07-19。
- M1.1 交付：递归不可变 JSON、canonical checksum、公共类型与枚举、稳定业务异常、JSON Schema 校验、M1 领域快照与 application commands。
- 本地测试：完整套件 `45 passed`；M1.1 定向套件 `34 passed`。
- 覆盖率：项目总分支覆盖率 93.45%；`domain/common.py` 90%，`domain/models.py` 93%。
- 静态与产物：Ruff format/lint、mypy strict 通过；wheel/sdist 构建成功，wheel 包含全部 M1.1 契约模块，Pydantic JSON Schema 生成冒烟测试通过。
- 代码提交：`8db26b4 feat: add immutable domain primitives`；`a865672 feat: define M1 production contracts`。
- 远程证据：GitHub Actions [`CI #4`](https://github.com/1471436961/agent-factory/actions/runs/29679135107) 在提交 `a865672` 上通过，运行耗时 23 秒。
- M1.2 完成时间：2026-07-19。
- M1.2 交付：六类 Repository Protocol、读写 Unit of Work、canonical SQLite codec、`002_persistence_contracts.sql`、快照/head 乐观并发、审计同事务写入、传输中立幂等记录、Container 组装与 wheel 资源检查。
- M1.2 定向测试：`14 passed`，覆盖 migration 原子回滚、六类仓储往返、原型状态 CAS、SemVer 排序、实例历史 revision、并发 revision 冲突、业务/审计共同回滚、只读事务、坏数据拒绝和 UoW 生命周期。
- M1.2 完整本地门禁：`57 passed`；项目总分支覆盖率 91%，其中 SQLite codec 91%、repositories 81%、Unit of Work 86%。覆盖率是路径执行证据，不替代需求完整性证明。
- M1.2 静态与产物：Ruff format/lint、mypy strict 通过；sdist/wheel 构建成功，wheel 已核对包含新增 application/domain/infrastructure 模块与 `001`、`002` 两个 migration 资源。
- M1.2 提交：`c1f8348 feat: define M1 persistence ports`；`7750072 feat: implement SQLite unit of work`；`00b8e0b docs: document M1.2 persistence design`。
- M1.2 远程证据：GitHub Actions [`CI #6`](https://github.com/1471436961/agent-factory/actions/runs/29681223005) 在提交 `00b8e0b` 上通过，运行耗时 21 秒。
- M1.3 完成时间：2026-07-19。
- M1.3 交付：纯原型/知识/工具策略、`AgentSpecBuilder`、metadata-only `ToolCatalog`、typed idempotency replay、allowlisted `AuditEventFactory`、完整 `FactoryController` 和 Container 组装。
- M1.3 事务证据：成功写操作在同一 UoW 提交业务快照、审计与幂等响应；相同 key/request 重放原对象且不重复审计，不同 request 冲突；失败校验不留业务或审计数据。
- M1.3 绑定/导出证据：真实 SQLite 测试覆盖 revision 冲突、替换规则、缺失知识、必填槽、未触碰 binding 溯源保持，以及同 revision Spec 只写入和审计一次。
- M1.3 完整本地门禁：`80 passed`，总分支覆盖率 92%，其中 `application/controller.py` 为 88%；Ruff format/lint、mypy strict、sdist/wheel 构建通过，wheel 核对包含 Controller、幂等、审计、工具目录和两份 migration。
- M1.3 提交：`83ffc99 feat: add M1 production policies`；`ee40d37 feat: orchestrate M1 production commands`；`090b145 feat: wire M1 controller into SQLite runtime`；`63f73f3 test: cover M1 prototype status transactions`。
- M1.3 远程证据：GitHub Actions [`CI #8`](https://github.com/1471436961/agent-factory/actions/runs/29686510642) 在提交 `b7b081b` 上通过。
- 下一个工作包：M1.4 核心 REST 契约。
- 进入 M2 的人工结论：待验收证据齐备后由项目 owner 决定。
