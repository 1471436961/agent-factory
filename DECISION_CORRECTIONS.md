# DECISION_CORRECTIONS

本日志只记录经过人工 review 后发生实质变化的设计判断。普通措辞调整、文件重命名和尚未采纳的讨论不记录。

## 记录格式

```text
日期：
里程碑：
原判断：
原判断的不足：
人工 review 结论：
最终修正：
证据：
对后续路线的影响：
```

## 编号规则

“已确认纠偏”使用 Markdown 自动有序列表。每条记录在源码中都以 `1.` 开头，渲染器会按当前位置显示为连续编号；新增、删除或移动记录后，后续编号会自动调整。

## 已确认纠偏

1. **元Agent改为确定性工厂控制器**

   - 日期：2026-07-17
   - 里程碑：M0 之前的架构收敛阶段
   - 原判断：由“元Agent”负责定义、生产、评估和晋升其他 Agent，容易被理解为一个依赖 LLM 自主决策的上层 Agent。
   - 原判断的不足：身份边界不清，无法说明硬约束由谁执行，也会让生产治理流程继承 LLM 的非确定性。
   - 人工 review 结论：生产治理核心不应是 Agent，而应是 100% 确定性的代码控制器；LLM evaluator 只能提供辅助信号。
   - 最终修正：核心组件命名为工厂控制器，负责知识槽、工具白名单、DAG、状态迁移和审计等硬约束。Agent 与运行时适配器位于控制边界之外。
   - 证据：`docs/architecture.md` 第 1.3、2.1、2.2 节。
   - 对后续路线的影响：domain 和 application controller 禁止导入模型 SDK；M0 不接入 LLM；M1 先完成确定性的注册、克隆、绑定和导出链路。

1. **代码讲解从逐项展开改为主线优先**

   - 日期：2026-07-18
   - 里程碑：M0 代码理解阶段
   - 原判断：为了完整理解生产级实现，首轮讲解同时展开核心流程、正则语法、标准库 API、全部非法输入示例和异常等细节。
   - 原判断的不足：把主线知识与参考级细节放在同一层，增加了首轮理解的认知负担，也弱化了对模块职责、关键取舍和验证证据的关注。代码逐项解释不等于更有效的工程理解。
   - 人工 review 结论：当前讲解粒度偏细。首次带读应优先讲清工程问题、核心流程、关键设计理由和测试证据；语法细节与边界枚举按实际疑问再深入。
   - 最终修正：后续采用两层讲解。主线层固定覆盖“解决的问题、执行流程、关键代码、设计取舍、测试证明”；深入层只在影响正确性、面试答辩或 owner 主动追问时展开。checksum、事务原子性、幂等和测试隔离属于主线，正则逐字符解析和基础 API 用法降为按需内容。
   - 证据：M0 第 4.1 节讲解后，owner 明确指出整体粒度偏细，并确认后续采用更紧凑的两层结构。
   - 对后续路线的影响：M0 后续代码带读、阶段复盘和新增学习材料均按主线优先组织；AI 在增加细节前先判断该细节是否影响设计理解、正确性验证或项目答辩。

1. **领域快照从字段冻结修正为递归不可变**

   - 日期：2026-07-19
   - 里程碑：M1.1 领域契约
   - 原判断：使用 Pydantic `ConfigDict(frozen=True)` 即可把领域模型视为不可变快照，JSON 字段可继续使用 `dict[str, Any]`。
   - 原判断的不足：`frozen=True` 只阻止字段重新赋值，嵌套 `dict` 和 `list` 仍可原地修改；已计算 checksum 的对象因此可在不生成新 revision 的情况下变化。
   - 人工 review 结论：M1 的可追溯快照必须对 JSON 容器实现递归不可变，不能只依赖团队约定不去修改字典。
   - 最终修正：引入不增加第三方依赖的 `FrozenJsonObject`，输入时递归冻结 mapping/array，序列化时还原标准 JSON 容器。
   - 证据：本地 Pydantic 实验显示 frozen model 的嵌套字典可从 `1` 原地修改为 `2`；`docs/architecture.md` 第 2.3、4.1 节已修正契约。
   - 对后续路线的影响：AgentDefinition、知识内容、工具 Schema 和 AgentSpec 等 JSON 字段统一使用冻结表示；仓储边界只接收序列化后的普通 JSON。

