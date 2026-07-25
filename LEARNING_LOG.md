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

1. **M3 用可信多入口和受限 Runtime 连接生产治理与实际执行**

   - 日期：2026-07-24
   - 里程碑：M3 全局
   - 主题：控制面、执行面、REST/SDK/Factory Tool 同构，以及 Factory Tool 与业务工具执行边界。
   - 上下文：理解 M1/M2 已有生产和技能治理闭环之后，M3 为什么既要增加可信调用入口，也要证明标准化 AgentSpec 能被受约束的 Runtime 消费；同时区分“上层 Agent 操作工厂”和“工厂产出的 Agent 执行业务工具”。
   - 证据：`tests/integration/test_factory_tool_adapter.py::test_rest_sdk_and_factory_tool_replay_the_exact_clone_result` 使用相同 Principal、命令语义和幂等键，证明 REST 首次执行后 SDK 与 Factory Tool adapter 精确重放同一个实例且不增加审计；`tests/integration/test_tool_execution.py::test_offline_demo_runtime_completes_with_one_audited_tool_call` 证明离线 Runtime 消费 AgentSpec 和已校验知识，并只经 ToolExecutor 调用已授权只读工具；`tests/integration/test_m3_exit_candidate.py::test_m3_public_workflow_survives_two_process_rebuilds` 从空库完成固定 Demo 主链并在两次应用重建后恢复 revision 5 AgentSpec、晋升重放和审计。项目 owner 在讲解问答中确认 Factory Tool adapter 的调用者是上层 Agent，而 ToolExecutor 服务于工厂产出的 Agent；进一步理解 SDK 走 HTTP 与 Factory Tool 直调 Controller 是为了验证不同接口边界。
   - 结论：
     - M3 包含两个相互连接但职责不同的平面。控制面通过 REST、Python SDK 和 Factory Tool adapter 操作 `FactoryController`，最终写入 SQLite 与 Audit；执行面由 Runtime Adapter 消费 AgentSpec 和精确知识来源，经 ToolExecutor 执行受限工具并返回 RunResult。
     - 三个控制面入口共享同一个 Controller，但路径刻意不同：SDK 真实经过 HTTP，用于验证 URL、Header、认证、DTO、错误 envelope、timeout 和 correlation ID；Factory Tool adapter 在可信宿主进程内把严格输入转换为 application Command 并直接调用 Controller，用于证明非 HTTP 入口不需要复制业务策略。
     - “入口同构”不能只比较删除动态字段后的近似 JSON。相同 Principal、业务参数和幂等键必须重放服务端首次生成的完整对象，包括 ID、时间和 checksum，同时审计数量不增加，才能证明入口共享同一业务事实和副作用边界。
     - Factory Tool adapter 与 ToolExecutor 不是同一种工具层。前者由上层 Agent 调用，用于列出原型、克隆、绑定知识、晋升和查审计；后者由 Runtime 调用，用于执行生产出的 AgentSpec 已授权的 `document-search` 等业务工具。两者的调用者、权限模型、数据模型和审计语义不能混用。
     - AgentSpec 是生产层交付物，不等于实例已经运行。Runtime 必须重新核对 instance/revision、Spec、知识 checksum 和上下文；工具请求还要经过授权、版本、定义、输入、超时和输出校验，不能因为工具名出现在模型输出中就直接执行。
   - 流程图：

     ```text
                            控制面
     调用方 ── Principal ──┬─ REST
                           ├─ Python SDK ─────► FactoryController ─► SQLite/Audit
                           └─ Factory Tool

                            执行面
     AgentSpec + 精确知识 ─► RuntimeAdapter ─► ToolExecutor ─► RunResult
                                                  │
                                                  └─ 仅执行 Spec 授权的固定工具
     ```

   - 易混点：SDK 是 HTTP 客户端而不是 Controller 的 Python facade；Factory Tool 是“操作工厂”的接口，不是产出 Agent 的业务工具；Runtime 状态迁移不证明外部执行器已经启动；默认离线 Runtime 可重复不代表真实模型输出确定。
   - 验证边界：M3 证明本地单机条件下的多入口语义、受限执行、工具安全、重启恢复和发布制品可重复，不证明静态 Token 可用于公网、真实 LLM 可靠、Agent 语义质量已保证、任意工具可安全执行或系统支持分布式运行。
   - 后续应用：新增接口适配器时继续只转换现有 Command/Query，并用跨入口精确幂等重放证明同构；新增 Runtime 或工具时继续以 AgentSpec、revision、来源 checksum、固定 Registry 和脱敏审计作为执行前置条件。

1. **M3.1 用单一身份真相源建立认证与授权边界**

   - 日期：2026-07-24
   - 里程碑：M3.1
   - 主题：Principal、Authenticator Protocol、角色权限矩阵、fail-closed 和可信审计 actor。
   - 上下文：理解为什么 M2 的 `X-Actor-ID` 只能作为可伪造标签，以及 M3.1 如何让 REST 和后续 Factory Tool adapter 共享传输无关的可信身份与授权语义。
   - 证据：`tests/contract/test_api_authentication.py` 分别验证未配置认证返回 503、缺失或错误凭据返回稳定 401 且 Token 不进入响应/日志、`X-Actor-ID` 被拒绝、角色矩阵保护读写和审计路由、审计 actor 只来自认证主体、全部非健康 OpenAPI operation 声明 Bearer security，以及独立 app 不共享认证配置；`tests/unit/test_authentication.py` 验证静态 Token 精确匹配、UnavailableAuthenticator fail-closed 和对象 repr 不暴露 Token；`tests/unit/application/test_security.py` 验证角色权限合并与稳定拒绝。项目 owner 在讲解问答中进一步确认服务端未配置认证与客户端凭据错误属于不同责任边界，并理解合法 Token 不能使第二个 `X-Actor-ID` 真相源变得可信。
   - 结论：
     - 认证回答“你是谁”：`Authenticator.authenticate()` 把不透明 Bearer credential 转换为不可变 `Principal(subject, roles)`；授权回答“你能做什么”：`AuthorizationPolicy` 把角色映射为 `factory:read`、`factory:write` 和 `audit:read` 权限。两者分离后，未来可以替换认证适配器而不改 Controller 或权限策略。
     - `Principal` 必须由可信接口边界产生，Router 构造写 Command 时只使用 `principal.subject` 作为 actor。继续接受客户端正文或 `X-Actor-ID` 会形成两个身份真相源，使调用方能够伪造审计主体，并让 Command、权限、幂等和追踪语义产生冲突。
     - Alpha 静态适配器启动时只保留配置 Token 的 SHA-256 digest，请求时计算候选 digest 并用 `hmac.compare_digest()` 比较，同时通过 `SecretStr`、`repr=False` 和固定错误消息降低凭据泄漏。这里不是用户密码存储方案，成立前提是服务端 Token 具有足够随机性。
     - 认证未配置时使用 `UnavailableAuthenticator`，服务对业务请求 fail-closed。503 `AUTHENTICATION_NOT_CONFIGURED` 表示服务端认证能力未就绪；401 `AUTHENTICATION_REQUIRED/FAILED` 表示服务已具备认证能力但当前请求没有有效身份；403 `AUTHORIZATION_DENIED` 表示身份有效但权限不足。
     - FastAPI 的 `HTTPBearer(auto_error=False)` 只负责解析 Authorization Header，错误由项目依赖显式生成，从而保持统一 envelope、`WWW-Authenticate: Bearer` 和 correlation ID。健康检查保持公开，业务路由再分别依赖 read、write 或 audit permission。
     - 当前 viewer/operator/auditor/admin 是固定 Alpha 角色矩阵，不包含用户目录、Token 轮换、撤销、过期、委托操作或租户隔离。需要代用户操作时应设计同时记录实际主体与被代理主体的显式 delegation，而不是恢复可任意填写的 actor Header。
   - 易混点：通过认证不等于拥有所有权限；401 与 403 的区别不是错误文本不同，而是身份是否已确认；SHA-256 digest 加常量时间比较不等于适合保存低强度密码；审计中有 actor 字段不等于 actor 来源可信。
   - 验证边界：M3.1 证明本地 Alpha 静态凭据、角色授权、错误语义和审计主体来源受约束，不证明凭据生命周期安全、抗暴力破解、支持多用户或服务可暴露到不可信网络。
   - 后续应用：后续接口统一传递 Principal，不允许模型或客户端提交 actor；引入 OIDC/JWT 时实现新的 Authenticator 并保持现有 Principal/Permission 边界，另行设计过期、轮换、撤销、issuer/audience 校验和租户隔离。

