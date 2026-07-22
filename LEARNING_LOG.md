# LEARNING_LOG

本日志记录项目 owner 在实践中确认的技术概念、工具原理和调试经验。每条记录应包含上下文、证据和可复用结论；未经 owner 确认的 AI 推测不写入。

## 记录格式

```text
日期：
里程碑：
主题：
上下文：
证据：
结论：
后续应用：
```

## 编号规则

“已确认记录”使用 Markdown 自动有序列表。每条记录在源码中都以 `1.` 开头，渲染器会按当前位置显示为连续编号；新增、删除或移动记录后，后续编号会自动调整。

## 已确认记录

1. **SQLite `executescript()` 需要显式事务**

   - 日期：2026-07-17
   - 里程碑：M0
   - 上下文：migration 必须保证业务 DDL 与历史记录同时成功或同时失败。
   - 证据：Python 官方文档说明 `executescript()` 不提供额外的隐式事务控制；`_apply()` 将事务控制、业务 DDL 与历史 INSERT 组合在同一脚本，并在 `aiosqlite.Error` 时 rollback。现有集成测试验证了迁移幂等和 checksum 冲突，但尚未直接验证中途失败后的 DDL 回滚。
   - 结论：Runner 需要把 `BEGIN IMMEDIATE`、迁移 SQL、历史 INSERT 和 `COMMIT` 放入同一脚本，失败时 rollback 并阻止应用 ready。
   - 验证边界：目前只能确认事务边界的实现与官方语义一致，不能宣称失败原子性已被集成测试充分证明；需要补充“前置 DDL 成功、后续 SQL 失败”场景，同时断言业务 DDL 与历史记录都未保留。
   - 后续应用：所有新增 migration 都保持 forward-only，已执行文件不得修改；复杂多后端需求出现时重新评估 Alembic。

1. **源码可运行不代表 wheel 资源完整**

   - 日期：2026-07-17
   - 里程碑：M0
   - 上下文：首次 wheel 构建成功，但归档列表中没有仓库根目录的 migration SQL；理解为什么源码 checkout、sdist 和 wheel 是三个不同运行边界。
   - 证据：首次用 `zipfile` 检查 wheel 时只看到 Python 模块和 dist-info；将 SQL 移入 package 后重新构建并检查归档。本次再次运行 `uv build`，成功生成 `agent_factory-1.0.0a1.tar.gz` 和 `agent_factory-1.0.0a1-py3-none-any.whl`，归档列表确认 wheel 包含 `agent_factory/infrastructure/sqlite/sql/001_initial.sql`。
   - 结论：构建成功只证明 build frontend 成功调用 backend 并生成归档，不证明运行时所需的非 Python 资源已经进入产物；源码测试能读取仓库文件，也不能代替 wheel 内容检查。sdist 是供后续构建的源码归档，wheel 是直接安装产物，二者都应独立验证。
   - 易混点：`src/agent_factory` 中存在 SQL 不等于安装后的 `agent_factory` 包含 SQL；wheel 安装成功也不等于应用能从 package 路径找到运行资源；`py3-none-any` 只说明项目自身是纯 Python wheel，不说明全部依赖都跨平台。
   - 后续应用：配置模板、Schema、Prompt 或 SQL 等运行资源必须作为 package data；CI 除测试源码目录外，还需构建 wheel、检查归档，并在隔离环境安装产物后从已安装 package 路径运行 migration smoke test。

1. **M0 启动链与服务就绪边界**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：ASGI lifespan、application factory、进程级依赖、数据库迁移与健康检查。
   - 上下文：理解进程启动后，Server、Web Application、依赖容器和数据库初始化之间的职责边界，以及 FastAPI 如何向请求暴露进程级依赖与服务状态。
   - 证据：`main.py` 的 FastAPI lifespan 按 `uvicorn → lifespan startup → build_container() → Container.start() → MigrationRunner.migrate()` 进入服务状态；`Container.start()` 只有在 `await migrate()` 正常返回后才设置 `_ready = True`；`test_api_lifespan.py` 使用注入临时 Settings 的独立应用、`ASGITransport` 和显式 lifespan context 验证数据库在接收请求前完成 migration、健康端点返回成功、请求期间 ready 且退出后恢复为 false，独立重跑结果为 `1 passed`。
   - 结论：
     - ASGI 是异步 Server 与 Python Application 之间的标准通信协议；Uvicorn 是 Server，FastAPI 应用是 Application，lifespan 是双方约定的启动与关闭事件通道。
     - `create_app(settings=None)` 是 application factory：生产入口 `app = create_app()` 从环境解析配置，测试可传入 `test_settings` 获得独立 Container 和临时数据库，避免共享模块级运行状态或误写真实数据目录。
     - “启动前完成数据库迁移”是把数据库 Schema 从已记录版本升级到当前代码要求的版本，不是备份数据，也不是搬迁数据库。应用不能在旧 Schema 上提前接收业务请求。
     - `_ready = True` 必须放在 `await migrate()` 之后。若 migration 抛出异常，赋值语句不会执行，因此 `ready=True` 才能作为“关键启动依赖已经成功”的可靠承诺。
     - `build_container()` 是 composition root，只同步创建对象并连接依赖关系，不执行 migration 等 I/O；`start()/close()` 承担有副作用的资源生命周期。这样对象装配可以独立测试，启动失败也能由 lifespan 统一处理。
     - Container 保存到当前应用实例的 `app.state`，请求通过 `request.app.state.container` 访问同一组进程级依赖；不同 `create_app()` 实例可以拥有彼此隔离的状态。
     - liveness 只回答进程是否仍能处理请求，正常返回 HTTP 200 和 `{"status":"ok"}`，不检查数据库；readiness 要求 `container.ready` 且数据库 `ping()` 成功，否则返回 HTTP 503，而不是布尔值。数据库短暂故障不应让 liveness 失败并触发无效重启。
   - 流程图：

     ```text
     Uvicorn
        │ ASGI lifespan startup
        ▼
     FastAPI ──► build_container() ──► Container.start()
                                               │
                                               ▼
                                      MigrationRunner.migrate()
                                         │               │
                                       成功              失败
                                         │               │
                                         ▼               ▼
                                   ready = True      启动失败，不 ready
                                         │
                                         ▼
                                      接收请求
     ```

   - 易混点：容器对象创建成功不代表服务 ready；数据库文件存在也不代表 Schema 已升级；readiness 内部条件是布尔判断，但 HTTP 接口用 200/503 表达结果；ASGI 不是 Web 框架，也不是 Web Server 的具体实现。
   - 验证边界：现有集成测试只覆盖成功启动和 ready 后正常响应，尚未覆盖 readiness 503、数据库 `ping()` 失败和 migration 失败时 lifespan 无法进入请求阶段。`container.start()` 位于 `try/finally` 之前，若未来 start 分步获取长期资源后中途失败，需要由 start 自行回滚或扩大清理边界；当前 migration connection 自行关闭，暂未形成资源泄漏。
   - 后续应用：新增数据库连接池、任务队列等启动资源时，继续保持“同步装配、异步启动、依赖成功后 ready、关闭阶段回收”的结构；为 success、not-ready、startup-failure 和 shutdown 分别建立集成测试。

1. **Pydantic Settings 的配置来源与校验边界**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：`BaseSettings`、配置优先级、类型转换、不可变配置和两层校验。
   - 上下文：理解 `settings.py` 如何把代码默认值、`.env`、系统环境变量和显式初始化参数转换成唯一的进程配置对象。
   - 证据：本地设置 `AGENT_FACTORY_DEFAULT_PAGE_SIZE='7'` 后，`Settings()` 得到整数 `7`；修改已创建实例触发 `frozen_instance`；`default_page_size=101, max_page_size=100` 触发跨字段 `ValidationError`；`tests/unit/test_settings.py` 覆盖默认配置、环境变量覆盖和非法分页范围。
   - 结论：
     - 当前项目的有效配置优先级为：`Settings(...)` 显式初始化参数 > 系统环境变量 > `.env` > 字段默认值。因此四处分别设置为 `50/40/30/20` 时，最终值是 `50`。
     - `env_prefix='AGENT_FACTORY_'` 把字段 `default_page_size` 映射为 `AGENT_FACTORY_DEFAULT_PAGE_SIZE`；Pydantic 会按字段声明把环境变量字符串解析为 `int` 等目标类型。
     - `Field(ge=...)` 检查单个字段，例如 `default_page_size=0`；`model_validator(mode='after')` 检查字段关系，例如默认页大小不能大于最大页大小。两类错误都在应用启动时以 `ValidationError` 暴露，实现 fail fast。
     - `frozen=True` 防止启动后重新给配置字段赋值，避免同一进程内配置漂移，但它是浅层冻结，不保证嵌套可变对象不可修改。
     - `SecretStr` 负责降低密钥在日志和对象展示中意外泄露的风险，不等于加密存储；真实密钥仍应放在未提交 Git 的 `.env` 或部署 Secret 中。
     - `data_dir` 是相对当前工作目录的运行时可写数据位置；`migrations_dir` 是随 package 发布的只读程序资源，使用 `__file__` 锚定为 package 内绝对路径，确保从其他目录启动或安装 wheel 后仍能找到 SQL。
   - 易混点：显式初始化参数的优先级高于环境变量；`.env.example` 只是配置模板，`BaseSettings` 默认读取的是 `.env`；进程启动后修改环境变量不会自动更新已经创建的 `Settings` 对象。
   - 后续应用：新增配置时同时定义类型、边界、环境变量示例和测试；涉及多个字段的业务不变量使用模型级校验；应用只在启动阶段创建一次 `Settings` 并通过容器共享。