1. **领域异常从自带 HTTP 状态修正为接口层映射**

   - 日期：2026-07-19
   - 里程碑：M1.1 领域契约
   - 原判断：`FactoryError` 子类同时声明稳定错误码和 HTTP status，FastAPI handler 直接读取 `exc.status_code`。
   - 原判断的不足：错误码是应用契约，HTTP status 是 REST 传输语义；把两者放在领域异常中，会使未来 SDK、Tool adapter 和非 HTTP 调用继承不必要的 HTTP 概念。
   - 人工 review 结论：领域/应用异常仅携带 `code`、`message` 和 `details`；REST 层在 M1.4 显式维护错误码到 HTTP status 的映射。
   - 最终修正：从 `FactoryError` 及子类移除 `status_code`，FastAPI handler 通过 `ERROR_STATUS_BY_CODE` 映射，未登记代码安全降级为 500。
   - 证据：`docs/architecture.md` 第 10.5、10.6 节的异常模型与 handler 规格。
   - 对后续路线的影响：M1.4 必须建立完整映射与契约测试；Python SDK 和 Tool adapter 可直接消费稳定业务码，不需要理解 HTTP status。

1. **原型状态变更从仓储内构造修正为应用层快照加 CAS**

   - 日期：2026-07-19
   - 里程碑：M1.2 端口与持久化
   - 原判断：`PrototypeRepository.set_status()` 接收原型 ID、旧状态、新状态和时间，由仓储更新并返回新的原型。
   - 原判断的不足：该签名无法提供废弃原因，也迫使仓储理解 `published_at`、`deprecation_reason` 等领域不变量；仓储会同时承担状态策略和持久化职责。
   - 人工 review 结论：状态转换和完整新快照应由 M1.3 应用服务构造，仓储只负责带 expected status 的原子替换。
   - 最终修正：端口改为 `replace(prototype, expected_status) -> bool`；返回 false 表示 compare-and-swap 未命中，由应用服务重新读取并映射领域冲突。
   - 证据：`docs/architecture.md` 第 6.3 节；`src/agent_factory/application/repositories.py` 与 SQLite 实现。
   - 对后续路线的影响：M1.3 的发布/废弃策略保持可单元测试，Repository 不创建领域对象；未来新增状态元数据时无需扩张仓储方法参数。

1. **幂等持久化从 HTTP 响应修正为传输无关结果**

   - 日期：2026-07-19
   - 里程碑：M1.2 端口与持久化
   - 原判断：`idempotency_records` 保存 `response_status` 和 `response_json`，由 Controller 重放首次 HTTP 响应。
   - 原判断的不足：M1.3 Controller 和 Repository 会提前依赖 HTTP status，与已经确认的领域异常传输中立原则矛盾，也使未来 SDK/Tool adapter 继承 REST 概念。
   - 人工 review 结论：幂等层只保存 application operation、请求哈希和结构化结果；HTTP status 应由 M1.4 接口适配器决定。
   - 最终修正：新增 forward-only `002_persistence_contracts.sql`，将记录调整为 `operation`、`request_hash`、`response_json`、`created_at` 和 `expires_at`；未知旧数据会阻止迁移而非被静默删除。
   - 证据：`docs/architecture.md` 第 6.4、6.5 节；`docs/design/sqlite-persistence.md` 第 6 节；migration 原子回滚集成测试。
   - 对后续路线的影响：M1.3 幂等服务可被 REST、SDK 和 Tool 共享；M1.4 单独决定成功状态码并建立 HTTP 契约测试。

1. **M1 工具依赖从可执行 Registry 修正为元数据 Catalog**

   - 日期：2026-07-19
   - 里程碑：M1.3 应用服务
   - 原判断：`FactoryController` 直接依赖同时保存 `ToolDefinition`、Pydantic 输入/输出模型和 executable handler 的 `ToolRegistry`，生产时从中解析 `AgentSpec`。
   - 原判断的不足：M1 只需要证明原型声明的工具存在且权限不越界；提前引入 handler、超时和执行副作用会把生产治理层与 M3 运行接口耦合，并造成“目录中有工具等于工具已经可执行”的错误表述。
   - 人工 review 结论：M1 只实现 metadata-only `ToolCatalog`，返回不可变 `ResolvedToolSpec`；可执行 Registry 与安全执行器留到 M3。
   - 最终修正：application 定义 `ToolCatalog` Protocol，infrastructure 提供 `InMemoryToolCatalog`；默认只注册无 handler 的 `document-search@1.0.0`，`ToolPolicy` 负责未知工具和权限上限校验。
   - 证据：`src/agent_factory/application/ports.py`、`application/tooling.py`、`infrastructure/tool_catalog.py`；`docs/design/application-services.md` 第 9 节。
   - 对后续路线的影响：M1.4 REST 只暴露规格中的工具元数据；M3 设计 executable Registry 时必须通过独立适配边界消费 `AgentSpec`，不能把 handler 注入 M1 Controller。