1. **M3.2 用受审计生命周期和不可变 Runtime 契约建立执行边界**

   - 日期：2026-07-24
   - 里程碑：M3.2
   - 主题：生命周期状态机、不可变 revision、CAS 并发控制、运行前就绪检查，以及 Runtime 执行权与工厂治理权分离。
   - 上下文：理解 Agent 实例的治理状态如何通过显式命令可靠变化，以及工厂为什么只把带精确来源的不可变 RunRequest 交给 Runtime，而不允许 Runtime 直接修改实例或持久化治理事实。
   - 证据：`tests/unit/domain/test_lifecycle.py` 覆盖完整状态转换矩阵、FAILED 重试标志、转换原因和禁止普通转换进入 DEGRADED；`tests/integration/test_lifecycle_controller.py` 覆盖转换幂等与审计、进入 RUNNING 后重新导出 AgentSpec、readiness 失败无副作用、stale revision、同 revision 并发单胜、审计失败整体回滚和重启后幂等重放；`tests/unit/application/test_runtime.py` 覆盖精确知识来源、知识内容 checksum、Runtime context revision、namespace 唯一性和 RunResult 终态一致性。项目 owner 在问答中确认 readiness 只证明配置可运行，状态转换后必须为新 revision 显式导出 AgentSpec；同时理解 Runtime 不拥有状态写入权，不能绕过 revision、审计、幂等和权限边界。
   - 结论：
     - `LifecyclePolicy` 是无 I/O 的纯领域策略：接收旧 AgentInstance、目标状态、原因、retry 和时间，按固定转换矩阵返回 `revision + 1` 的新快照，不覆盖旧 revision。`COMPLETED` 与 `TERMINATED` 是终态，`FAILED -> RUNNING` 必须显式声明 retry。
     - `DEGRADED` 不能由普通生命周期命令进入，因为它代表 M2 降级策略基于观察证据生成的治理结果；开放普通转换会允许调用方绕过阈值、配置回退和降级审计。
     - `TransitionInstanceCommand.expected_revision` 与 Repository 的 compare-and-swap 共同防止丢失更新。两个请求同时基于同一 head 转换时，最多一个能保存新 revision；另一个以 revision conflict 失败，不能生成竞争的同号快照。
     - 新 snapshot、`instance.transitioned` 审计事件和幂等结果在同一 UoW 中提交。审计失败会回滚状态变化；原幂等键在进程重启后仍返回首次结果且不重复追加审计，证明重放事实来自持久化层。
     - 进入 `RUNNING` 前的 readiness 会重新解析原型、知识绑定、工具权限、技能配置和候选 AgentSpec，但不持久化 Spec。它回答“当前配置能否运行”，不代表外部执行器已经启动，也不产生一次隐式 Spec 导出。
     - 生命周期转换会产生新 revision，因此转换前的 AgentSpec 立即成为旧 revision 交付物。调用方必须为新 revision 显式导出 AgentSpec，再构造 RunRequest；否则会发生当前实例为 revision 3、Runtime 却执行 revision 2 配置的来源错位。
     - `RunRequest` 要求 AgentSpec、解析后的知识集合和可选 RuntimeContextRef 在 instance ID、revision、Spec checksum、知识 ID/version/checksum/injection mode 上精确一致。缺失、多余、重复或内容被修改的知识都在进入适配器前被拒绝。
     - `RunResult` 绑定 task、instance revision 和 AgentSpec checksum，并约束完成时间、工具调用 ID 和终态错误语义：FAILED 必须有稳定 error code，COMPLETED 不得携带失败码。结果因此可以追溯到实际消费的不可变输入，但不自动等同于 Agent 语义质量正确。
     - `RuntimeAdapter.run()` 只执行已验证请求并返回 RunResult，不拥有工厂 Repository，也不自动把实例改为 RUNNING、WAITING、COMPLETED 或 FAILED。状态目标取决于上层业务编排，必须继续通过 Controller 的显式命令、CAS、审计和幂等边界完成。
   - 易混点：readiness 成功不等于 Runtime 已运行；实例状态为 RUNNING 不等于存在对应外部进程；RunResult 为 COMPLETED 不意味着实例必须直接进入 COMPLETED；AgentSpec 不可变不代表跨 revision 可复用；Runtime 返回来源字段不等于外部模型或执行环境本身可信。
   - 验证边界：M3.2 证明本地单机条件下生命周期规则、revision 并发控制、事务审计、重启重放和 Runtime DTO 来源一致性可重复，不证明外部执行器实际存活、真实 LLM 输出可靠、远程 Runtime 身份可信或分布式状态协调正确。
   - 后续应用：上层运行编排保持“显式转换状态 -> 导出当前 revision AgentSpec -> 解析精确知识 -> 调用 Runtime -> 根据结果显式转换状态”的顺序；新增 Runtime Adapter 时只实现执行协议，不直接访问工厂数据库或复制生命周期策略。