1. **Protocol 与可替换系统端口**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：结构化子类型、依赖倒置与确定性测试。
   - 上下文：理解为什么应用层定义 `Clock`、`IdGenerator`、`CorrelationContext`，而真实时间、UUID 和上下文存储实现在 infrastructure 层。
   - 证据：`tests/unit/test_system_ports.py` 将 `SystemClock()`、`UUID4Generator()` 和 `ContextVarCorrelationContext()` 分别赋给对应 Protocol 类型；默认适配器不显式继承这些 Protocol，mypy strict 仍通过签名检查；运行测试验证 UTC 时间、不同 UUID 和上下文初始状态。
   - 结论：
     - `Protocol` 使用结构化子类型：实现类不必显式继承协议，只要具有名称、参数和返回类型匹配的方法，静态类型检查器就认为它满足契约。例如 `SystemClock.now() -> datetime` 满足 `Clock`。
     - 应用服务依赖端口，而不是直接依赖 `datetime.now()`、`uuid4()` 或 `ContextVar`。生产环境注入真实适配器，测试环境可注入固定时间和固定 ID，不需要修改业务代码。
     - 当前 Protocol 未使用 `@runtime_checkable`，契约主要由 mypy 在静态检查阶段验证；运行时行为仍需单元测试覆盖。
     - 只为已经确认需要替换的外部不确定性建立小接口，比引入完整依赖注入框架更符合 M0 的范围。
   - 易混点：结构匹配不等于继承关系；类型签名 `datetime` 不能自动保证所有 Clock 实现都返回带时区时间；生成两个不同 UUID 的测试是基本行为检查，不是数学意义上的唯一性证明。
   - 后续应用：业务服务需要当前时间或新 ID 时通过构造参数接收端口；测试使用 `FixedClock`、顺序 ID Generator 等确定性实现，基础设施适配器单独验证真实系统行为。

1. **ContextVar 与 Token 的上下文恢复机制**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：异步上下文隔离、嵌套状态和异常安全清理。
   - 上下文：理解为什么 correlation ID 不能保存在普通全局变量中，以及 `reset(token)` 为什么能恢复 `set()` 前的状态。
   - 证据：本地实验依次执行 `set('request-1')`、`set('request-2')`、`reset(inner)`、`reset(outer)`，观察到 `None → request-1 → request-2 → request-1 → None`；`inner.old_value` 为 `request-1`，首次设置产生的 `outer.old_value` 为 `Token.MISSING`；单元测试验证同样的嵌套恢复顺序和空白 ID 拒绝逻辑。
   - 结论：
     - `ContextVar` 是当前执行上下文中的键，不是只保存一个进程级值的普通全局变量；同一个 `ContextVar` 在不同 asyncio Task 的 Context 中可以解析出不同值，从而避免并发请求的 correlation ID 相互覆盖。
     - 每次 `set()` 都返回只对应本次修改的 `Token`。Token 记录目标变量和修改前状态；`reset(token)` 根据该记录恢复旧绑定。旧状态为 `Token.MISSING` 时，reset 会移除绑定，后续 `get()` 回落到默认值。
     - `set(None)` 只会写入新值，不知道嵌套调用前是 `request-1` 还是未绑定，因此不能替代精确恢复。内层 `reset(inner)` 应恢复外层值，最外层 `reset(outer)` 才恢复初始状态。
     - 标准用法是 `token = set(...)` 后在 `finally` 中 `reset(token)`，保证正常返回、异常和任务取消都不会泄漏请求上下文。嵌套调用应按与 set 相反的顺序恢复，且同一 token 不能重复使用。
   - 流程图：

     ```text
     当前值 None
         │ set("request-1")，返回 outer token（旧状态：MISSING）
         ▼
     当前值 request-1
         │ set("request-2")，返回 inner token（旧值：request-1）
         ▼
     当前值 request-2
         │ reset(inner)
         ▼
     当前值 request-1
         │ reset(outer)
         ▼
     无显式绑定，get() 返回默认值 None

     并发时：Task A Context ─► request-A
             Task B Context ─► request-B
     ```

   - 易混点：`Token` 不是 correlation ID，也不是整个 Context 的快照，而是一次特定 `set()` 的撤销记录；`default=None` 与 Context 中显式绑定 `None` 在内部状态上不同，`Token.MISSING` 用来区分此前没有绑定。
   - 后续应用：API middleware 设置 correlation ID 后必须在 `finally` 中 reset；日志与审计代码只通过 `CorrelationContext.get()` 读取当前请求 ID，不使用模块级可变字符串保存请求状态。

1. **Forward-only migration 通过历史与 checksum 保证幂等和可追溯**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：migration 发现、历史校验、checksum 与幂等执行。
   - 上下文：理解数据库中已经存在业务表，为什么仍不足以证明 Schema 与当前代码一致，以及重复启动时 Runner 如何决定跳过或执行 migration。
   - 证据：`schema_migrations` 保存 `version`、`name`、`checksum` 和 `applied_at`；Runner 先发现本地文件并校验版本从 `001` 连续，再加载数据库历史并比较名称与 SHA-256；集成测试实际验证首次运行返回 `(1,)`、第二次返回空元组，并在修改已应用 SQL 后抛出 `MigrationChecksumError`，提权重跑结果为 `2 passed`。
   - 结论：
     - migration 文件采用 `NNN_name.sql` 并从 `001` 连续递增，防止缺失、重复或顺序含糊的 Schema 变更进入执行阶段。
     - Runner 必须先执行 `_validate_history()`，再跳过已应用版本；否则修改过的 SQL 会因为版本已存在而被静默忽略。
     - 幂等性来自数据库历史中的已应用版本，而不是依赖业务 SQL 使用 `CREATE TABLE IF NOT EXISTS`。`applied_versions` 表示本次实际执行内容，`current_version` 表示完成后的最终状态。
     - checksum 基于 SQL 原始字节，因此注释和换行变化也会触发冲突。已执行 migration 必须保持不可变；后续 Schema 调整应新增 `002_*.sql`，而不是修改 `001_initial.sql`。
     - `MigrationConfigurationError`、`MigrationDefinitionError`、`MigrationHistoryError` 和 `MigrationExecutionError` 区分部署配置、仓库定义、历史冲突和 SQL 执行失败；它们都属于必须阻止应用启动的 `MigrationError`。
   - 易混点：checksum 用于发现可信环境中的历史漂移，不是防攻击签名；数据库里有表不等于 migration 历史可信；对外执行异常应是具体的 `MigrationExecutionError`，底层 SQLite 异常通过 `__cause__` 保留。
   - 后续应用：所有 Schema 变化只新增 forward migration；补充数据库历史缺失本地版本、名称变化、非法 UTF-8、错误 URL 归一化和执行回滚测试；切换 PostgreSQL 或需要 downgrade、多进程迁移时评估 Alembic。

1. **实例持久化采用不可变 snapshot 与可变 head**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：初始 Schema 的版本化、审计与 JSON 载荷取舍。
   - 上下文：理解 `001_initial.sql` 为什么同时设计 `instance_snapshots` 与 `instance_heads`，以及原型、知识包和实例规格为什么将完整模型保存为 `payload_json`。
   - 证据：`instance_snapshots` 以 `(instance_id, revision)` 为主键并外键引用来源原型版本；`instance_heads` 以实例 ID 指向当前 revision；`agent_specs` 外键绑定对应实例快照；`prototypes` 与 `knowledge_packages` 均使用“稳定身份列 + version + payload_json + checksum”的结构。
   - 结论：
     - 实例变化通过新增 snapshot 保存不可变历史，head 只更新当前 revision。这样既能快速读取当前状态，也能复现旧配置、审计晋升路径和追踪 Agent 出身；直接覆盖单行会丢失历史。
     - 稳定身份、版本和外键关系使用关系型列，变化频繁的完整模型使用 `payload_json`，在 Alpha 阶段降低 Schema 频繁迁移成本并便于保存完整快照。
     - JSON 载荷的代价是 SQLite 不能用普通列约束完整校验内部结构，字段级查询和索引也更困难，因此后续必须由 Pydantic 与 repository 层保证结构正确性。
     - `audit_events` 为跨实体操作保留 actor、correlation ID 与事件载荷；`idempotency_records` 保存请求哈希和响应，为重复请求返回同一结果提供持久化基础。
   - 易混点：M0 创建这些表不等于已经实现原型注册、知识绑定、AgentSpec 导出或 repository；当前知识包与实例尚无独立关系表，状态字段也没有数据库枚举约束。
   - 后续应用：M1 repository 写入实例时采用“追加 snapshot + 原子更新 head”，Pydantic 模型序列化为规范 JSON 后计算 checksum；通过集成测试证明外键、revision 递增和审计溯源关系。