1. **M1 Controller 从预注入未来端口修正为只依赖当前能力**

   - 日期：2026-07-19
   - 里程碑：M1.3 应用服务
   - 原判断：`FactoryController.__init__` 在 M1 就接收 `EvaluatorPort`、`ToolRegistry` 等未来依赖，并同时声明评估、晋升和降级方法。
   - 原判断的不足：未使用依赖没有可验证行为，会扩大构造和测试表面，也模糊 M1 与 M2 的退出条件；Evaluator 还可能让人误以为核心生产决策依赖 LLM。
   - 人工 review 结论：Controller 只注入当前里程碑真实调用的端口与纯策略；新能力在进入对应里程碑时再扩展。
   - 最终修正：M1 Controller 只提供原型、知识、克隆、绑定、导出和审计操作；依赖 UoW、系统端口、四类纯策略/Builder、幂等与审计工厂，不接收 Evaluator。
   - 证据：`src/agent_factory/application/controller.py` 构造签名；`docs/architecture.md` 第 3.4 节；`docs/design/application-services.md` 第 3 节。
   - 对后续路线的影响：M2 引入 Evaluator、技能 DAG 和任务结果时必须先更新阶段设计与验收测试，不能把占位端口视为已有实现。

1. **AgentSpec 导出从 GET 修正为 POST action resource**

   - 日期：2026-07-19
   - 里程碑：M1.4 核心 REST 契约
   - 原判断：使用 `GET /instances/{instance_id}/spec?revision=...` 导出或读取 `AgentSpec`，把它视为普通查询。
   - 原判断的不足：目标 revision 首次导出时会持久化规格并追加 `spec.exported` 审计事件，GET 因此具有服务端写副作用，不满足 HTTP safe method 语义；缓存、预取或爬虫也可能意外触发写入。
   - 人工 review 结论：首次生成与后续稳定重放应保持同一 application 语义，但 HTTP 入口必须诚实表达该操作可能创建资源。
   - 最终修正：路由改为 `POST /instances/{instance_id}/spec-exports`，请求体为 `{revision?: PositiveInt}`；同一 revision 的重复请求仍由 Controller 返回已持久化快照且不重复审计。
   - 证据：`src/agent_factory/interfaces/api/routers/instances.py`；`tests/contract/test_rest_api.py` 的重复导出与 OpenAPI method 断言；`docs/design/rest-api.md` 第 5 节。
   - 对后续路线的影响：M3 SDK 和 Tool adapter 必须复用“导出 action”语义，不能把该操作重新包装为无副作用 GET；若未来需要纯读取，可另增只读取已存在快照的资源端点。

1. **M1 固定 API key 认证修正为明确延期认证并暴露不可信 actor 标签**

   - 日期：2026-07-19
   - 里程碑：M1.4 核心 REST 契约
   - 原判断：Alpha 可先实现固定本地 API key，并由认证依赖生成 `Principal.subject`，审计查询同时按角色授权。
   - 原判断的不足：M1 阶段范围已明确排除用户认证；临时 API key 若没有密钥生命周期、主体存储、权限模型和轮换机制，只会制造“已经安全”的假象，也扩大核心闭环验收面。
   - 人工 review 结论：M1 不实现半成品认证，但必须保留 actor 审计字段，并在接口和部署文档中诚实标明其不可信性质。
   - 最终修正：写路由要求 `X-Actor-ID` 作为 1-128 字符的审计标签；它不参与身份验证或授权。审计查询当前不设角色门槛，服务不得直接暴露到不可信网络。
   - 证据：`src/agent_factory/interfaces/api/dependencies.py`；`docs/architecture.md` 第 10.2、11.2 节；`docs/design/rest-api.md` 第 1、3 节。
   - 对后续路线的影响：认证必须作为独立里程碑能力设计；接入后 actor 改为可信 `Principal.subject`，审计读取增加角色授权，并补充 401/403 契约与安全测试。