1. **M3.3 用异步 HTTP SDK 固化远程客户端契约**

   - 日期：2026-07-24
   - 里程碑：M3.3
   - 主题：HTTP 客户端边界、REST DTO 复用、operation manifest、correlation、显式幂等重试和脱敏异常。
   - 上下文：理解为什么 Python SDK 即使与服务端位于同一代码仓库，也必须通过 HTTP 使用公开 FastAPI 契约，而不是直接调用 `FactoryController`；同时理解网络结果不确定时为什么不能由 SDK 擅自重试写操作。
   - 证据：`tests/contract/test_sdk_operation_manifest.py` 验证不可变 manifest 的 20 个 method/path 与真实 OpenAPI 精确相等，并验证 SDK 重导出的 request model 与 REST 使用同一类型对象；`tests/integration/test_python_sdk.py` 使用 ASGITransport、真实 FastAPI lifespan 和文件型 SQLite 经 SDK 执行全部公开 operation，覆盖自定义 API prefix、重复查询参数、精确幂等重放和业务错误保真；`tests/unit/sdk/test_client.py` 覆盖 Client 生命周期、Header/DTO、标准与非标准错误、成功响应协议漂移、correlation 冲突、Transport 错误不重试、并发请求隔离和非法配置。项目 owner 在问答中确认 SDK 走 HTTP 是为了真实覆盖 URL、认证、DTO、状态码、correlation、幂等 Header 和 FastAPI 错误 envelope，并理解结果未知时不应由 SDK 替调用方做有副作用的重试决定。
   - 结论：
     - `AgentFactoryClient` 是公开异步 HTTP 客户端，不是 Controller 的 Python facade。它通过 HTTP 验证远程进程实际可见的 URL、认证、序列化、状态码、Header 和错误契约，也使客户端未来可以与服务端部署在不同进程或机器上。
     - SDK 直接重导出 `interfaces.api.contracts` 中的 Pydantic request model，因此 SDK 与 REST 共享同一个结构真相源，而不是维护字段相似的第二套 DTO。请求统一使用 `model_dump(mode="json")`，响应必须由声明的 Pydantic model 执行 `model_validate()` 后才能返回。
     - 不可变 `SDK_OPERATIONS` 为每个公开方法固定 HTTP method、路径模板、认证要求和 API prefix 范围。契约测试要求 manifest 与 OpenAPI operation 集合双向相等，从而同时发现 REST 新增但 SDK 遗漏、SDK 残留已删除接口以及 method/path 漂移。
     - Client 自己创建并拥有 `httpx.AsyncClient`，支持异步上下文管理器和幂等 `close()`；关闭后调用稳定抛出 `AgentFactoryClientClosedError`。只允许注入 Transport，避免外部共享 Client 的所有权和关闭责任不清。
     - 每次请求单独生成或接收 correlation ID，并验证响应 Header 与请求值一致；标准错误还要验证错误 body 中的 correlation。Client 不保存 `last_response` 或 `last_correlation_id`，所以同一 event loop 中并发调用不会因共享可变状态串扰。
     - 认证 operation 才发送内部 Bearer Token，健康检查不发送；SDK 永不发送 `X-Actor-ID`，服务端 actor 继续只来自认证 Principal。可选 `Idempotency-Key` 和 correlation 都是本次调用的局部输入，SDK 不暗中改写。
     - M3.3 不自动重试。网络超时可能发生在服务端提交之后、客户端收到响应之前，SDK无法判断事务是否已经生效；若生成新幂等键重试，服务端会把请求当成新命令，可能重复产生实例、状态变化和审计。调用方应根据业务决定是否使用原幂等键显式重试，以重放首次持久化结果。
     - `AgentFactoryApiError` 保留标准业务错误的 status、code、message、details 和 correlation；`AgentFactoryTransportError` 只保留安全的底层异常类型；`AgentFactoryProtocolError` 表示响应 JSON、Schema 或 correlation 违反声明；`AgentFactoryClientClosedError` 表示生命周期误用。非标准 HTML/空错误响应统一为 `SDK_HTTP_ERROR`，不复制原始正文、Header、Token、请求体或 traceback。
     - `base_url` 只允许带 host 的 HTTP/HTTPS origin 与可选路径，不允许 userinfo、query 或 fragment；API prefix 必须是规范绝对路径，路径变量逐段 URL encode。`follow_redirects=False` 避免携带 Authorization 的请求被自动转发到边界不明的地址。
     - M3.3 没有新增 migration 或服务端业务能力。它为已有 20 个 REST operation 增加类型化客户端和契约证据，但不实现同步 API、流式响应、大文件上传、自动分页或协议版本协商。
   - 易混点：SDK 与服务端共享 Python package 不等于应绕过 HTTP；共享 DTO 不等于业务规则写在 DTO 中；HTTP 超时不等于服务端未执行；不自动重试不等于不支持恢复；manifest 与 OpenAPI 相等只能防止接口清单漂移，不能单独证明业务断言充分。
   - 验证边界：M3.3 证明当前 SDK 与 FastAPI 在本地 ASGI 链路中的公开操作、类型、错误、correlation 和幂等语义一致，不覆盖 DNS、TLS、反向代理、真实网络中断或跨版本兼容，也不证明 API 已可安全暴露公网。
   - 后续应用：SDK 调用方为有副作用操作预先生成并持久保存幂等键，网络结果不确定时复用原键；新增或修改 REST operation 时同步更新 manifest、公开方法和 OpenAPI 集合测试；未来若引入同步或生成式 SDK，应继续保持同一 DTO、错误脱敏和显式重试边界。

1. **M3.4 用可信宿主上下文和统一适配管线开放工厂工具**

   - 日期：2026-07-24
   - 里程碑：M3.4
   - 主题：Factory Tool 契约、可信上下文、双重鉴权、Pydantic Schema、Command 映射、上下文恢复和跨入口幂等重放。
   - 上下文：理解上层 Agent 如何通过结构化工具操作工厂，同时防止模型伪造身份和审计来源、绕过权限或复制业务规则；并区分“操作工厂”的 Factory Tool 与“生产出的 Agent 执行业务工具”的 ToolExecutor。
   - 证据：`tests/unit/interfaces/factory_tools/test_factory_tool_contracts.py` 验证五项工具定义、Pydantic Schema、模型可见字段边界、权限过滤和结果 envelope 不变量；`tests/unit/interfaces/factory_tools/test_adapter.py` 验证 Command/Query 映射、鉴权顺序、默认与显式幂等键、输入/输出/意外错误脱敏、嵌套 correlation 恢复和任务取消传播；`tests/integration/test_factory_tool_adapter.py` 使用真实 SQLite 和 Controller 完成列表、克隆、知识绑定、晋升及审计查询，并证明 REST、SDK、Factory Tool 使用同一显式幂等键时精确返回同一个 AgentInstance 且克隆审计只产生一次。项目 owner 在问答中确认工具发现不能替代调用时鉴权，且鉴权先于参数校验可以减少未授权信息探测；进一步理解默认幂等键保障单一工具请求重试，显式键支持宿主控制和跨入口重放。
   - 结论：
     - M3.4 只开放 `list_prototypes`、`clone_agent`、`bind_knowledge`、`apply_promotion` 和 `query_audit_log` 五项现有工厂能力。Adapter 负责发现、鉴权、参数校验、上下文传播、Command/Query 转换和结果封装，业务规则、事务、revision、审计与持久化继续由 `FactoryController` 负责。
     - 模型生成的 `arguments` 是不可信输入；`FactoryToolCallContext(request_id, correlation_id, principal, idempotency_key)` 由完成认证的宿主注入。Principal、actor、request ID、correlation ID 和幂等键不得进入模型可见 input schema，否则 Agent 可以伪造身份、审计来源或重试边界。
     - 写命令的 actor 只取 `context.principal.subject`。Adapter 不接受 Bearer Token，也不从参数解析 actor；这延续 M3.1 的单一身份真相源，使权限主体、命令主体和审计主体保持一致。
     - 工具输入和输出 JSON Schema 直接由 Pydantic 模型生成。写工具输入继承现有 REST request model，并只补充原本位于 URL path 的资源 ID；审计工具刻意排除 actor 过滤字段，避免模型通过工厂工具查询指定主体的审计活动。
     - `definitions(principal)` 按权限过滤并排序工具，用于减少无关能力和改善调用体验，但不是安全边界。调用方可以跳过发现直接调用 `invoke()`，所以 Adapter 必须根据可信 Principal 再次鉴权。
     - `invoke()` 固定执行“解析工具 -> 鉴权 -> 参数校验 -> 设置 correlation -> 调 Controller -> 输出校验 -> 恢复 correlation”。鉴权位于详细参数校验之前，使未授权调用方不能通过不同校验错误探测受保护工具的字段和约束。
     - Adapter 只把已验证模型映射为现有 Command/Query。例如 clone 的原型状态、promotion evidence、知识槽、工具权限和 expected revision 均不在 Adapter 重判；直接复用 Controller 避免 REST、SDK 和 Factory Tool 形成三套治理真相。
     - 写工具默认使用 `tool:{tool_name}:{request_id}` 作为幂等键：request ID 使同一宿主请求重试可重放，tool name 区分不同工具。宿主显式键优先，可与 REST/SDK 共享同一键，从而跨入口精确重放服务端首次生成的完整对象而不重复副作用。
     - 调用 Controller 前通过 `CorrelationContext.set()` 写入宿主 correlation，并在 `finally` 中用返回的 Token `reset()`，因此正常返回、业务错误、意外异常和任务取消都不会污染外层上下文。`asyncio.CancelledError` 保持向上传播，不伪装成普通工具失败。
     - `FactoryToolResult` 强制成功时只有 output、失败时只有 error。输入错误只保留 location/message/type；未知工具、业务错误、输出 Schema 漂移和意外异常使用稳定 code，均不回显原始参数、异常文本、traceback 或敏感上下文。
     - Factory Tool adapter 运行于可信宿主进程并直接调用 Controller，不经过 HTTP；这与用于验证远程协议的 SDK 路径不同。它不实现 MCP Server、供应商 function-calling 方言、网络监听、业务 ToolExecutor 或独立 ToolCallRecord。
   - 易混点：工具未出现在 definitions 中不等于无法被直接调用；输入通过 Pydantic 校验不等于已有权限；Factory Tool 的 `apply_promotion` 不重新实现晋升策略；默认幂等键与显式跨入口键服务于不同重放范围；Controller 记录业务审计不等于已记录每次工具调用尝试。
   - 验证边界：M3.4 证明可信同进程宿主条件下五项工厂工具的 Schema、权限、上下文、错误和 Controller 复用可重复，并证明三种入口可以精确重放同一业务事实；不证明模型供应商工具方言兼容、远程工具主机可信、MCP 网络安全或产出 Agent 的业务工具执行安全。
   - 后续应用：宿主先认证并创建可信 FactoryToolCallContext，再把模型 arguments 交给 Adapter；新增工厂工具时复用同一鉴权、校验、上下文、输出和错误管线，并优先映射现有 application service；需要 provider 或 MCP 支持时只增加外层方言转换，不把供应商字段传入 Controller。