1. **测试分层、环境隔离与覆盖率证据边界**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：单元测试、集成测试、pytest 隔离策略与分支覆盖率解释。
   - 上下文：理解 M0 的测试如何区分局部规则与跨层闭环，如何避免环境变量、真实数据库、系统时间和 migration 源文件导致测试相互污染，以及测试通过数与覆盖率能够支持多强的结论。
   - 证据：当前测试集包含 8 个单元测试和 3 个集成测试；提权运行 `python -m pytest -q` 得到 `11 passed in 1.43s`，运行分支覆盖率得到总体 91%，其中 Migration Runner 为 85%、FastAPI main 为 95%。覆盖报告明确指出 SQL 执行失败、历史冲突、数据库 `ping()` 失败和 readiness 503 等路径尚未执行。
   - 结论：
     - 单元测试验证单个模块的规则和错误边界，例如 Settings 校验、系统适配器行为、数据库 URL 与 migration 版本定义；集成测试使用真实文件型 SQLite，验证 Runner、文件系统、数据库、Container、FastAPI lifespan 和 HTTP 端点之间的协作。
     - 测试类别由跨越的依赖边界决定，而不是由是否启动真实网络端口决定；`httpx.ASGITransport` 不打开 socket，但 API 测试仍跨越多个组件，因此属于集成测试。
     - `tmp_path` 为每次测试提供独立文件目录，`monkeypatch` 在测试结束后恢复环境变量，`FrozenClock` 固定时间，application factory 注入临时 Settings；checksum 测试先复制 migration 再修改副本，既模拟历史漂移，也避免污染 package 内基准 SQL 和其他测试。
     - `strict-config`、`strict-markers` 和 `asyncio_mode='strict'` 让配置错误、marker 拼写和未明确声明的异步测试尽早失败；`--basetemp=.tmp/pytest` 将临时数据库限制在项目工作区。
     - `11 passed` 只能证明当前环境下 11 个已定义场景的断言通过；91% 分支覆盖率只说明相应代码路径被执行，不能证明断言充分、需求完整、并发安全或生产环境可靠。当前配置未设置 `fail_under`，91% 还是观测值，不是自动质量门禁。
   - 易混点：覆盖率高不等于业务正确率高；测试文件位于 `integration` 目录也不能替代对其跨层行为的检查；源码目录测试通过不能证明 wheel 中 package data 完整，打包产物需要独立验证。
   - 验证边界：当前优先缺口包括 migration 中途失败后的 DDL/历史回滚、readiness 503 与 `ping()` 失败、migration 失败阻止 lifespan、数据库历史名称或版本冲突，以及并发 asyncio Task 的 ContextVar 隔离。
   - 后续应用：新增功能时先按风险选择测试层级；纯模型不变量使用单元测试，repository 与事务使用文件型数据库集成测试，API/SDK/Tool 共享 application service 时增加契约测试；CI 同时运行测试、分支覆盖率观测和最终 wheel 隔离安装验证。

1. **Python 基线、依赖声明、锁文件与发布产物承担不同职责**

   - 日期：2026-07-18
   - 里程碑：M0
   - 主题：Python 版本请求、依赖兼容范围、uv 锁定与 Hatchling 打包边界。
   - 上下文：理解 `.python-version`、`requires-python`、`pyproject.toml`、`uv.lock` 和 wheel 分别约束什么，以及“项目环境可复现”与“第三方安装时的依赖解析”为什么不是同一件事。
   - 证据：`.python-version` 请求 Python 3.11，当前解释器为 3.11.15；`uv tree --locked` 成功核对 85 个已解析 package，包含 FastAPI 0.139.2、Pydantic 2.13.4 和 SQLAlchemy 2.0.51 等具体版本；`uv build` 通过 Hatchling 成功构建 sdist 与 wheel。构建与依赖树命令在沙箱内因用户级 uv cache 权限失败，提权后正常完成，说明失败来自受限执行环境而非项目配置。
   - 结论：
     - `.python-version = 3.11` 是本地和 CI 的默认 minor baseline，允许使用可用的 3.11.x patch；`requires-python = '>=3.11'` 是发布给安装工具与使用者的兼容性声明，两者都不提供解释器字节级固定。
     - `pyproject.toml` 声明直接依赖和允许版本范围，`uv.lock` 保存本项目实际采用的跨平台解析结果。新版本发布不会使 `uv run --locked` 自动升级；更新需要显式执行 `uv lock --upgrade-package ...`。
     - `uv.lock` 可以解析所有 extras，但只有 `uv sync --extra ...` 选择的集合会安装到当前环境。lock 应提交版本控制且不应手工编辑，否则其他机器无法复用相同解析结果。
     - 普通用户通过 wheel 和 pip 安装时读取的是 wheel metadata 中的版本范围，不读取项目仓库的 `uv.lock`；因此 lock 能固定本项目环境，却不能弥补过宽或缺失的发布依赖约束。
     - `uv build` 是 build frontend，Hatchling 是 backend；`src` layout 让测试和运行依赖已安装或 editable package，减少从仓库根目录误导入同名源码的风险。安装后的 `__version__` 来自 distribution metadata，与 `pyproject.toml` 中的版本保持一致。
   - 易混点：锁文件包含某个 extra 不等于当前虚拟环境已安装该 extra；`pyproject.toml` 的宽范围不等于当前环境没有固定版本；Python minor baseline、依赖锁定和 wheel 文件完整性是三类独立问题。
   - 验证边界：当前 `httpx` 只在测试中使用却属于基础依赖，`jsonschema` 和 `PyYAML` 在 M0 尚未使用；`demo` 与 `llm` extras 没有版本范围；发布元数据还缺少 license、project URLs 和 classifiers。当前配置提供工程级一致性，不构成完全 hermetic 或字节级可复现构建。
   - 后续应用：首次提交必须包含 `pyproject.toml`、`.python-version` 和 `uv.lock`；依赖升级通过显式 lock 更新并运行完整质量门禁；M1 按实际使用重新归类运行时依赖，开源发布前收紧 extras 版本并补齐 metadata。

1. **M1 生产闭环以 AgentSpec 为交付边界**

   - 日期：2026-07-21
   - 里程碑：M1
   - 主题：生产层职责、运行时中立规格与 Agent 出身追溯。
   - 上下文：理解 M1 的各工作包为什么围绕定义、注册、克隆、知识绑定、规格导出和审计展开，以及 Agent 工厂与 Agent 运行时的职责边界。
   - 证据：`docs/milestones/m1-core-production-chain.md` 将最小闭环定义为从原型注册到 AgentSpec 导出和审计查询；`tests/contract/test_rest_api.py::test_register_clone_bind_export` 从空 SQLite 数据库执行完整 REST 链路，并在重建应用后验证原型、规格和审计仍可读取；M1.5 本地门禁结果为 `87 passed`，domain、application 和项目总分支覆盖率分别为 96%、93% 和 92%。项目 owner 在讲解问答中确认 M1 的最终产品是 `AgentSpec`，revision 快照用于保证 Agent 来源可追溯。
   - 结论：
     - Agent 工厂生产的是运行时可以消费的结构化 `AgentSpec`，不是 Agent 执行任务后的自然语言答案；任务推理和工具执行仍属于后续运行时。
     - `AgentDefinition` 描述设计内容，`AgentPrototype` 增加稳定身份、版本和 checksum，`AgentInstance` 保存来源引用与不可变 revision，`AgentSpec` 解析出最终工具、知识和输出约束。各对象不是同一模型的重复命名，而是生产过程中的不同责任边界。
     - 可追溯性来自完整证据链，而不是只保存一个实例 ID：`AgentSpec(instance_id, revision) -> AgentInstance snapshot -> PrototypeRef(id, version, checksum) -> knowledge version/checksum -> audit events`。
     - 知识绑定和后续配置变化产生新 revision，不覆盖旧 revision；这使“当前配置是什么”和“它如何演化而来”可以同时回答。
   - 流程图：

     ```text
     AgentDefinition
            │ 注册身份、版本和 checksum
            ▼
     AgentPrototype
            │ 克隆配置快照
            ▼
     AgentInstance revision 1
            │ 绑定知识并保留旧快照
            ▼
     AgentInstance revision 2
            │ 解析工具、知识引用和输出约束
            ▼
        AgentSpec ──► 外部运行时
            │
            └──────► AuditEvent 追溯生产过程
     ```

   - 易混点：AgentSpec 是可交付规格，不是可执行进程；保存原型 ID 不能替代保存原型版本和 checksum；技术验收项通过不等于 M1 已自动退出，进入 M2 仍需要 owner 人工决策。
   - 后续应用：后续运行时适配器只消费 AgentSpec，不反向改变工厂领域模型；新增生产操作必须同时考虑 revision、来源引用和审计事件，保持追溯链完整。

1. **M1.1 通过递归不可变对象和 canonical checksum 建立领域契约**

   - 日期：2026-07-21
   - 里程碑：M1.1
   - 主题：Pydantic v2 不变量、不可变 JSON、确定性序列化与校验职责。
   - 上下文：理解为什么 `FrozenModel`、`FrozenJsonObject`、canonical JSON 和分层校验需要同时存在，以及哪些规则适合模型校验器、哪些规则应由领域策略执行。
   - 证据：`tests/unit/domain/test_common.py` 验证嵌套 JSON 无法原地修改、非 JSON 值被拒绝、键顺序不影响 canonical JSON/checksum，以及 frozen model 拒绝字段赋值和额外字段；`tests/unit/domain/test_models.py` 和 `tests/unit/domain/test_services.py` 覆盖模型不变量、知识策略与确定性 AgentSpec 构建。项目 owner 在问答中确认 `frozen=True` 不能阻止普通嵌套 `dict` 被修改，知识类型和版本与槽位的匹配属于领域策略。
   - 结论：
     - Pydantic `frozen=True` 只阻止模型字段重新赋值，是浅层冻结；嵌套 `dict` 和 `list` 必须在输入边界递归转换为只读 mapping 和 tuple，才能使领域快照真正不可原地修改。
     - 冻结后的 JSON 在内存中保持不可变，序列化时恢复为标准 JSON object/array，因此不可变性不会破坏 REST、SQLite 和构建产物之间的数据交换。
     - canonical JSON 固定 UTF-8、键排序、紧凑分隔符并拒绝 NaN/无穷值；checksum 基于 canonical bytes，使语义相同但键顺序不同的 JSON 得到相同摘要。checksum 用于内容一致性和漂移检测，不是鉴权签名。
     - 字段格式和长度由字段约束负责；同一对象内部的时间、状态、唯一性等关系由 `model_validator` 负责；知识包是否满足实例槽位等跨对象规则由纯领域策略负责；版本唯一性和并发冲突由 repository/数据库负责；HTTP 状态映射属于接口层。
   - 易混点：模型不可重新赋值不等于内部容器不可变；checksum 相同只支持内容一致性判断，不证明内容正确或来源可信；Pydantic 模型各自合法不代表两个模型组合后符合业务规则。
   - 后续应用：新增领域模型时先区分自身不变量与跨对象策略；进入持久化前统一生成 canonical JSON；需要参与追溯的快照都保存或校验稳定 checksum。