1. **M2 技能治理从松散状态字段修正为完整证据与来源契约**

   - 日期：2026-07-21
   - 里程碑：M2 规划与设计评审
   - 原判断：Evaluator 只接收 AgentSpec、suite 和 cases；实例仅保存 active skill node；晋升默认新增知识已提前绑定；评估报告可以直接标记 stale 并内嵌人工复核结果。
   - 原判断的不足：没有 case 实际输出就无法执行规则；active node 缺少技能树版本和 checksum 来源；提前绑定新增知识会产生晋升失败后遗留的中间状态；修改报告的 stale 或 review 字段会破坏历史评估事实的不可变性。
   - 人工 review 结论：规则引擎必须评估显式提交的 case results；Prototype、Instance 和 AgentSpec 必须携带 `SkillTreeRef`；晋升命令必须允许携带新知识选择并与新 revision 原子提交；stale 应在使用报告时相对当前快照判定；人工复核应作为独立不可变记录追加。
   - 最终修正：M2 新增 `EvaluationSubmission`、`SkillTreeRef`、独立 `EvaluationReview` 和带 `knowledge_selections` 的晋升命令；报告绑定 instance revision、AgentSpec checksum、SkillTreeRef 与 EvaluationSuiteRef，晋升策略在使用时完成全量一致性检查。M1 历史对象兼容缺失 skill tree，新对象输出 AgentSpec 1.1。
   - 证据：`docs/milestones/m2-skill-governance.md` 第 4 节；`docs/design/skill-governance.md` 第 2-5 节；`docs/architecture.md` 第 7、9、10、14 章。
   - 对后续路线的影响：M2.1 必须先实现上述领域契约和纯算法；M2.2 使用 forward-only `003_skill_governance.sql` 保存来源投影；M2.3-M2.6 的评估、晋升、降级和 REST 验收均以这些不可变引用与原子事务为前提。

1. **Instance 配置完整性从 Prototype 等值修正为 revision 独立 checksum**

   - 日期：2026-07-21
   - 里程碑：M2.4 晋升与配置重建
   - 原判断：M2.4 可以复用现有 Instance snapshot 持久化契约，不需要新增 migration；Repository 可继续用 `sha256(instance.configuration) == instance.prototype.checksum` 检查配置来源。
   - 原判断的不足：该等式只在配置尚未特化时成立。合法晋升会从 Prototype 基线叠加 active skill nodes，产生新的 configuration；Repository 因而把成功写入的晋升快照误判为 `projection-mismatch:configuration_checksum`，并使并发请求无法到达 revision CAS。
   - 人工 review 结论：不能通过跳过 active snapshot 的校验来规避问题；每个 Instance revision 必须保存并校验自己的 configuration checksum，继续保留持久化损坏检测。
   - 最终修正：新增 forward-only `004_instance_configuration_checksum.sql`，为历史快照回填 Prototype checksum，并用触发器约束新值；Repository 写入每个 revision 的实际 configuration checksum，读取时与 payload 重算值比较。Controller 另从 Prototype definition 和完整 active node 集合重建并核对当前配置，分别覆盖存储完整性和业务来源完整性。
   - 证据：首轮 M2.4 SQLite 集成测试在晋升 revision 2 读回时稳定触发 `projection-mismatch:configuration_checksum`；修正后连续晋升、历史升级、checksum 篡改检测和同 revision 并发单胜测试通过。
   - 对后续路线的影响：M2.5 降级快照必须写入自身 configuration checksum；发布制品与 CI 增加 `004` 资源检查；任何后续会改变 Instance configuration 的操作都不能再假设它与 Prototype definition checksum 相等。