1. **M3.5 用不可变授权链和固定注册表实现受限 Runtime 工具执行**

   - 日期：2026-07-24
   - 里程碑：M3.5
   - 主题：ToolCatalog/ToolRegistry 分工、ToolExecutor 验证链、调用记录与审计、只读工具、离线 Runtime 和可选模型 Gateway。
   - 上下文：理解生产出的 AgentSpec 如何在不信任模型工具请求的前提下进入实际执行，以及为什么当前闭环只开放固定、只读、无文件和无网络副作用的 `document-search@1.0.0`，不能据此直接扩展到任意外部写工具或代码执行。
   - 证据：`tests/unit/test_runtime_tool_registry.py` 验证 Catalog/Registry 从同一 ToolDefinition 派生、Schema 与 Pydantic model 精确对应及 document-search 的确定性边界；`tests/integration/test_tool_execution.py` 覆盖持久化 Spec、当前 RUNNING revision、请求身份、授权、版本、definition、输入输出、超时、handler 异常、重复 call ID、记录与审计事务、篡改检测、重启恢复、离线 Runtime 和 fake model loop；`tests/unit/application/test_model_gateway.py` 与 `tests/unit/test_openai_gateway.py` 验证 provider-neutral turn 契约、工具结果回放、并行调用拒绝、非法响应和 provider 异常归一化。项目 owner 在问答中确认 Catalog 与 Registry 职责不同但不能维护两套元数据，也理解 handler 是进程内代码资源而不应持久化；同时准确识别执行前 call ID 查询与执行后唯一写入之间的 check-then-act 窗口，使当前实现只适用于重复执行无外部副作用的只读工具。
   - 结论：
     - `ToolCatalog` 只向生产层提供可授权的 `ResolvedToolSpec`，用于原型校验和 AgentSpec 导出；`ToolRegistry` 供 Runtime 按 name/version 查找 Pydantic input/output model 与真实 async handler。二者职责不同，不能合并成让领域层持有可执行代码的对象。
     - Container 先创建 Registry，再从同一 `ToolDefinition.resolved_spec()` 派生 Catalog。`RegisteredTool` 构造时比较 definition 中的输入输出 JSON Schema 与 Pydantic model 生成结果，防止生产时授权的契约和执行时净化的契约发生漂移。
     - Registry 不持久化到数据库。handler 是随部署制品加载的进程内代码，数据库无法恢复函数实现；保存一份可变 definition 只会形成数据库元数据与实际代码两个真相源。固定 Registry 构造后不支持动态注册，符合 Alpha 的白名单边界。
     - `ToolCallRequest` 必须携带 call/task ID、instance ID、revision、AgentSpec checksum、tool name/version 和 arguments；`ToolExecutionContext` 携带已导出 AgentSpec、精确匹配的已解析知识、actor 与 correlation。工具名只是请求，不能单独构成执行授权。
     - `ToolExecutor` 依次证明 Spec 已持久化、call ID 未使用、实例 head 仍为该 revision 且状态为 RUNNING、request 身份匹配 Spec、工具出现在 Spec、请求和 Registry 版本一致、Registry definition 与 Spec 完整元数据一致，再用 Pydantic 净化 arguments。任何模型或 Runtime 都不能跳过该链直接调用 handler。
     - 伪造或未持久化的 Spec 无法建立合法的记录外键，因此在确认 Spec 真实前的拒绝不写 ToolCallRecord；确认真实 Spec 后发生的授权、版本和输入拒绝记录为 `rejected`。handler/输出失败为 `failed`，超时为 `timed-out`，完整成功为 `succeeded`。
     - handler 在 `asyncio.timeout()` 中执行，结果还要经过声明的 output model 校验。外部 `CancelledError` 保持向上传播；timeout 属于协作式取消，不能中断恶意或阻塞 event loop 的同步代码，因此它不是 shell、任意插件或不可信代码的隔离机制。
     - `006_tool_call_records.sql` 用复合外键把调用绑定到 `agent_specs(instance_id, revision, checksum)`，并约束终态、result hash 与 error code 的组合。Repository 同时保存查询投影、规范 JSON 和 record checksum，读取时重新核对全部投影与模型摘要。
     - ToolCallRecord 只保存 arguments/result hash、状态、错误码、耗时、actor、correlation 和 Spec 来源，不保存原始参数、结果正文、Prompt、知识内容或凭据；记录与 `tool.called` 审计事件在同一短 UoW 中提交，任一失败则整体回滚。
     - 相同 call ID 已持久化时返回稳定冲突，不重放首次工具结果，因为系统只保存结果 hash。两个并发首次请求仍可能同时通过“未存在”检查并执行 handler，最终唯一约束只能阻止重复记录，不能撤销已发生的外部副作用。
     - 当前 `document-search` 只遍历 ToolExecutionContext 中 checksum 已验证且与 AgentSpec 精确匹配的 inline 知识，采用有界词项重叠、稳定排序和固定输出长度，不访问文件、网络、全局知识库或向量数据库。它验证工程闭环，不代表语义检索质量。
     - `OfflineDemoRuntimeAdapter` 即使没有工具调用也先验证持久化 Spec 与当前 RUNNING head；若已授权 document-search，则通过 ToolExecutor 执行一次检索，再生成固定 Writer 结构并按 AgentSpec output schema 本地校验。它返回 RunResult，但不修改实例状态、不持久化 RunResult、不访问网络。
     - 可选 `ModelRuntimeAdapter` 只通过 provider-neutral ModelGateway 接收单个工具申请或最终结构化输出。Runtime 为模型申请补齐内部 call ID、当前 revision、Spec checksum 和授权版本后仍交给同一 ToolExecutor；模型名称、provider call ID、轮数和最终 Schema 都受本地约束。
     - OpenAI gateway 位于 optional `llm` extra，默认 Container 不创建 client、不读取 API key、不联网；测试使用结构 fake。provider DTO 和异常不进入 application/domain，模型或 SDK 异常统一映射为稳定错误，不能把 provider 作为本地权限与 Schema 校验的替代品。
   - 易混点：AgentSpec 列出工具不等于可以直接调用 handler；数据库中存在工具名称不等于能恢复执行代码；唯一 call ID 只能阻止重复记录，不能阻止执行阶段的并发副作用；result hash 不等于可恢复结果；`asyncio.timeout()` 不等于进程沙箱；离线输出确定不等于真实模型输出确定或质量合格。
   - 验证边界：M3.5 证明本地单进程内固定、可信、输入有界的 async 只读 handler 可以经过 Spec/revision/Registry/Pydantic 授权链执行并留下脱敏可追溯记录；不证明外部写操作幂等、同步阻塞代码可取消、第三方插件可信、任意代码可隔离或真实 LLM 语义质量可靠。
   - 后续应用：开放付款、发信、写文件等副作用工具前增加执行前 reservation、outbox 或外部幂等协议，并根据风险加入进程/容器隔离、文件工作区和网络 allowlist；新增 Runtime 或 provider 只负责协议映射，继续让 ToolExecutor 和本地 output schema 成为最终执行与结果校验边界。