1. **M1.2 用 Repository、Unit of Work 与 CAS 实现可靠快照持久化**

   - 日期：2026-07-21
   - 里程碑：M1.2
   - 主题：持久化端口、事务原子性、实例 revision 和损坏检测。
   - 上下文：在 M0 已理解 snapshot/head 表结构的基础上，进一步理解 M1 如何把不可变领域对象落入真实 SQLite，并保证业务快照、审计和幂等记录不会出现部分成功。
   - 证据：`tests/integration/test_sqlite_repositories.py` 验证六类 M1 repository 的 canonical 快照往返、实例历史与并发 revision 冲突、显式 rollback 和 UoW 生命周期保护；实现中所有 repository 由同一个 `SqliteUnitOfWork` connection 创建，`save_snapshot()` 先追加新快照，再以 `WHERE current_revision = expected_revision` 更新 head。项目 owner 在问答中确认单个 Repository 不能保证跨 Repository 原子性，CAS 失败抛出的 `RevisionConflictError` 会使同一 UoW 中的快照插入整体 rollback。
   - 结论：
     - Repository 是按聚合划分的持久化端口，负责某一类对象的查询和保存；Unit of Work 管理共享 connection 和事务，负责多个 Repository 之间的原子性。两者解决的问题不同，不能互相替代。
     - 写 UoW 使用 `BEGIN IMMEDIATE` 尽早取得 SQLite 写权限，只读 UoW 使用 `PRAGMA query_only = ON` 防止误写；业务成功必须显式 `commit()`，异常、忘记提交或上下文退出时仍有活动事务都会 rollback。
     - 实例更新采用“追加 `instance_snapshots` + CAS 更新 `instance_heads`”。新 revision 必须等于 `expected_revision + 1`；主键冲突或 head 更新行数不为 1 都转换为 `RevisionConflictError`，系统不自动合并并发配置变更。
     - 快照保存完整 canonical `payload_json`，同时保存 ID、版本、状态、来源和 checksum 等关系型投影。读取时重新进行 Pydantic 校验、投影一致性检查和可重算 checksum 检查，从而发现 JSON 损坏或列与载荷之间的漂移。
     - SQLite 驱动错误统一转换为不泄露 SQL、路径和驱动信息的 `RepositoryUnavailableError`；原始异常保存在 `__cause__` 中供服务端诊断。已知唯一键和 revision 冲突则转换为稳定的业务错误。
   - 易混点：Repository 的 async 方法不自动意味着共享事务；执行过 `INSERT` 不等于已经持久化，只有 `commit()` 后数据才耐久；CAS 负责发现丢失更新，不负责自动解决冲突；捕获仓储异常后继续提交会破坏既定用法，因此应用服务不应在同一 UoW 中吞掉异常。
   - 验证边界：SQLite 单写者和每 UoW 新建 connection 适合 Alpha 单机闭环，不代表具备高并发吞吐；JSON 快照便于回放和模型演进，但数据库无法直接约束全部内部字段，高频字段查询出现后需要重新评估列投影、索引或 PostgreSQL。
   - 后续应用：应用服务把一次业务变化、审计事件和幂等记录放入同一 UoW，并把 `commit()` 作为最后一个有副作用动作；新增 revision 写入继续使用 append-only snapshot 和 expected revision CAS。

1. **M1.3 用确定性 Controller 编排完整生产操作**

   - 日期：2026-07-21
   - 里程碑：M1.3
   - 主题：应用服务编排、纯领域策略、幂等重放、审计与工具元数据边界。
   - 上下文：理解 `FactoryController` 为什么位于接口与领域/持久化之间，以及一次注册、克隆、知识绑定或规格导出应如何组合数据读取、规则判断和事务写入。
   - 证据：`tests/integration/test_factory_controller.py` 覆盖可重放且可审计的完整生产链、失败校验回滚、幂等 key 冲突、revision/替换绑定规则、未触碰槽位的来源保持、原型状态变化和非法 correlation 导致的整体回滚；`tests/unit/application/test_idempotency.py` 验证 typed replay、请求哈希、过期清理和损坏缓存拒绝；工具策略测试验证未知工具和越权权限被拒绝，审计测试确认知识正文不进入事件载荷。项目 owner 在问答中确认幂等机制用于防止客户端超时重试造成重复生产，并进一步理解 Controller 为纯策略收集数据和控制事务的职责。
   - 结论：
     - `FactoryController` 是确定性的应用服务，不是 Agent，也不依赖 LLM 做决策。它接收 Command/Query，组织端口、Repository、纯领域策略、审计和幂等服务，接口层只负责输入输出转换。
     - Command 表达调用者的操作意图，包含 actor、expected revision 和 idempotency key 等执行信息；`AgentPrototype`、`AgentInstance` 等领域对象表达操作完成后的事实。两者不能直接混为同一个模型。
     - Controller 负责从 Repository 读取实例和知识包、按顺序调用纯策略、保存新快照与审计/幂等记录并统一 commit；领域策略只对完整输入执行无 I/O 判断。这样业务规则可脱离 SQLite 单元测试，也不会反向依赖基础设施。
     - 幂等不是简单拒绝重复请求：相同 key、operation 和 request hash 返回之前保存且重新经 Pydantic 校验的 typed response，不重复产生实例或审计；同一 key 被不同 operation/参数复用时抛出 `IdempotencyKeyReusedError`。只缓存成功结果，并与业务数据和审计事件在同一 UoW 中提交。
     - `AuditEventFactory` 通过专用方法限制事件类型和载荷，避免调用方自由拼接或把知识正文、system prompt、工具参数与 secret 写入审计；同一次操作的事件共享 correlation ID。
     - M1 的 ToolCatalog 只提供工具版本、Schema 和权限标签，`ToolPolicy` 负责存在性与权限上限检查；handler 执行、网络/文件访问、超时和沙箱属于运行时，不进入生产层。
     - `export_spec()` 先用只读 UoW 查询已有规格，未命中后进入写 UoW 二次检查，再重新校验知识和工具并执行 `add_if_absent`；因此同一 `(instance_id, revision)` 只持久化一个 AgentSpec，只有首次写入者追加导出审计。
   - 易混点：Controller 负责组织规则，不等于规则都应写在 Controller 中；async 方法不自动提供事务，原子性仍来自共享 UoW；幂等 key 不是请求 ID，也不能在不同业务参数之间复用；ToolCatalog 中存在工具元数据不表示生产层能够执行该工具。
   - 验证边界：M1.3 尚未提供 REST DTO、HTTP 错误映射、认证授权、技能 DAG、Evaluator、工具 handler 或 Agent 任务执行；幂等响应只在配置的 TTL 内可重放，URI 知识只保存来源和调用方 checksum，不主动下载验证内容。
   - 后续应用：新增接口适配器统一调用 Controller，避免复制业务流程；新增写操作沿用“幂等检查 -> 数据读取 -> 纯策略 -> 业务写入 -> 审计 -> 幂等响应 -> commit”顺序，并为失败回滚和重复请求建立集成测试。

