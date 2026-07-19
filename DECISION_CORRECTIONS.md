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