1. **M3.6 用固定可恢复工作流展示生产、执行与人工治理闭环**

   - 日期：2026-07-24
   - 里程碑：M3.6
   - 主题：Gradio 依赖边界、三阶段 Demo 状态机、revision 证据、checkpoint/幂等恢复、人工复核和脱敏展示。
   - 上下文：理解可视化 Demo 如何在不直接依赖 Controller、Repository 或 SQLite 的前提下，经公开 SDK 和 Runtime Adapter 串起固定 Writer 的生产、知识绑定、执行、评估、人工批准、晋升与审计；同时区分演示可重复性与通用 Agent 产品能力。
   - 证据：`tests/contract/test_demo_import_boundaries.py` 用 AST 固定 Demo package 只能依赖 SDK、Runtime contract 和自身 DTO，不能导入 domain、Controller、Repository、SQLite 或 Container；`tests/unit/interfaces/demo/test_demo_contracts.py` 验证固定 fixture/checksum、不可变 session checkpoint 和 phase evidence 不变量；`tests/integration/test_gradio_demo_workflow.py` 从空文件型 SQLite 达到 revision 5，覆盖中途失败恢复、非法顺序和异常脱敏；`tests/integration/test_m3_exit_candidate.py` 进一步验证两次应用重建后的最终 Spec、幂等晋升重放和审计恢复。项目 owner 在问答中理解 revision 3 到 4 只改变生命周期状态，因此固定 Demo 可把 revision 3 输出作为 revision 4 当前治理快照的评估 evidence，但该结论不能推广到配置变化的任意 revision；同时理解 completed operations 保存客户端进度，稳定幂等键解决服务端已提交但客户端结果未知的窗口，二者不能互相替代。
   - 结论：
     - Gradio 不是新的业务入口。`DemoWorkflow` 的生产和治理操作必须通过 `AgentFactoryClient` 走 HTTP，任务执行必须通过注入的 `RuntimeAdapter`；最外层 `agent_factory.demo` 只负责装配 Container、SDK、Runtime 和页面，不实现业务规则。
     - Demo 固定 Prototype、Knowledge、SkillTree、EvaluationSuite、promotion node、Runtime、工具、任务和输出要求，不提供任意 Prompt、知识、工具或模型参数输入。固定场景把失败原因限制在可验证的工程链路，避免演示退化为输入与模型波动不可归因的通用 Playground。
     - `DemoSession` 是严格、不可变、可复制的 UI DTO，状态机为 `NEW -> READY_TO_RUN -> AWAITING_REVIEW -> PROMOTED`。模型不变量要求进入高级 phase 前已经保存对应 instance、revision、Spec、RunResult、report、review 和 active node 证据；按钮禁用只改善体验，Workflow 方法仍在服务端调用前检查合法 phase。
     - 初始化阶段依次检查 readiness、注册 Suite、用真实 Suite checksum 注册 Tree、注册并发布 Prototype、注册 Knowledge、克隆 revision 1、验证未绑定知识时 Spec 导出以 `MISSING_KNOWLEDGE_BINDING` 失败、绑定到 revision 2、导出验证 Spec、迁移 RUNNING revision 3 并重新导出 Spec。
     - 预期失败也是验收证据：只接受 `MISSING_KNOWLEDGE_BINDING` 证明知识槽约束实际由服务端执行；若未绑定即可导出或返回其他错误，Workflow 以稳定 Demo invariant 失败停止，不能把所有失败都误当成测试通过。
     - 运行阶段用 revision 3 AgentSpec、固定 UUID5 task ID 和 checksum 已验证知识构造 RunRequest，经离线 Runtime 与 ToolExecutor 得到实际 RunResult；随后实例转为 WAITING revision 4、重新导出 revision 4 Spec，再提交真实 content、structured output 和 tool-call evidence，且只接受 `REVIEW_REQUIRED`。
     - revision 3 到 revision 4 只由 `RUNNING -> WAITING` 生命周期转换产生，Prototype、Prompt、知识、工具、output schema 和 active nodes 不变，因此固定 Demo 可以使用 revision 3 的执行输出评估 revision 4 的当前治理快照。该前提不适用于晋升、降级、知识更新或其他配置变化；旧输出默认不能评价任意新 revision。
     - 评估报告不会自动晋升。第三步必须由用户显式触发 APPROVED Review，再用当前 revision、report 和 review 晋升 `mid-writer`，得到 revision 5、保持 WAITING 且 active nodes 精确为目标节点；Evaluation、Review 与 Promotion 因而是三个独立审计事实。
     - 每完成一个子操作，Workflow 立即产生新不可变 checkpoint，并把 operation 加入 `completed_operations`；恢复时据此跳过已完成步骤并保留 instance/report/review ID、Spec、RunResult 和来源 checksum。`gr.State` 只是当前浏览器会话控制状态，SQLite 中的快照、工具记录和审计才是持久事实。
     - 每个写操作使用 `demo:{workflow_id}:{operation}` 稳定幂等键。若服务端已经提交但响应丢失或 checkpoint 尚未更新，重试原键会返回首次结果；completed operations 解决“客户端从哪里继续”，幂等键解决“服务端是否已执行未知”，只保留任一机制都会留下恢复缺口。
     - 稳定幂等只覆盖 Controller 写命令。RunResult 本身不持久化，页面刷新不会恢复完整 session；若 Runtime 完成后进程在 checkpoint 前退出，固定只读运行仍可能重新执行。M3.6 不通过猜测数据库状态自动重建跨刷新工作流。
     - 页面只显示来源 ID/version/checksum、RunResult 身份与有界摘要、工具调用数量及审计投影；完整知识、system prompt、原始 SDK response、审计 payload、Token、异常文本和 traceback 不进入页面。未知异常只记录 workflow ID 与异常类型并返回 `DEMO_INTERNAL_ERROR`。
     - Gradio 通过 optional extra 惰性导入；未安装时核心 API、SDK 和 Runtime 仍可使用。Launcher 强制 API 为 loopback、页面绑定 `127.0.0.1`、关闭 share/error/monitoring 并启用 strict CORS；API 与 UI 两个本地进程共享文件 SQLite 只服务串行演示，不构成多用户调度或公网部署。
   - 易混点：Gradio 能完成业务操作不等于它可以直接调用 Controller；按钮不可点击不等于后端无需 phase 校验；revision 数值相邻不等于配置必然等价；checkpoint 不等于服务端事务；幂等键不等于持久化完整 UI 会话或 RunResult；离线 Writer 结果稳定不等于内容质量已经验证。
   - 验证边界：M3.6 证明固定 Writer 场景能在本地经真实 SDK/FastAPI、文件 SQLite、离线 Runtime 和人工按钮完成 revision 1 到 5 的可追溯闭环，并能从部分会话故障恢复；不证明页面刷新恢复、多用户并发、真实模型质量、远程 Runtime 调度、Gradio 公网安全或通用 Agent 配置能力。
   - 后续应用：演示新增步骤时继续通过 SDK/Runtime 端口、为每项写操作分配稳定键并在成功后立刻 checkpoint；跨刷新恢复需设计持久化 workflow/run record，而不是扫描数据库猜测；若转为真实产品，应拆分身份、租户、任务队列、Runtime 租约与部署安全，不能直接扩展当前共享 SQLite 和静态 Token 页面。