1. **M1.4 通过 REST Adapter 保持传输契约与业务逻辑分离**

   - 日期：2026-07-21
   - 里程碑：M1.4
   - 主题：FastAPI 适配边界、HTTP 方法语义、统一错误契约与不可信 actor 标签。
   - 上下文：理解 REST 层如何把 path、header 和 body 转换为 application Command，为什么 Router、middleware、dependency 与 exception handler 各自承担不同职责，以及严格的 HTTP 契约为什么不等于已经具备公网部署安全性。
   - 证据：`tests/contract/test_rest_api.py` 从真实 HTTP adapter 验证完整生产链、稳定且关联 correlation ID 的脱敏错误、声明长度与流式 body 超限、lifespan 前 readiness 失败和可配置 API prefix；`tests/unit/interfaces/api/test_contracts.py` 验证全部当前 `FactoryError` 都有 HTTP status 映射、请求 DTO 拒绝额外字段/重复知识选择，以及错误 envelope 保持稳定。项目 owner 在问答中确认首次导出 AgentSpec 会持久化规格并追加审计，因此不能使用具有 safe method 语义的 GET；同时确认 `X-Actor-ID` 可由客户端任意填写，只能作为审计标签。
   - 结论：
     - REST 是 `FactoryController` 的 adapter：middleware 处理请求级上下文和大小上限，FastAPI DTO/dependency 校验传输输入，Router 将 path/header/body 机械合并为 transport-neutral Command，Controller 执行业务规则与事务，response model/exception handler 再转换为 HTTP 输出。
     - Router 不直接访问 Repository、clock、ID generator 或 migration runner，也不重新判断原型状态、知识槽、工具权限和 revision；未来 SDK 与 Tool adapter 应通过各自输入构造相同 Command，复用同一 Controller，避免形成多套业务逻辑。
     - `RequestContextMiddleware` 对每个请求解析或生成 correlation UUID，写入 request state 与 `CorrelationContext`，在响应 header、错误体、审计和日志中共享，并在 `finally` 中用 token 恢复上下文。请求体限制既提前检查合法的 `Content-Length`，也累计实际 ASGI body chunk，防止缺失或不可信长度绕过上限。
     - 领域层只提供 transport-neutral 的稳定错误码，REST 层显式映射 HTTP status；映射覆盖由集合相等契约测试保护。请求校验错误不回显原始 input，未知异常只在服务端按 correlation ID 记录 traceback，客户端固定收到脱敏的 `INTERNAL_ERROR`。
     - `POST /instances/{id}/spec-exports` 表示执行导出操作，因为首次调用会构建并持久化 AgentSpec、追加 `spec.exported` 审计；这不符合 GET 不应产生服务端状态变化的 safe method 语义。未来若提供只读取已存在规格的接口，可以单独设计 GET。
     - `X-Actor-ID` 是调用方声明且可伪造的审计标签，不是由服务端验证凭证后得到的 principal，也不能作为授权依据。真正的安全边界还需要认证验证 token/session/API key，并在此基础上执行操作级授权。
   - 易混点：Pydantic DTO 校验通过不代表业务操作允许执行；POST 可重复返回同一规格是 Controller 的唯一性行为，不会把该操作变成 GET；correlation ID 用于跨层关联，不是幂等 key，也不是身份凭证；统一错误格式不应包含未知异常的原始文本。
   - 验证边界：当前 middleware 以最多 1 MiB 的有界缓冲处理 JSON body，不支持大文件 streaming upload；认证和授权尚未实现，审计查询也未受权限保护；因此 M1 REST 契约可用于本地闭环验证，但不能描述为可直接暴露到不可信公网的生产服务。
   - 后续应用：新增 REST 路由时保持“DTO -> Command/Query -> Controller”结构，并同步补充 response model、稳定错误映射和契约测试；大知识文件采用对象存储直传或独立 streaming endpoint，不扩大当前全局缓冲上限；接入认证后由服务端 principal 替代客户端自报 actor。

1. **M1.5 用闭环测试、重启恢复与分层门禁建立阶段验收证据**

   - 日期：2026-07-21
   - 里程碑：M1.5
   - 主题：端到端生产故事、持久化恢复、证据矩阵和人工阶段退出。
   - 上下文：理解 M1.5 为什么不继续增加业务功能，而是把 M1.1-M1.4 组合成可重复的退出证据，以及自动化测试通过与 owner 决定结束阶段之间的区别。
   - 证据：`tests/contract/test_rest_api.py::test_register_clone_bind_export` 从空 SQLite 经真实 FastAPI lifespan 和 HTTP adapter 完成原型注册/幂等重放、发布、知识注册、实例克隆、未绑定导出失败、知识绑定、规格导出、原型废弃和审计查询；测试关闭并重建应用后，使用同一数据库重新读取原型、AgentSpec 和 7 条审计，并断言 `spec.exported` 仍只有 1 条。M1.5 完整本地门禁为 `87 passed`，domain、application 和项目总 branch coverage 分别为 96%、93% 和 92%，Ruff、mypy strict、sdist/wheel 构建及 CI 均通过。项目 owner 在问答中确认重启恢复用于排除进程内存造成的假阳性，并准确说明覆盖率只能证明分支被执行，不能证明断言、需求、并发、安全或语义质量完整。
   - 结论：
     - 完整闭环测试应从空数据库和真实启动协议开始，使 migration、Container、REST adapter、Controller、领域策略、Repository 和审计在同一条用户可观察链路中接受验证；预置数据库或绕过 lifespan 可能掩盖 Schema 和启动问题。
     - 幂等不能只以“第二次请求仍返回成功”为证据，还要同时断言响应对象相同、业务对象没有重复、相关审计事件没有重复。相同 revision 的 AgentSpec 二次导出返回持久化结果，但 `spec.exported` 只能出现一次。
     - 在必填知识未绑定时导出明确失败，绑定后实例从 revision 1 变为 revision 2 并导出 revision 2 的 AgentSpec，证明结构门禁确实位于交付边界之前，而不是只在文档中声明。
     - 第一次运行成功不能独立证明持久化。关闭 app/Container 后用相同数据库创建全新应用并恢复原型、规格和审计，才能排除对象只存于旧进程内存的假阳性；同时验证 migration 重入和新 Container 不依赖旧运行状态。
     - 主闭环测试证明跨层生产故事存在，模型不变量和纯策略由 unit tests 覆盖，Repository/UoW/CAS 由 SQLite integration tests 覆盖，HTTP 与错误语义由 contract tests 覆盖。失败组合分散到最接近责任边界的测试层，比全部堆入单个大型 REST 测试更容易定位和维护。
     - domain 90%、application 85%、total 80% 的独立 branch coverage 门槛，可以防止外围简单代码的覆盖率掩盖核心层测试不足；最终 96%/93%/92% 说明相应分支在当前测试集中被执行，不说明断言充分、需求无遗漏、并发场景全部正确、服务可安全公网部署或 Agent 语义输出可靠。
     - 验收清单勾选和 CI 通过表示预先定义的技术契约已有可重复证据，不会自动改变里程碑状态。阶段目标、文档一致性、残余风险与下一阶段进入条件仍需要 owner 人工评审和明确决策。
   - 易混点：跨完整技术栈的 ASGI 测试即使不打开真实网络端口，仍属于契约/集成层证据；重建 HTTP client 不等于重建应用，必须关闭旧 lifespan 并调用新的 `create_app()`；高覆盖率不能替代失败样本设计；M1 验收的是确定性生产治理，不是 Agent 回答质量。
   - 验证边界：M1.5 没有验证真实 LLM、运行时任务执行、技能晋升、认证授权、多进程并发或 PostgreSQL 部署；SQLite 单机闭环和当前失败矩阵支持 Alpha 技术可行性，不构成生产规模与安全性结论。
   - 后续应用：每个后续里程碑在开始前定义可观察验收标准和证据矩阵，退出时同时提供主闭环、责任层失败测试、静态检查、覆盖率门禁、构建产物与远程 CI 证据，并由 owner 单独记录人工进入下一阶段的决定。

1. **M2 以 revision 级证据和显式配置变换治理能力演化**

   - 日期：2026-07-22
   - 里程碑：M2 全局
   - 主题：评估证据作用域、显式晋升、观察降级和不可变配置演化。
   - 上下文：理解 M1 交付 AgentSpec 后，M2 如何在不运行 Agent、不依赖 LLM 做内部决策的前提下，对特定实例快照建立可审计的评估、晋升与降级闭环。
   - 证据：`tests/contract/test_m2_rest_api.py::test_m2_rest_governance_loop_survives_restart` 从空库经 REST 注册 Suite、SkillTree、绑定技能树的 Prototype 和 Knowledge，克隆 revision 1，提交外部 case result 生成 `REVIEW_REQUIRED` 报告，经最终人工复核后显式晋升为 revision 2，再记录“通过、失败、失败”三次观察并触发 revision 3 降级；重启后可恢复 Suite、Tree、Spec 和审计，并可幂等重放 Report、Review、Promotion 与最终 TaskOutcome。项目 owner 在讲解问答中确认 EvaluationReport 只是证据，修改实例必须显式调用 `promote_agent()`，并确认从 Prototype 重建配置可以防止已移除技能的 Prompt、工具或知识残留。
   - 结论：
     - M1 回答“如何生产可追溯的 AgentSpec”，M2 回答“如何在确定性治理规则下演化某个实例配置”；M2 不执行任务，也不把规则通过描述成语义质量已得到保证。
     - `EvaluationReport` 是绑定特定 instance revision、AgentSpec checksum、SkillTreeRef、EvaluationSuiteRef、runtime model 和一次 submitted evidence 的历史测量。模型、Prompt、工具、知识、Suite 或 revision 变化后，结论不能自动外推为永久能力。
     - 评估事实与配置变更必须分离。规则通过只产生报告；需要人工复核时追加不可变 `EvaluationReview`；真正晋升必须由显式 `PromoteAgentCommand` 触发，从而保留操作意图、人工决策权和审计边界。
     - 晋升生成 `revision + 1` 的新快照，不覆盖旧配置；TaskOutcome 只进入对应 revision 的观察窗口。达到确定性阈值后，降级再生成新 revision，旧报告和旧快照仍作为历史事实保留。
     - 晋升与降级都从来源 Prototype 和完整 active node 集合全量重建配置，而不是在当前配置上做反向 patch。这样既能清除已移除节点的 Prompt、工具、Schema 和知识槽，也能保证相同输入得到相同配置与 checksum。
   - 流程图：

     ```text
     M1 Instance revision 1
              │ 提交外部 case results
              ▼
       EvaluationReport ──► 可选最终 EvaluationReview
              │ 显式 PromoteAgentCommand
              ▼
        Instance revision 2
              │ 记录当前 revision 的 TaskOutcome 窗口
              ▼
       未达阈值：revision 不变
       达到阈值：从 Prototype + 剩余 active nodes 重建
              │
              ▼
       Instance revision 3 / DEGRADED
     ```

   - 易混点：报告通过不等于实例已经晋升；人工 review 在没有认证时仍是不可信 actor 标签；`DEGRADED` 是新快照的状态，不是删除历史 revision；规则对 submitted evidence 可重复不等于 evidence 来自可信运行时。
   - 验证边界：当前闭环证明给定输入下的评估、配置变换、持久化、幂等和审计可重复，不证明 Agent 永久具备某项能力、真实模型输出可靠、人工复核身份可信或服务可安全部署到公网。
   - 后续应用：运行时接入后必须把真实执行结果显式转换为 EvaluationSubmission，并保留 runtime/model 来源；任何新增配置演化操作都继续使用 expected revision、新快照、来源校验和显式命令，不允许评估器直接修改实例。