1. **观察窗口从跨 revision 聚合修正为快照级证据窗口**

   - 日期：2026-07-21
   - 里程碑：M2.5 观察期与降级
   - 原判断：`TaskOutcomeRepository.list_for_node()` 只按 instance 和 skill node 查询最近结果；`task_outcomes` 的主键只限制 task ID，同一 EvaluationReport 可由不同 task ID 再次提交。
   - 原判断的不足：跨 revision 聚合会把不同 Agent 配置产生的结果混入同一阈值；报告可重复消费则允许调用方通过更换 task ID 重复计入同一失败证据，自动降级不再对应独立观察样本。
   - 人工 review 结论：观察窗口必须绑定当前 instance revision；一次 EvaluationReport 在 TaskOutcome 中只能消费一次。配置发生变化后重新积累窗口是 Alpha 阶段更保守、可解释的选择。
   - 最终修正：`list_for_node()` 增加 `instance_revision` 条件；新增 forward-only `005_task_outcome_integrity.sql`，为 `evaluation_report_id` 建立唯一索引和 revision 级窗口索引。Controller 还校验 Report、AgentSpec、Tree、Suite、最终 review 与 `passed` 的一致性。
   - 证据：Repository 集成测试证明错误 revision 返回空窗口且报告重放被稳定拒绝；Controller 集成测试证明阈值只使用当前 revision，并发跨阈值只产生一个降级 revision。
   - 对后续路线的影响：M2.6 REST 必须把报告重复消费映射为 409，把证据结果矛盾映射为 422；若未来需要跨配置连续观察，应显式引入 activation/evaluation cohort ID，不能恢复无边界聚合。

1. **M3 从直接增加适配器修正为先建立身份与生命周期前置契约**

   - 日期：2026-07-23
   - 里程碑：M3 规划与设计评审
   - 原判断：项目路线图把 M3 概括为 SDK、Tool adapter、运行接口与 Gradio，默认可以直接在 M2 REST 和 Controller 上增加接口包装并进入演示。
   - 原判断的不足：M2 REST 的 `X-Actor-ID` 只是可伪造审计标签，无法为直接调用 Controller 的 Tool adapter 提供可信 `Principal`；固定 Demo 脚本又要求 `CREATED -> RUNNING -> WAITING`，但 M2 没有生命周期 Command、事务或 REST 路由。若直接做适配器，只能在不同入口复制身份和状态逻辑，或在 UI 内伪造状态。
   - 人工 review 结论：M3 必须先建立最小可信身份边界和显式生命周期能力，再实现 SDK、Factory Tool adapter、Runtime 与 Gradio；三种入口都只能转换输入并复用同一 Controller 规则。
   - 最终修正：M3.1 增加 `Principal`、认证端口、Alpha 静态 Bearer Token 与最小角色授权；M3.2 增加 `LifecyclePolicy`、transition Command、revision CAS、审计、幂等和 REST action；M3.3-M3.7 再依次实现 SDK、Factory Tool adapter、受限 Runtime、Gradio 与同构退出测试。静态 Token 只作为本地 Alpha 信任边界，完整认证与公网安全仍由 M4 验收。
   - 证据：`src/agent_factory/interfaces/api/dependencies.py` 当前从 `X-Actor-ID` 直接返回 actor；Controller 不存在 `transition_instance()`；`docs/architecture.md` 第 9.1-9.2 节已定义未实现的生命周期规格，第 10.9、11.2、14.6 节分别要求可信 Principal、审计授权和演示状态迁移。
   - 对后续路线的影响：M3 不从 UI 或 adapter 绕过身份、状态、幂等和审计边界；M3.1/M3.2 需先独立通过回归门禁。M4 负责安全加固而不是首次发现 actor 不可信；Engineer 可执行代码 Demo 在沙箱与工具权限单独验收前不进入 M3。

1. **M4 从“完整认证与公网安全”修正为 Alpha 安全、回归与发布门禁**

   - 日期：2026-07-24
   - 里程碑：M4 规划与 M4.1 设计冻结
   - 原判断：M3 规划纠偏曾表述“静态 Token 只作为本地 Alpha 信任边界，完整认证与公网安全仍由 M4 验收”，路线图也将 M4 概括为“完整安全与回归门禁”。
   - 原判断的不足：当前单一静态 Bearer Token、单进程 SQLite、loopback Demo 和固定只读工具只能形成受限本地 Alpha 边界。公网生产安全还需要身份提供方、凭据生命周期、多用户/租户隔离、TLS 与代理信任、共享限流和告警、容量与高可用设计；这些不是增加回归测试即可完成的能力。
   - 人工 review 结论：项目 owner 在审阅 M4 的范围、工作包、风险、备选方案和退出标准后，确认 M4 应以生产级工程要求验证当前 Alpha，而不宣称系统已具备公网生产部署能力。
   - 最终修正：M4 拆分为基线冻结、公共契约与语义快照、API/Runtime 安全回归、事务并发故障注入、隔离发布制品检查和 CI 退出六个工作包。完整身份系统与公网部署能力进入后续单独的 Productionization 里程碑。
   - 证据：[`docs/milestones/m4-quality-security.md`](docs/milestones/m4-quality-security.md)、[`docs/design/security-regression-gates.md`](docs/design/security-regression-gates.md)、[`docs/project/PROJECT_ROADMAP.md`](docs/project/PROJECT_ROADMAP.md)。
   - 对后续路线的影响：M4 只能对当前存在的接口、只读工具和本地部署拓扑给出自动化证据；未来新增文件、网络、shell、外部写工具或公网入口时，必须先增加与其攻击面匹配的独立设计和阻断门禁。