1. **M3.7 用重建恢复、发布制品和远程 CI 形成阶段退出证据**

   - 日期：2026-07-24
   - 里程碑：M3.7
   - 主题：退出候选主链、跨 App 重建、AgentSpec 导出幂等、wheel 资源、optional extras 隔离安装和跨平台 CI。
   - 上下文：理解业务测试通过后为什么仍需验证应用重建、实际 wheel 内容和干净环境安装，以及本地门禁与远程 Linux CI 分别能排除哪些假阳性；同时避免把同一 pytest 解释器中的 App 重建夸大为真正多进程或崩溃恢复。
   - 证据：`tests/integration/test_m3_exit_candidate.py` 从空文件型 SQLite 经第一组 App/Container 完成固定 Demo 至 revision 5，在第二组重建中精确恢复 Suite、Tree、Prototype、Promotion 幂等响应、审计与 revision 5 AgentSpec，再经第三组重建证明相同 revision Spec 精确重放且不增加第二条 `spec.exported`；`.github/workflows/ci.yml` 固定 locked dependency sync、Ruff、mypy strict、pytest branch coverage、sdist/wheel、包资源清单和隔离 `[demo,llm]` extras 安装；M3.7 封存记录为 `351 passed`、domain/application/total branch coverage 96%/94%/92%，GitHub Actions CI #20 通过。项目 owner 在问答中理解源码测试验证工作区行为，而隔离 wheel 验证实际交付物，二者不能互替；并准确区分跨 App/Container 持久化恢复与真实 OS 多进程、强制崩溃恢复。
   - 结论：
     - M3.7 不增加新的生产、治理或 Runtime 功能，而是把 M3.1-M3.6 的契约组合成阶段退出候选。退出标准不仅要求主链成功，还要求既有 M1/M2 回归、静态检查、覆盖率、构建、资源打包、optional extras 和远程 CI 同时通过。
     - 第一个 App 从空库完成固定 Writer 主链并得到 revision 5，同时保存 Suite、Tree、Prototype page、Promotion 幂等响应和完整审计作为比较基线。测试比较完整 Pydantic 对象和事件集合，而不是只断言 HTTP 200 或少数字段。
     - 关闭第一个 App 后，第二个 App 使用相同 SQLite 但重新创建 Container、Repository 与 Controller；它必须精确读取旧治理对象、用原键重放同一个 Promotion 结果且不增加审计，并导出来源、知识、技能节点和工具均正确的 revision 5 AgentSpec。这排除了对象只存在于旧应用实例内存中的主要假阳性。
     - 第二个 App 首次导出 revision 5 时只增加一条 `spec.exported`；第三个 App 再次导出必须返回完全相同 Spec 且审计集合不变。由此同时验证 AgentSpec 持久化恢复、同 revision 导出幂等和审计副作用单次发生。
     - 该测试严格来说是在同一 pytest Python 进程内顺序创建三个 App，而不是启动三个独立 OS 进程。它会重建应用对象，但解释器、导入模块和潜在模块级全局状态仍存在；关闭也经过正常 lifespan，而不是在任意指令或事务中途强制终止。
     - 因此该证据支持“跨 App/Container 重建后从 SQLite 恢复”，不支持“多进程并发协调、进程崩溃恢复、断电持久性、分布式锁、任务租约或真实网络故障已验证”。真正验证这些能力需独立进程、故障注入和并发测试环境。
     - 源码测试运行的是工作区源码加开发/测试依赖，能深入验证业务规则、事务和异常，但本地 `src` 路径可能掩盖 wheel 遗漏模块、migration、metadata、entry point 或运行依赖。测试通过不等于用户安装的制品完整。
     - wheel 隔离安装验证实际交付物，但 import/entrypoint smoke test 不能替代业务测试。两类证据分别回答“代码行为是否正确”和“发布给用户的制品是否完整”，必须同时保留。
     - CI 构建 sdist/wheel 后直接检查归档资源清单，覆盖核心 domain/application、SDK、Factory Tool、Runtime、Demo、SQLite repository 和 001-006 migration，防止源码环境能读取资源而 wheel 安装后启动失败。
     - 隔离安装先用 `uv export --locked --extra demo --extra llm --no-dev --no-emit-project` 从锁文件得到不含项目本体的依赖，再在新 venv 安装依赖，最后以 `--no-deps` 安装刚构建的 wheel。这样项目代码只能来自 wheel，不会被 editable checkout 或当前工作目录遮蔽。
     - 安装后同时导入 `agent_factory.demo`、Gradio 和 OpenAI SDK，并检查 distribution metadata 中存在 `demo`/`llm` extras 与 `agent-factory-demo = agent_factory.demo:main` entry point；这比只执行 `import agent_factory` 更能覆盖可选发布面。
     - CI 对 domain、application 和全项目 branch coverage 分别设置 90%、85% 和 80% 最低门槛。M3.7 记录的 351 tests 与 96%/94%/92% 是当时提交的封存快照，不是未来修改后的永久保证，后续提交仍需重新通过门禁。
     - Ubuntu CI 暴露 Gradio 动态 `Button.click` 的跨平台 typing 差异。修复使用局部 `_ClickableButton` Protocol 和 `cast` 表达第三方动态边界，没有关闭全局 mypy strict 或忽略全部 `attr-defined`，使放宽范围局限在可解释接口。
     - mypy 输出经 `tee` 写日志时必须读取 Bash `PIPESTATUS[0]` 并以该状态退出，否则 pipeline 可能返回 `tee` 的成功码而吞掉 mypy 失败。CI annotations 改善可读性，但不能改变真实命令退出状态。
   - 易混点：本地测试通过不等于 wheel 完整；wheel 可导入不等于业务行为正确；重新创建 FastAPI app 不等于重启 Python 解释器；正常 lifespan 关闭不等于崩溃恢复；覆盖率达到门槛不等于断言充分；远程 CI 通过不等于公网部署安全。
   - 验证边界：M3.7 证明固定 M3 主链、持久化幂等与审计在跨 App 重建后可恢复，当前 wheel 包含声明资源并能在锁定依赖的隔离 Linux 环境安装，质量门禁可由 GitHub Actions 重复执行；不证明多进程 SQLite 写入、强制崩溃恢复、真实网络部署、容器隔离、生产身份或真实 LLM 可靠。
   - 后续应用：每个里程碑结束时同时保存行为回归、重启/故障证据、构建归档检查和隔离安装 smoke test；新增 package data、extra 或 console script 时扩展 wheel 验证；需要宣称多进程或崩溃恢复前，增加独立 OS 进程、kill/fault injection、数据库耐久性与竞争写入测试，不能沿用 App 重建结果推断。