1. **M2.1 用不可变引用、稳定 DAG 和纯函数建立治理领域契约**

   - 日期：2026-07-22
   - 里程碑：M2.1
   - 主题：精确版本引用、技能树合法性、稳定拓扑顺序、全量配置重建与纯评估输入。
   - 上下文：理解 M2 在进入 Repository 和 Controller 之前，如何先把技能、评估和配置演化定义为可独立验证、无 I/O 副作用的领域规则。
   - 证据：`tests/unit/domain/test_skills.py` 覆盖非法图、多分支 DAG、稳定顺序、依赖、配置组合与冲突；`tests/unit/domain/test_evaluation.py` 覆盖 Suite、Submission、Report 不变量和六类规则参数；`tests/unit/domain/test_m2_checksums.py` 验证无序集合和输入顺序不改变 Tree/Suite checksum；`tests/unit/domain/test_m2_compatibility.py` 验证新增可选技能树来源仍可读取 M1 快照。项目 owner 在问答中确认规则引擎只有拿到实际 `case_results` 才能评估，因为 M2 不负责运行 Agent。
   - 结论：
     - `SkillTreeRef` 和 `EvaluationSuiteRef` 使用 `id + version + checksum` 精确标识不可变内容。`id + version` 只能说明逻辑名称，checksum 进一步发现同名版本内容被替换或错误引用；checksum 是一致性证据，不是来源签名。
     - `SkillTreeDraft` 在模型边界拒绝重复节点、自依赖、缺失父节点和环，并规范化节点顺序；非法图不能先注册再等待运行时发现。`SkillNode` 只声明父依赖和对 Prompt、工具、知识槽、输出 Schema、评估 Suite 与观察策略的配置影响。
     - `topological_order()` 先满足父子依赖，再使用节点 ID 对同时 ready 的节点稳定排序。即使两个节点互不依赖，也不能依赖 set/frozenset 的遍历顺序，否则 Prompt appendix 顺序、最终配置和 checksum 可能随输入顺序或进程变化。
     - `apply_skill_nodes(base, tree, active_node_ids)` 是纯数据变换：从不可变 Prototype definition 开始，按稳定顺序叠加完整 active node 集合，检测 Schema 和知识槽冲突，返回新的 `AgentDefinition`；它不读取仓储、不修改输入、不生成 ID 或时间，也不写审计。
     - `EvaluationSuite` 定义题目和规则，`EvaluationSubmission` 提供特定实例 revision 的实际 case results，`DeterministicRuleEngine` 只根据二者返回 `EvaluationOutcome`。只有 AgentSpec 和测试题目时并不存在待检查的输出，因此不能完成评估。
     - `EvaluationReport` 中的 report ID、服务端时间、Spec/Tree 来源和持久化事实不应由纯规则引擎伪造；M2.1 只定义结果和报告不变量，M2.3 再由 application service 组合服务端来源并提交事务。
   - 易混点：DAG 合法不代表任意 active node 子集合法，激活子节点时仍必须包含父节点；拓扑排序不稳定可能不影响工具集合，却会影响有顺序的 Prompt 和整体 checksum；纯函数返回新模型不等于结果已经持久化或实例已经晋升。
   - 验证边界：M2.1 能证明相同规范化输入产生相同图校验、规则结果、配置和 checksum，不能证明 submitted evidence 真实、SkillTree/Suite 已注册、报告来源由服务端生成、事务成功或 revision 已发生变化。
   - 后续应用：新增技能字段时必须明确组合顺序与冲突规则，并进入 canonical checksum；新增 RuleKind 时保持规则执行器纯计算和有界输入；涉及 Repository、clock、ID、审计或事务的职责继续留在 application/infrastructure 层。

1. **M2.2 用精确来源外键和双重存储建立治理持久化基础**

   - 日期：2026-07-22
   - 里程碑：M2.2
   - 主题：治理快照、关系投影、复合外键、读取验真和 M1 数据兼容。
   - 上下文：理解 M2.1 的不可变领域对象如何进入 SQLite，并保证 EvaluationReport、AgentSpec、SkillTree、EvaluationSuite、Review 和 TaskOutcome 之间的来源关系不能由调用方任意拼接。
   - 证据：`003_skill_governance.sql` 新增 EvaluationSuite、SkillTree、EvaluationReport、EvaluationReview 和 TaskOutcome 五类表，以及 `skill_node_suites`、`prototype_skill_trees`、`instance_skill_trees` 三类来源关系投影；`tests/integration/test_skill_governance_repositories.py::test_governance_snapshots_round_trip_after_restart` 在真实文件型 SQLite 中写入治理对象，关闭旧连接并创建新 UoW Factory 后完整读回；同文件的唯一约束、外键、rollback 和损坏投影测试证明非法来源组合与 JSON/列漂移会被拒绝。项目 owner 在问答中确认完整 JSON 与关系型投影并存是 Alpha 阶段的折中，并理解不同来源约束分别由 Report、Tree/Suite 和 TaskOutcome 的复合外键承担。
   - 结论：
     - M2.2 分别保存不可变 Suite、Tree、Report、Review 和 TaskOutcome，而不是把全部治理历史塞入当前 Instance JSON。独立记录允许按自身身份查询、追加最终事实并建立跨对象来源约束。
     - Report 通过 `(instance_id, instance_revision, agent_spec_checksum)` 外键绑定确切 AgentSpec 快照；SkillTree 和 EvaluationSuite 分别通过 `id + version + checksum` 外键绑定精确版本。一个实例 ID 存在不足以证明报告来源有效。
     - TaskOutcome 使用自己的 `(evaluation_report_id, instance_id, instance_revision)` 关系约束，保证观察样本引用的报告属于同一实例 revision。Tree/Suite 来源和 TaskOutcome 一致性不是 AgentSpec 复合外键单独完成，而是多组外键共同形成证据链。
     - 完整 `payload_json` 用于恢复 Pydantic 对象和降低 Alpha 阶段的 Schema 演进成本；关系型投影列用于主键、唯一约束、外键、索引和常用查询。两者重复是受校验的工程冗余，不是两个可以独立修改的数据源。
     - Repository 读取时先用 Pydantic 解码 JSON，再比较 ID、版本、decision、时间和来源投影，并重新计算 definition/payload checksum；SkillTree 还核对每个节点的 Suite 关系。任一不一致都转换为安全的 `RepositoryUnavailableError`，不会静默返回损坏对象。
     - 五个治理 Repository 保持各自的聚合边界，但由同一个 `SqliteUnitOfWork` connection 和事务管理。M2.3 因而可以把 AgentSpec、Report、AuditEvent 和 IdempotencyRecord 放入同一提交中，而不要求某个 Repository 承担跨聚合事务。
     - `003` 保持 forward-only，只新增表和索引，不修改已经发布的 `001`、`002`。M1 Prototype、Instance 和 AgentSpec 缺少技能树来源时按可选字段 `None` 读取，新 M2 对象再通过关系表保存精确 `SkillTreeRef`。
   - 易混点：数据库中存在 `instance_id` 不代表任意 report 都可挂载到该实例；JSON 能通过 Pydantic 校验不代表关系投影和 checksum 一致；保存多个对象的 Repository 方法都是 async 不代表它们自动共享事务，共享事务来自同一个 UoW connection。
   - 验证边界：M2.2 证明治理对象可以可靠 round trip、重启恢复并受当前外键与 checksum 约束，但不负责生成可信的服务端 Report 字段、执行规则、写治理审计、处理幂等或决定晋升/降级；这些编排从 M2.3 开始进入 Controller。
   - 后续应用：新增持久化来源关系时优先为稳定身份和高价值约束增加关系投影，同时保留完整 canonical payload；任何冗余列都必须在读取测试中验证与 JSON 一致；涉及多 Repository 的业务变化继续由 UoW 原子提交。