1. **M5 从单一混合实验修正为三类证据协议**

   - 日期：2026-07-25
   - 里程碑：M5 规划与 M5.1 实验协议设计
   - 原判断：使用同一组 240 次 MANUAL/FACTORY Writer 生成，同时验证结构一致性、知识遗漏、构建成本、适应性和审计完整性，并把五项结果放入同一假设表。
   - 原判断的不足：模型生成不能直接产生开发者构建时间证据，审计完整性又是可以由固定业务链确定性检查的工程属性；把二者混入生成实验会制造不成立的样本量。MANUAL 与 FACTORY 还同时改变生产流程、Prompt 组织和结构约束，因此该比较不能识别某个单一组件的因果效应。将场景改为 Engineer 又会引入代码执行、依赖、文件权限和沙箱等当前 Alpha 不具备的混杂因素。
   - 人工 review 结论：项目 owner 确认保留可控的 Writer 场景和 24×2×5 生成规模，但必须拆分证据来源、限制因果表述，并在真实模型调用前设置 pilot、冻结和预算人工审批门。
   - 最终修正：H1/H2/H4 由主要 Writer 生成实验检验；H3 降级为单操作者探索性构建案例；H5 改为确定性审计链验证。正式知识使用虚构合成材料，两组实际可见知识正文必须字节级一致；pilot 不进入正式数据；没有第二名独立评分者时不计算 Cohen's kappa。正式调用只在模型、版本、价格和成本上限冻结并经 owner 明确批准后执行。
   - 证据：[`docs/milestones/m5-validation-experiment.md`](docs/milestones/m5-validation-experiment.md)、[`docs/design/experiment-protocol.md`](docs/design/experiment-protocol.md)、[`docs/architecture.md`](docs/architecture.md) 第十三章。
   - 对后续路线的影响：M5 拆分为协议、模型与任务、执行器、分析、pilot 冻结、正式执行和复算报告七个工作包；CI 永远使用 fake gateway，M5.6 不会因前置代码完成而自动触发真实模型调用。实验结论只能解释冻结模型和 Writer workflow bundle，消融或跨模型验证必须使用新的 experiment ID 独立开展。

1. **M5.5 Pilot 冻结从实验源码扩展为完整生产依赖闭环**

   - 日期：2026-07-26
   - 里程碑：M5.5 收尾评审
   - 原判断：冻结 Pilot fixture、全部 `experiments/*.py`、`pyproject.toml` 和 `uv.lock` 即可证明受控 launcher 的执行输入没有漂移。
   - 原判断的不足：`experiments/pilot_launcher.py` 会调用真实 `FactoryController`、领域服务、SQLite Repository 和 migration 来生成 FACTORY 条件的 `AgentSpec`，但 M5.5.7 的 62 项 inventory 没有包含任何 `src/agent_factory` 文件。生产代码即使变化，旧 Manifest 仍可能通过 content-only 校验，无法证明 AgentSpec 生成逻辑与冻结时一致。
   - 人工 review 结论：真实付费 Pilot 前必须闭合生产依赖证据；不能只冻结直接 import 的文件，也不能通过放宽环境 verifier 绕过缺口。
   - 最终修正：Pilot candidate 显式纳入 `src/agent_factory` 下全部 85 个 Python 文件和 6 个 migration SQL，测试从文件系统重新枚举这 91 项输入并要求全部出现在 inventory。M5.5.7 Manifest 保留为历史证据，修正源码进入 clean commit 后重新生成 canonical Manifest。
   - 证据：`experiments/pilot_launcher.py` 的 `agent_factory` imports；`tests/unit/experiments/test_pilot.py` 的 production inventory 断言；新旧 Manifest 的 source commit、inventory 和 checksum 回归测试。
   - 对后续路线的影响：任何生产 package 或 migration 新增都会使候选完整性测试失败；真实 Pilot 只能使用包含完整生产依赖的新 Manifest。正式 M5.6 Manifest 沿用同一闭环，不把旧 M5.5.7 文件作为费用授权依据。