1. **M4.2 用稳定语义快照把公共契约变化变成显式评审事件**

   - 日期：2026-07-25
   - 里程碑：M4.2
   - 主题：OpenAPI、AgentSpec 与审计时间线的稳定语义投影，以及 `--check`/`--write` 的职责分离。
   - 上下文：理解为什么回归快照不能直接保存每次运行产生的 UUID、时间戳等动态数据，以及 CI 为什么只能报告契约漂移，不能自动接受新的基线。
   - 证据：`scripts/contract_snapshots.py` 生成 `docs/generated/openapi-v1.json`、`tests/regression/snapshots/writer-agent-spec-v1.json` 和 `tests/regression/snapshots/writer-audit-timeline-v1.json`；`tests/regression/test_contract_snapshots.py` 验证稳定投影、连续生成字节一致、缺失/漂移失败和显式写入；CI 在测试前运行 `python -m scripts.contract_snapshots --check`。
   - 结论：
     - 快照比较的对象应是调用方依赖的稳定语义，而不是原始运行记录。动态 UUID、绝对时间和环境路径必须被固定、归一化或排除，否则快照只能制造无意义噪声。
     - 规范 JSON 使用固定 UTF-8 编码、键排序、缩进和末尾换行，使字节差异能够对应可审查的契约差异；连续生成字节一致是确定性证据，不等于契约设计本身正确。
     - `--check` 是只读门禁：快照缺失或发生字节漂移时返回非零退出码；`--write` 是人工确认后的基线替换操作。CI 不应自动执行 `--write`，否则真正的破坏性变更会被测试流程自行接受。
     - 后续快照变更需要按兼容性说明 PATCH、MINOR 或 MAJOR 影响。版本标签是评审语言，不代替对消费者行为和迁移方案的分析。
   - 易混点：快照变化不一定是缺陷，但必须被解释；快照未变化也不能证明所有行为兼容；OpenAPI 一致不代表 SDK、Factory Tool、运行时副作用或数据库语义全部一致。
   - 后续应用：新增公开字段、错误码、路由、AgentSpec 语义或审计投影时，先运行 `--check` 查看差异，人工确认兼容性与变更级别后再显式更新快照，并把原因写入提交说明。

1. **M4.3 安全回归必须覆盖真实信任边界和信息泄漏面**

   - 日期：2026-07-25
   - 里程碑：M4.3
   - 主题：fail-closed 认证授权、服务端身份来源、请求体边界、敏感数据最小化和默认 Runtime 能力。
   - 上下文：理解安全测试为什么不能只验证“非法参数返回 4xx”，而要证明未认证请求不会进入 Controller 或工具 handler、客户端不能伪造 actor，并且错误响应、日志和 UI 不泄漏凭据或完整业务内容。
   - 证据：`tests/security/test_api_security.py` 覆盖 Bearer Token、权限、correlation、安全响应头、非法/重复 `Content-Length` 与流式请求体上限；`tests/security/test_sensitive_data_boundaries.py` 扫描响应、日志、Demo 和持久化边界；`tests/security/test_runtime_capabilities.py` 证明默认注册表只开放固定只读工具且不建立外部 socket；`docs/design/security-regression-gates.md` 记录信任边界和证据矩阵。
   - 结论：
     - 认证和授权必须早于详细业务参数校验与 Controller/handler 调用。这样既阻止副作用，也避免未授权调用方利用校验差异探测受保护 Schema 和业务状态。
     - 审计 actor 必须由认证后的服务端上下文构造；客户端提供的 actor、owner 或标签只能视为不可信输入，不能直接成为责任归属证据。
     - 请求大小需要同时校验声明的单个非负十进制 `Content-Length` 和实际流式读取字节数。只相信 Header 会被 chunked body 或错误长度绕过，只限制读取则会浪费处理资源并延迟拒绝。
     - 错误响应、结构化日志、审计记录和 Demo 只保留稳定错误码、身份摘要、hash 与 correlation，不保存 Token、完整 Prompt、知识正文、原始工具参数/结果或 traceback。
     - 默认 Runtime 能力应保持封闭：当前只注册固定、只读、无文件与网络副作用的工具。网络 socket 测试证明的是默认执行路径没有联网，不是操作系统级网络沙箱。
   - 验证边界：M4.3 的 12 项专门安全测试证明当前 Alpha 的本地单用户入口和默认 Runtime 能力遵守已声明边界；不证明 OIDC、TLS、WAF、租户隔离、密钥轮换、容器沙箱或公网抗攻击能力，因而不能把“安全门禁通过”表述为“可安全公网部署”。
   - 后续应用：每增加一个公开入口、凭据来源、工具副作用或展示字段，都要先更新信任边界和数据分类，再补充入口前置拒绝、敏感信息扫描及最小权限测试。

1. **M4.4 用事务阶段故障注入证明原子性，而不是从正常路径推断**

   - 日期：2026-07-25
   - 里程碑：M4.4
   - 主题：UoW 阶段故障、业务事实与审计/幂等原子提交、并发首次写入和 migration 回滚。
   - 上下文：理解一条写命令在正常测试中成功，只能证明 happy path；要声称原子性，必须在实体、审计、幂等记录和最终提交之间主动制造失败，并检查所有可观察状态都没有留下半成品。
   - 证据：`tests/integration/test_transaction_fault_injection.py` 通过测试专用 UoW/Repository wrapper 在 `after entity`、`after audit`、`after idempotency` 和 `before commit` 注入异常，覆盖生产、治理与工具记录写能力；同一测试集验证并发 AgentSpec 导出仅产生一份 Spec/审计，以及临时 `007` migration 失败不会留下 DDL 或历史记录。`docs/design/transaction-fault-evidence.md` 保存写能力矩阵、判定口径和限制。
   - 结论：
     - fault injector 位于测试装配边界，不改变生产 Controller API 和领域规则；它观察真实 UoW 阶段，使测试能够稳定复现原本依赖时序的失败窗口。
     - 任一阶段失败后，实体 revision/head、审计事件和幂等记录必须整体保持原状。只断言抛出异常不充分，因为数据库仍可能已经提交部分事实。
     - `ToolCallRecord` 与 `tool.called` 审计必须在同一短事务提交；否则可能出现工具调用有记录但无审计，或审计声称发生但没有对应调用事实。
     - 并发首次 AgentSpec 导出需要依靠最终事务和数据库唯一约束收敛为一份事实；事务外预查只能优化性能，不能消除 check-then-act 竞态。
     - migration 只有在 DDL 与 `schema_migrations` 历史同时提交后才算成功。临时错误的 pending migration 必须整体回滚，修正同一未应用脚本后才能重试；失败版本不能留下虚假的成功历史。
   - 易混点：`before commit` 注入验证应用事务回滚，不等于模拟断电、进程被强杀或 SQLite WAL 耐久性；本地事务能撤销数据库记录，不能撤销已经发生的付款、发信或文件修改等外部副作用。
   - 验证边界：M4.4 的 34 项事务故障测试为当前 SQLite 单进程写链提供阶段级回滚证据；不证明多进程竞争写入、操作系统崩溃恢复、磁盘故障或外部系统 exactly-once。
   - 后续应用：新增写能力时先列出实体、审计、幂等和外部副作用，再把每个数据库提交阶段纳入故障矩阵；外部写工具需另外设计 reservation、outbox、远端幂等键或补偿协议。