1. **M2.3 用三段式评估和服务端来源构造生成可追溯报告**

   - 日期：2026-07-22
   - 里程碑：M2.3
   - 主题：治理定义注册、事务外规则计算、最终写事务、双重幂等复查和最终人工复核。
   - 上下文：理解 M2.3 如何把 M2.1 的纯规则引擎与 M2.2 的治理 Repository 组合为实际应用服务，同时控制 SQLite 写锁时间、并发幂等、报告来源和人工复核边界。
   - 证据：`tests/integration/test_evaluation_controller.py` 覆盖 Suite/Tree 注册与精确引用、PASS/FAIL/REVIEW_REQUIRED、最终复核、历史 revision、幂等重放、审计脱敏和最终写事务回滚；`FactoryController.evaluate_instance()` 先在只读 UoW 中准备 Instance、Tree、Suite 和 AgentSpec，在事务外调用 `DeterministicRuleEngine`，再在最终写 UoW 中复查幂等并原子保存缺失 Spec、Report、AuditEvent 和 IdempotencyRecord。项目 owner 在问答中确认第一次幂等检查用于避免重复计算，最终事务中的第二次检查负责并发正确性；同时确认 report ID、Spec checksum 和时间必须由服务端从数据库事实构造，不能信任客户端提交。
   - 结论：
     - Suite 注册由 Controller 根据 Draft 计算 definition checksum，并补充 `created_at/created_by`；SkillTree 注册前逐个解析节点的精确 EvaluationSuiteRef。Prototype 绑定的 SkillTreeRef 再传播到 Instance revision 和 AgentSpec，形成评估所需的来源链。
     - 评估采用“只读准备 -> 事务外纯计算 -> 最终写入”三段式。只读阶段加载指定历史 revision、精确 Tree/Suite 和已有或候选 AgentSpec；纯规则计算不持有 SQLite 写锁；最终事务重新加载持久化来源并一次提交业务事实。
     - 第一次幂等检查位于规则计算前，是减少重复计算的快速重放；两个并发请求仍可能同时未命中，因此最终写事务必须再次检查。第二次复查与 Report/Audit/Idempotency 写入处于同一串行化事务，才是防止重复报告和重复审计的正确性边界。
     - 客户端只负责提交 case results、runtime model 和目标 Suite 引用。Controller 从 Repository 和系统端口生成 report ID、实际 instance revision、AgentSpec checksum、SkillTreeRef、EvaluationSuiteRef、开始/完成时间；这既减少主动伪造，也阻止客户端误传过期来源。
     - `DeterministicRuleEngine` 的决策顺序固定为：任一 HARD 失败或 SOFT 低于阈值则 FAIL；规则通过且 Suite 要求复核则 REVIEW_REQUIRED；其余为 PASS。人工复核不能把 FAIL 改为通过，默认路径也不生成或依赖 LLM JudgeSignal。
     - Report 不持久化完整 `output_text`，只保存 case-result checksum、可选 artifact URI 和有界 rule evidence；审计进一步只保存 allowlist 元数据。该设计降低自由文本泄露，但没有 artifact 时不能只凭 Report 独立重放原始输出。
     - `EvaluationReview` 是独立于 Report 的追加事实，只允许 REVIEW_REQUIRED 报告拥有一次最终 APPROVED/REJECTED 结果；应用检查与数据库唯一约束共同防止重复或反向改写。Review comment 不进入审计，但当前 reviewer 仍只是未经认证的 actor 标签。
     - 旧 revision 可以继续被评估，因为 Report 表达的是该快照的历史测量事实；报告是否仍能修改当前 head 属于使用时关系，M2.4 再通过 stale 检查禁止旧证据晋升新 revision。
   - 流程图：

     ```text
     快速幂等重放
            │ 未命中
            ▼
     只读 UoW：加载 Instance revision + Tree + Suite + Spec
            │ 关闭事务
            ▼
     DeterministicRuleEngine：事务外纯计算 EvaluationOutcome
            │
            ▼
     最终写 UoW：再次检查幂等
            │ 未命中
            ▼
     缺失 AgentSpec + EvaluationReport + AuditEvent + IdempotencyRecord
            │
            ▼
          单次 commit
     ```

   - 易混点：两次幂等检查不是重复代码，只有最终事务内的复查解决 check-then-act 竞态；服务端生成来源字段不代表 submitted evidence 真实；允许保存历史 revision 报告不代表该报告还能晋升当前实例；REVIEW_REQUIRED 不是 PASS，也不是 FAIL。
   - 验证边界：M2.3 能证明给定 evidence 下规则结果、服务端来源绑定、事务和幂等可重复，不能证明 evidence 来自真实 Agent 执行、runtime model 名称真实、reviewer 身份可信或报告对应永久能力。
   - 后续应用：耗时的纯计算继续移出 SQLite 写事务，但最终提交前必须复查并重新加载关键持久化事实；外部 Runtime Adapter 只提交 evidence，不得构造服务端 provenance；任何消费 Report 的操作都要在使用时重新检查 revision、Spec、Tree、Suite 和 Review 关系。

1. **M2.4 用显式晋升、来源重建和 CAS 生成新配置快照**

   - 日期：2026-07-22
   - 里程碑：M2.4
   - 主题：显式证据消费、纯晋升策略、配置来源校验、完整知识绑定和实例配置 checksum。
   - 上下文：理解 M2.3 的 EvaluationReport 如何在不修改历史事实的前提下，被显式晋升命令消费并转化为新的 AgentInstance revision，以及为什么晋升不能直接 patch 当前配置或只验证新增字段。
   - 证据：`tests/unit/domain/test_promotion.py` 覆盖状态、节点、父依赖、stale、Suite、PASS/FAIL/REVIEW_REQUIRED 和 Review 规则；`tests/integration/test_promotion_controller.py` 覆盖连续全量重建、必填知识、未知工具、旧 binding 来源保留、幂等、同 revision 并发单胜、事务回滚和 configuration checksum；`004_instance_configuration_checksum.sql` 为历史快照回填并要求后续 snapshot 保存独立配置 checksum。项目 owner 在问答中确认 Controller 重建当前配置是为了证明其确实来自声明的 Prototype 和 active nodes，并进一步理解完整知识校验不仅保留旧 binding 来源，还要发现缺失槽位、重复绑定和跨新旧集合的 cardinality/版本/模式问题。
   - 结论：
     - 规则通过只产生 EvaluationReport，不能自动改变实例；只有包含 target node、expected revision、report/review 引用、知识选择、actor 和幂等键的 `PromoteAgentCommand` 能触发晋升。这样把历史测量事实与配置变更意图区分开。
     - 纯 `PromotionPolicy` 不访问 Repository、clock 或 ID，只验证实例状态、技能树与目标节点、父依赖、当前 AgentSpec、Report provenance、目标节点 EvaluationSuite 和最终 Review。Controller 负责加载这些对象并管理事务，两层职责不能混合。
     - stale 是证据与当前 head 的使用时关系：Report 仍是其原 revision 的有效历史事实，但 instance/revision、Spec checksum、Tree 或 active nodes 与当前状态不匹配时，不得用于新 head 的晋升；系统不修改 Report 增加 mutable stale 标志。
     - 晋升前先从来源 Prototype 和当前完整 active node 集合重建 expected configuration，并与已保存配置比较。该检查证明当前快照符合业务来源；如果来源不明，即使 Report 与 Spec 字段彼此匹配，也不能继续演化。
     - 候选配置始终从 Prototype definition 和“旧 active nodes + target node”全量重建，而不是在 current configuration 上增量 patch。这样可防止 Prompt 重复、隐藏字段传播和配置漂移，并重新执行工具、知识槽、输出 Schema 的顺序与冲突规则。
     - SkillNode 的工具声明只是授权意图；候选配置中的全部工具仍需通过 ToolCatalog/ToolPolicy 解析。未知或越权工具会在 snapshot 写入前拒绝，不能因为来自已注册技能树就绕过白名单。
     - 知识校验面向完整候选定义和“现有 bindings + 新 selections”集合。只检查新增项无法发现其他必填槽遗漏、新旧组合超过 cardinality、重复引用或现有绑定的 checksum、版本、kind、injection mode 已不匹配。
     - 通过完整复验的旧 binding 原样保留原始 `bound_at/bound_by`，只有新增 selection 生成新的绑定来源；晋升只追加知识，不静默替换已有 binding，也不把旧知识伪装成本次重新绑定。
     - 晋升以 `expected_revision` 保存 `revision + 1` snapshot，并把 `skill.promoted` 审计和幂等结果放在同一 UoW 中。并发请求即使读取相同 head，也只有一个能通过 instance head CAS；失败事务的 snapshot、审计和幂等记录整体回滚。
     - M1 的“Instance configuration checksum 等于 Prototype checksum”只适用于未特化配置。`004` 增加独立 `configuration_checksum`：历史 M1 snapshot 可用 Prototype checksum 回填，新 revision 保存实际配置摘要，Repository 读取时重新计算并核对。
     - `configuration_checksum` 检查存储内容是否损坏；Controller 从 Prototype + active nodes 重建则检查配置是否来自声明的业务规则。错误程序即使同时改写 JSON 和 checksum，也不能由此证明来源正确，因此两层校验不可互相替代。
   - 易混点：Report 通过不等于已晋升；Review ID 存在不等于它属于当前 Report 且为 APPROVED；SkillNode 声明工具不等于工具已获系统白名单授权；旧 binding 被保留不等于可以跳过完整知识复验；checksum 一致不等于业务来源正确。
   - 验证边界：M2.4 证明给定当前快照和证据时，晋升判定、配置/知识重建、revision CAS、审计与幂等提交可重复；不证明外部 evidence 或 reviewer 可信，也不执行新 revision 的真实 Agent 任务。晋升不会自动生成语义能力保证。
   - 后续应用：所有消费历史证据并修改实例的操作都在使用时校验当前 revision 和完整 provenance；新增技能配置字段必须进入全量重建与 configuration checksum；需要替换知识时使用独立显式命令和审计语义，不把替换隐藏在晋升中。