1. **M5.5 收尾操作从新增子里程碑修正为原阶段退出任务**

   - 日期：2026-07-26
   - 里程碑：M5.5 收尾评审
   - 原判断：把生产源码补冻、重新归档、真实 Pilot、Pilot review 和正式冻结继续编号为 M5.5.8-M5.5.11。
   - 原判断的不足：原始 M5.5 规划只有 M5.5.1-M5.5.6；把缺陷修复、付费审批点和退出动作都升级为新子里程碑，会造成阶段范围不断生长，也掩盖 M5.5 尚未满足原退出条件这一事实。
   - 人工 review 结论：不新增 M5.5.8-M5.5.11。剩余工作统一称为“M5.5 收尾”，只包含冻结缺口修复、8-run Pilot 及评审、正式配置冻结与 owner 审批。
   - 最终修正：保留已有 Git 历史和证据章节，不再扩张工作包编号；项目状态按 M5.5 原始退出条件判断，真实 240-run 仍属于 M5.6。
   - 证据：`docs/milestones/m5-validation-experiment.md` 第 5、8 节；项目 owner 对新增编号的人工 review 结论。
   - 对后续路线的影响：实现步骤和审批检查点不再自动获得里程碑编号；M5.5 只有在 Pilot 完成、正式 Manifest 冻结且 owner 明确批准后才能勾选完成。

1. **M5.5 Pilot 从 OpenAI 专用费用契约修正为 Moonshot 与货币中立契约**

   - 日期：2026-07-26
   - 里程碑：M5.5 真实 Pilot 执行前评审
   - 原判断：使用 OpenAI `gpt-4.1-mini-2025-04-14`、Responses API 和美元预算完成 8-run Pilot；项目 owner 已按 `$0.051815` 上限批准真实调用。
   - 原判断的不足：ChatGPT Plus 与 OpenAI API 是独立产品，现有地区和付款条件不能形成可执行的 OpenAI API 账户路径；原实现还把金额字段编码为 USD 专用名称，无法在不歪曲单位的情况下切换供应商。另一方面，Kimi 当前只提供 `kimi-k2.6` 别名，没有带日期的不可变 snapshot，不能沿用“固定模型快照”的可复现性声明。
   - 人工 review 结论：Pilot 改用中国区 Moonshot API 和 `kimi-k2.6` 非思考模式；旧 OpenAI 批准立即失效。模型调用 profile、人民币价格和新硬上限必须重新冻结，并在真实调用前再次获得精确批准。
   - 最终修正：冻结 schema 升级为 `1.1`，价格和预算显式携带 `currency`，规范字段改为货币中立的 integer micros；新增 OpenAI-compatible `MoonshotExperimentGateway`，固定 Chat Completions 流式调用、`temperature=0.6`、`top_p=0.95`、`n=1`、MANUAL JSON mode、FACTORY strict JSON Schema 与本地共同校验。候选记录 `model_is_immutable_snapshot=false`，预算按未缓存输入计算为预期 `¥0.429184`、硬上限 `¥0.858368`。OpenAI canonical Manifest 原样转为切换前历史证据。
   - 证据：[Kimi API 概览](https://platform.kimi.com/docs/api/overview)、[Kimi K2.6 使用说明](https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart)、[Chat Completions 结构化输出说明](https://platform.kimi.com/docs/api/chat)、[Kimi 开放平台价格](https://platform.kimi.com/)；`experiments/moonshot_gateway.py`、`experiments/definitions/writer-pilot-v1/freeze-candidate.json`、对应 fake-stream 单元测试，以及绑定 source commit `889807a15b3d1cff9fe5df51f077de2110f6464a` 的 canonical `freeze-manifest.json`。
   - 对后续路线的影响：所有实验费用确认都必须同时匹配 currency 与 integer micros；provider 切换必须生成新 schema-compatible Manifest，不能复用旧费用批准。M5.5 在新的 Moonshot clean-commit Manifest 生成和 owner 二次批准前仍未达到真实执行条件；正式报告必须披露 provider alias 可能漂移的外部复现限制。