1. **M4.5 用隔离 wheel 和真实进程重启验证实际交付物**

   - 日期：2026-07-25
   - 里程碑：M4.5
   - 主题：源码与发布制品隔离、锁定依赖、真实 Uvicorn/HTTP/SDK 链路和 SQLite 重启恢复。
   - 上下文：理解 M3 的 wheel import smoke 为什么仍不足以证明用户安装后的服务可以运行，以及如何排除当前工作目录、editable package、`PYTHONPATH` 和已导入模块造成的源码遮蔽。
   - 证据：`scripts/local_alpha_smoke.py` 作为标准库编排器，不从工作区导入 `agent_factory`；脚本重新构建 sdist/wheel，检查 metadata、extras、entry point 与 001-006 migrations，在分别由 `uv.lock` 导出的 minimal 和 `[demo,llm]` 隔离环境中以 `--no-deps` 安装 wheel；随后在仓库外启动两次真实 Uvicorn，第一次通过已安装 SDK 跨 HTTP 写入 Prototype，第二次从同一文件 SQLite 读取恢复。
   - 流程图：

     ```text
     工作区源码
         │ 构建并检查归档
         ▼
     sdist + wheel
         │ 锁定依赖、隔离安装、移除源码路径影响
         ▼
     wheel-only 环境
         │ 启动 Uvicorn #1，SDK 经 HTTP 写入
         ▼
     文件型 SQLite
         │ 关闭并启动 Uvicorn #2，SDK 经 HTTP 读取
         ▼
     恢复相同 Prototype 业务事实
     ```

   - 结论：
     - 源码测试证明工作区代码行为，隔离安装证明归档内容和发布元数据，真实进程 smoke 证明 console/ASGI 启动、网络协议、SDK 和持久化能够组合运行；三类证据回答的问题不同，不能互相替代。
     - wheel 使用 `--no-deps` 安装，是为了确保依赖集合来自锁文件导出、项目代码只来自刚构建的制品，而不是让安装器重新解析一套可能不同的依赖结果。
     - minimal 与 `[demo,llm]` 必须使用不同隔离环境，才能同时证明核心安装不偷偷依赖 optional package，以及完整 extra 的声明和导入路径确实可用。
     - 第二个独立 Uvicorn 进程能够读取第一个进程写入的 Prototype，排除了数据只保存在 Python 对象或旧 Container 内存中的假阳性；但它仍是正常关闭后的单机串行恢复，不是强制崩溃恢复。
   - 验证边界：本地部署拓扑是单机、单 Uvicorn、单文件 SQLite、loopback；脚本不证明公网服务、反向代理、多 worker、多主机、容器镜像、TLS 或生产数据库可用。
   - 后续应用：发布面新增模块、migration、extra 或 entry point 时同步扩展制品检查；需要扩大部署声明前，分别增加容器、反向代理、多进程数据库和强制终止恢复证据。

1. **M4.5 资源释放和进程退出必须依据 API 与平台语义判断**

   - 日期：2026-07-25
   - 里程碑：M4.5
   - 主题：`sqlite3.Connection` 生命周期、跨平台 Uvicorn 关闭语义和受约束临时目录清理。
   - 上下文：真实进程 smoke 暴露三个仅靠单进程或单平台测试不容易发现的问题：数据库事务上下文结束不等于连接已关闭；Windows 与 POSIX 信号后的非零退出码都不能脱离关闭日志直接解释为服务异常。
   - 证据：首次 M4.5 运行中，Uvicorn 收到 `CTRL_BREAK_EVENT` 后完整打印 application shutdown 日志但返回 Windows 平台退出码 3；第二次运行完成业务阶段后，残留 SQLite handle 阻止清理运行目录。远程 CI #24 又发现 Uvicorn `0.51.0` 在 Ubuntu 完成相同 shutdown 后返回 `-SIGTERM`。其 `capture_signals()` 在 lifespan 结束后恢复原 handler，再通过 `signal.raise_signal()` 重新抛出捕获信号。`scripts/local_alpha_smoke.py` 最终使用 `contextlib.closing()` 关闭连接，并只在对应平台退出码与两条完整关闭标记同时匹配时接受非零结果。
   - 结论：
     - `with sqlite3.connect(...) as connection` 管理的是事务：正常退出提交、异常退出回滚；它不会自动调用 `connection.close()`。需要确定释放文件 handle 时，应叠加 `closing()` 或显式关闭连接。
     - 进程退出码必须结合所使用的信号、平台和完整生命周期日志解释，但不能宽泛忽略所有非零值。当前兼容条件只接受 Windows 码 3 或 POSIX `-SIGTERM`，且都必须同时出现 `Application shutdown complete.` 与 `Finished server process`；错误平台、其他信号和缺失日志仍失败。
     - 临时目录清理必须先解析并验证目标位于专用工作根目录、名称符合唯一 `run-*` 子目录，再进行有限次数重试；不能为追求测试清理成功而递归删除未经确认的计算路径。
     - 启动失败、业务失败和用户中断都要进入同一 `finally` 清理路径，优先终止已发布的子进程并释放数据库连接，避免留下后台服务和锁定文件。
   - 易混点：事务上下文不等于资源上下文；完整关闭日志不意味着任意非零码都可忽略；清理失败也不应通过无限重试或扩大删除范围掩盖。
   - 后续应用：涉及文件数据库、子进程、socket 或临时目录的脚本，都要分别设计资源所有权、关闭顺序、超时、平台差异和失败后的有界清理策略。

1. **M4.6 用单一发布入口收敛 CI，同时保留可诊断的独立门禁**

   - 日期：2026-07-25
   - 里程碑：M4.6
   - 主题：动态 wheel 资源检查、CI 去重、专门风险门禁和本地/远程证据分层。
   - 上下文：理解减少 CI 重复代码不等于把所有检查合并成一个不可诊断的黑盒，以及为什么源码资源清单应从当前 package 树推导，而 migrations、metadata、extras 和 entry point 仍需要显式断言。
   - 证据：`source_package_resources()` 动态遍历 `src/agent_factory/**/*.py` 并推导 wheel 内预期路径；`.github/workflows/ci.yml` 从 16 个步骤收敛到 14 个，统一调用 `scripts.local_alpha_smoke`，但继续单独执行 security 和 transaction suites；最终本地候选验证为 Ruff/mypy 覆盖 151 个文件、12 项安全测试、34 项事务测试、总计 409 项测试，以及 domain/application/全项目 branch coverage 96%/94%/92%。远程 CI #23 和 #24 分别暴露 Linux typeshed 与 POSIX shutdown 差异，修复后的 `4a55d73` 由 CI #25 完整通过。
   - 结论：
     - 手工维护生产模块清单会随新增文件漂移；从源码 package 树动态推导能让“新增 `.py` 却未进入 wheel”自动失败。非 Python 资源和发布元数据无法由该遍历推断，仍需显式检查。
     - CI 应复用与开发者相同的跨平台发布脚本，避免本地和 YAML 内联逻辑形成两个真相源；脚本本身需要单元测试，否则“统一入口”只会集中未知错误。
     - 安全和事务套件虽然也包含在完整 pytest 中，仍保留独立步骤，因为它们是阶段声明的风险门禁，独立失败信号能缩短诊断路径并防止关键证据被总测试输出淹没。
     - job timeout 约束整条流水线的失控上限，发布脚本内部的进程和命令 timeout 约束具体资源生命周期；二者作用层级不同。
     - 本地全门禁通过只形成“可推送的退出候选”，远程 GitHub Actions 才能补充干净 Ubuntu runner 和仓库工作流证据。CI #23 和 #24 的失败说明这种分层确实发现了 Windows 本地无法证明的类型与进程语义；CI #25 通过后，项目 owner 才确认封存 M4。
   - 易混点：CI 步骤更少不等于验证变少；完整 pytest 已包含专项测试不等于专项门禁没有价值；动态 Python 文件清单不等于所有 package data 自动正确；本地通过不等于远程 CI 已通过。
   - 后续应用：每次里程碑封存都保存远程 run URL、commit SHA 和各门禁结果，并把失败运行作为纠错证据保留；未来调整 CI 时优先消除重复实现，但保留与风险声明一一对应的可见失败信号。