1. **M2.5 用 revision 级观察窗口和单次证据消费实现确定性降级**

   - 日期：2026-07-22
   - 里程碑：M2.5
   - 主题：观察证据一致性、确定性阈值、依赖后代移除、revision 隔离、报告单次消费和原子降级。
   - 上下文：理解技能晋升后如何持续记录可审计的任务结果，并在不依赖 LLM 或后台自主决策的情况下，根据固定窗口和阈值生成可重复的降级快照。
   - 证据：`tests/unit/domain/test_degradation.py` 覆盖最小样本数、最新窗口、顺序无关性、连续失败、失败率、状态、active node、Suite/Spec/Report/Review 来源和 submitted passed 一致性；`tests/integration/test_degradation_controller.py` 覆盖未降级 revision 不变、目标及后代移除、独立分支保留、Prompt/工具/Schema/知识回退、Report 重放拒绝、审计故障整体回滚和并发跨阈值单胜；`005_task_outcome_integrity.sql` 增加 Report 单次消费唯一索引与 revision 级窗口索引。项目 owner 在问答中确认旧 revision 的结果不能直接评价新配置，并准确区分幂等键防止同一命令重试与唯一索引防止更换 task ID 后重复消费同一 Report。
   - 结论：
     - `RecordTaskOutcomeCommand.passed` 是调用方声明，不能直接进入窗口。`DegradationPolicy` 必须根据 Report 决策和可选最终 Review 重新推导 evidence_passed；两者矛盾时返回 `TASK_OUTCOME_MISMATCH`，防止调用方隐瞒失败或伪造失败触发降级。
     - Observation 只接受当前 head revision、当前 AgentSpec、精确 Tree/Suite、当前 active node 和匹配 Report/Review 的证据。旧 Report 可作为历史事实保留，但不能混入新配置的观察窗口。
     - 阈值算法先按 `recorded_at + task_id` 稳定排序并截取最新固定窗口；只有样本数达到 `minimum_samples` 后，尾部连续失败达到阈值或窗口失败率达到阈值任一成立才降级。稳定顺序使 Repository 返回顺序不影响结果。
     - 未达阈值时只追加 TaskOutcome、`task-outcome.recorded` 审计和幂等结果，`resulting_revision` 等于当前 revision；记录一次观察事实不会无意义地产生新配置快照。
     - 达到阈值时移除目标节点和它的所有已激活后代，因为父节点消失会破坏后代依赖；与目标无依赖关系的独立 active 分支继续保留，避免把局部能力失败扩大为全实例清空。
     - 降级配置从 Prototype 和剩余完整 active node 集合重新构建，并再次解析工具白名单；系统不通过删除 Prompt 字符串或反向撤销 patch 来猜测旧节点贡献，避免残留后代 Prompt、技能工具或 Schema override。
     - 新配置不再声明的技能知识槽对应 binding 被移除，知识包本身不删除；仍声明的 binding 重新验证 checksum 和 injection mode 后原样保留来源。降级收缩的是新 revision 的引用关系，不改写旧 revision 或知识包历史。
     - 窗口查询必须包含 `(instance_id, instance_revision, skill_node_id)`。revision 变化意味着 AgentSpec 可能变化；即使变化来自独立技能分支，Alpha 仍保守地重新积累样本，优点是证据边界明确，代价是丢弃部分可能仍可比较的历史观察。
     - Idempotency-Key 只识别相同 operation 和 request hash 的重试；调用方仍可更换 task ID、参数或幂等键重复引用同一 FAIL Report。`UNIQUE(evaluation_report_id)` 提供业务级单次消费约束，使一份 EvaluationReport 最多形成一个 TaskOutcome。
     - `005` 创建唯一索引时若历史数据已重复消费 Report，migration 必须整体失败并回滚，`schema_migrations` 不记录 v5；这避免实际 Schema 未满足新不变量却被标记为升级成功。revision 级复合索引则与真实窗口查询路径匹配。
     - 触发降级时，TaskOutcome、`task-outcome.recorded`、`DEGRADED` revision + 1 snapshot、`skill.degraded` 和 IdempotencyRecord 在同一 UoW 提交。任一步失败连本次 Outcome 也回滚；并发跨阈值请求依赖 expected revision/CAS，只允许一个产生新 head。
   - 易混点：自动降级不是后台 Agent 自主运行，而是在显式记录 TaskOutcome 时按纯规则同步判断；task ID 唯一不能阻止同一 Report 换 ID 重放；未降级时不增加 revision 不代表 Outcome 没有持久化；降级删除 binding 不等于删除知识包；`DEGRADED` 新状态不覆盖旧快照。
   - 验证边界：M2.5 能证明给定当前快照、可信来源关系和 submitted evidence 时，窗口、阈值、配置回退、事务与并发结果可重复；不能证明 evidence 由真实 Agent 任务产生，也不能证明阈值适合真实业务。当前单机 SQLite CAS 证据不代表分布式调度能力。
   - 后续应用：Runtime Adapter 接入后仍需为每个 Outcome 提供可验证 Report 来源；真实实验应根据误降级/漏降级数据校准窗口和阈值；只有出现明确且可证明安全的跨 revision 等价规则时，才考虑复用历史样本，默认继续保持 revision 隔离。

1. **M2.6 用薄 REST Adapter 和重启重放验证完整治理闭环**

   - 日期：2026-07-22
   - 里程碑：M2.6
   - 主题：治理 DTO、薄 Router、统一错误契约、持久化幂等重放和跨层退出证据。
   - 上下文：理解 M2.3-M2.5 已有应用服务如何通过最小 REST 契约对外暴露，以及关闭并重建应用后的重放测试如何证明治理状态来自 SQLite，而不是旧进程内存。
   - 证据：`tests/contract/test_m2_rest_api.py::test_m2_rest_governance_loop_survives_restart` 从空库仅经 HTTP 完成 Suite/Tree/绑定 Tree 的 Prototype/Knowledge 注册、克隆、`REVIEW_REQUIRED`、最终 Review、晋升、三次观察、降级、Spec 导出和审计查询；关闭第一个 app 后使用同一数据库创建新 app，精确读回 Suite、Tree、revision 3 AgentSpec 和审计，并用原幂等键重放 revision 1 Evaluation/Review/Promotion 与 revision 2 最终 TaskOutcome，响应与首次相同且审计未增加。`tests/contract/test_rest_api.py` 还固定八个 M2 路径在自定义 prefix 下的唯一允许方法；CI wheel 检查包含新增 Router。项目 owner 在问答中确认旧 Promotion/TaskOutcome 在当前 revision 3 下若没有持久化幂等重放应发生 revision 冲突，并理解 Router 复制业务规则会导致 REST、SDK、Tool adapter 形成多套治理真相。
   - 结论：
     - M2.6 只增加请求 DTO、Router 装配、OpenAPI/错误契约和完整 HTTP 退出测试，不重新定义 stale、Review、知识、工具、晋升或降级规则，也不新增 Repository 和 migration。
     - `RegisterPrototypeRequest` 增加可选 `SkillTreeRef`，使 HTTP 客户端能建立 Prototype -> Instance -> AgentSpec 的技能树来源链；Suite/Tree 注册查询、评估、复核、晋升和 TaskOutcome 共形成八个最小治理端点。
     - Router 只合并 path 中的实体 ID、body DTO 和 actor/idempotency header，调用 `validate_command()` 构造 transport-neutral Command，再交给 `FactoryController`。它不访问 Repository、clock、ID generator，也不在 HTTP 层重新判断业务条件。
     - DTO 只校验传输结构；Command 还组合 path/header，因此 `validate_command()` 把 Pydantic `ValidationError` 转为 `RequestValidationError`，使组合后的错误继续返回统一 `422 REQUEST_VALIDATION_FAILED`，而不是泄漏为 500。
     - 所有请求模型继续 `extra="forbid"`；错误响应只返回 location/message/type，不回显原始 evidence 或 review comment。`X-Actor-ID` 仍是不可信审计标签，统一 envelope 和脱敏不能替代认证授权。
     - Alpha 没有为测试便利增加通用 Report/Review/Instance GET。恢复证据由不可变 Suite/Tree GET、指定 revision AgentSpec、审计查询和写命令的持久化幂等重放共同组成，避免在需求未确认前扩大公共 API。
     - `ASGITransport` 不打开真实网络端口，但测试进入真实 FastAPI lifespan、migration、Container、Router、Controller 和文件型 SQLite，能够验证应用内部跨层契约；它不覆盖 TLS、反向代理、认证、网络故障或公网部署安全。
     - 重启后旧 Promotion（`expected_revision=1`）和 TaskOutcome（`expected_revision=2`）面对 revision 3 时，若被当作新请求必然冲突；返回首次 JSON 证明幂等响应已持久化，审计完全不变进一步证明请求没有重新执行或产生重复副作用。只断言 HTTP 200 不能提供这两层证据。
     - Router 复制 stale、Review 或阈值规则会造成 REST、未来 SDK 和 Tool adapter 行为漂移，并把检查放到 UoW 外形成竞态；共享 Controller 才能让不同入口得到相同规则、错误、事务、审计和幂等语义。
     - OpenAPI contract test 固定路径和唯一方法集合；wheel 内容检查确保新增 Router 与 003-005 migration 进入安装产物，避免源码环境测试通过但发布制品缺资源。
   - 易混点：`ASGITransport` 测试不是纯 Router 单元测试，也不等于真实网络部署测试；HTTP 200 不等于幂等重放成功，必须比较首次响应和副作用数量；统一 DTO 不等于业务规则应写在 DTO；`X-Actor-ID` 出现在 Review 中不代表 reviewer 经过认证。
   - 验证边界：M2.6 证明本地单机条件下完整治理链可经 HTTP 重放、重启恢复并保持稳定错误、幂等和审计契约，不证明外部 evidence、runtime model 或 reviewer 可信，也不证明 API 可安全暴露公网、支持多进程或具备 SDK/Tool adapter。
   - 后续应用：M3 SDK 和 Tool adapter 只负责把各自输入转换为相同 application Command，继续复用 `FactoryController`；认证接入后由服务端 Principal 替代自报 actor；增加公共查询端点前先确认真实调用需求、权限模型和分页/脱敏契约。
