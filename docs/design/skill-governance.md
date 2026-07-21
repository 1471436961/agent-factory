# M2 技能治理设计说明

## 1. 解决的问题与边界

M2 在 M1 的不可变实例快照、AgentSpec、UoW、审计和幂等基础上增加技能治理。输入是已注册的技能树、评估套件和外部提交的 case result；输出是绑定到确切实例 revision 的评估报告，以及通过显式命令产生的晋升或确定性降级快照。

本模块不执行 Agent，不调用真实模型，不提供可信身份，也不把规则分数解释为语义质量。默认测试完全离线。

依赖方向保持：

```text
REST DTO / application commands
              |
              v
       FactoryController
       |      |       |
       |      |       +--> Repository / UoW ports
       |      +----------> deterministic EvaluationEngine
       +-----------------> Skill/Promotion/Degradation policies
                                 |
                                 v
                           immutable domain models

infrastructure/sqlite 实现端口；domain 不导入 application、SQLite、FastAPI 或模型 SDK。
```

## 2. M2 前置契约修正

### 2.1 评估必须接收实际输出

原 `EvaluatorPort.evaluate(spec, suite, cases)` 没有每个 case 的执行结果，无法计算规则。M2.1 将纯计算端口改为接收 `EvaluationSubmission`，只返回派生结果；M2.3 application service 再补充报告 ID、Spec 来源和时间：

```python
class EvaluationEngine(Protocol):
    def evaluate(
        self,
        *,
        suite: EvaluationSuite,
        submission: EvaluationSubmission,
    ) -> EvaluationOutcome: ...
```

默认实现 `DeterministicRuleEngine` 是纯同步计算，不生成 UUID、不读取系统时间也不访问仓储。未来 Runtime Adapter 负责产生 submission；未来 LLM adapter 只能增加 `JudgeSignal`，不能替代 HARD 规则或单独触发晋升。

### 2.2 技能树引用进入来源链

```python
class SkillTreeRef(FrozenModel):
    tree_id: Slug
    version: SemVer
    checksum: Sha256


class EvaluationSuiteRef(FrozenModel):
    suite_id: Slug
    version: SemVer
    checksum: Sha256
```

M2 对现有模型增加默认值为 `None` 的 `skill_tree`：

```python
class AgentPrototype(FrozenModel):
    # M1 fields unchanged
    skill_tree: SkillTreeRef | None = None


class AgentInstance(FrozenModel):
    # M1 fields unchanged
    skill_tree: SkillTreeRef | None = None


class AgentSpec(FrozenModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    # M1 fields unchanged
    skill_tree: SkillTreeRef | None = None
```

历史 M1 Spec 保持 `schema_version="1.0"` 并允许缺失 `skill_tree`；绑定技能树的新 Spec 使用 `1.1`。1.0 checksum 计算排除新增的空 `skill_tree` 字段，1.1 checksum 则包含完整 SkillTreeRef；该版本规则集中在 `checksum_agent_spec()`，Builder 和 Repository 损坏检测复用同一实现。原型 `checksum` 继续表示 definition checksum，技能树使用独立 ref checksum，避免静默改变 M1 checksum 语义。

绑定技能树必须发生在原型注册时。已发布原型不可原地修改；需要技能树的调用方注册新原型版本。克隆时将 ref 复制到 instance，导出时再复制到 AgentSpec。

### 2.3 stale 不写回报告

报告是关于历史 revision 的事实。实例变化后报告仍可查询，不增加 mutable `stale` 字段。`PromotionPolicy` 在使用报告时比较 instance ID、revision、AgentSpec checksum、suite ref 和 tree ref，任一不匹配都返回稳定错误。

### 2.4 人工复核独立追加

`EvaluationReview` 与 `EvaluationReport` 分表保存。Alpha 每份报告最多接受一条最终复核；重复提交返回冲突。需要更正时必须重新执行评估生成新报告，不能覆盖旧复核。

## 3. 领域模型

### 3.1 技能树

```python
class ObservationPolicy(FrozenModel):
    window_size: int = Field(default=10, ge=3, le=100)
    minimum_samples: int = Field(default=5, ge=1, le=100)
    consecutive_failures: int = Field(default=3, ge=1, le=20)
    failure_rate_threshold: float = Field(default=0.5, gt=0, le=1)


class SkillNode(FrozenModel):
    node_id: Slug
    display_name: str = Field(min_length=1, max_length=128)
    parents: frozenset[Slug] = frozenset()
    prompt_appendix: str = Field(default="", max_length=8_000)
    granted_tools: frozenset[Slug] = frozenset()
    added_knowledge_slots: tuple[KnowledgeSlot, ...] = ()
    output_schema_override: JsonObject | None = None
    evaluation_suite: EvaluationSuiteRef
    observation_policy: ObservationPolicy = Field(default_factory=ObservationPolicy)


class SkillTreeDraft(FrozenModel):
    tree_id: Slug
    version: SemVer
    nodes: Annotated[tuple[SkillNode, ...], Field(min_length=1)]


class SkillTree(SkillTreeDraft):
    checksum: Sha256
    created_at: AwareDatetime
    created_by: Actor
```

领域对象使用 tuple 而不是可变 dict 保存节点。注册时按 `node_id` 排序，并校验：节点 ID 唯一、父节点存在、禁止自依赖、图无环、节点引用的评估套件存在且 checksum 一致。

### 3.2 评估套件与提交证据

```python
class RuleKind(StrEnum):
    JSON_SCHEMA = "json-schema"
    REQUIRED_TERMS = "required-terms"
    FORBIDDEN_TERMS = "forbidden-terms"
    REGEX = "regex"
    MAX_LENGTH = "max-length"
    TOOL_CALLED = "tool-called"


class EvaluationRule(FrozenModel):
    rule_id: Slug
    kind: RuleKind
    hard: bool = True
    parameters: JsonObject
    weight: float = Field(default=1.0, gt=0, le=100)


class EvaluationCase(FrozenModel):
    case_id: Slug
    input: str = Field(min_length=1, max_length=64_000)
    metadata: JsonObject = Field(default_factory=FrozenJsonObject)


class EvaluationSuiteDraft(FrozenModel):
    suite_id: Slug
    version: SemVer
    rules: Annotated[tuple[EvaluationRule, ...], Field(min_length=1)]
    cases: Annotated[tuple[EvaluationCase, ...], Field(min_length=1)]
    minimum_soft_score: float = Field(default=0.8, ge=0, le=1)
    require_manual_review: bool = False


class EvaluationSuite(EvaluationSuiteDraft):
    checksum: Sha256
    created_at: AwareDatetime
    created_by: Actor


class SubmittedCaseResult(FrozenModel):
    case_id: Slug
    output_text: str = Field(max_length=64_000)
    structured_output: JsonObject | None = None
    called_tools: tuple[Slug, ...] = ()
    artifact_uri: AnyHttpUrl | None = None


class EvaluationSubmission(FrozenModel):
    instance_id: UUID
    instance_revision: PositiveInt
    suite: EvaluationSuiteRef
    runtime_model: str = Field(min_length=1, max_length=128)
    case_results: Annotated[tuple[SubmittedCaseResult, ...], Field(min_length=1)]
```

suite 注册时按 RuleKind 校验参数 Schema：

| RuleKind | 必需参数 | 检查对象 |
| --- | --- | --- |
| `json-schema` | `schema` | `structured_output` |
| `required-terms` | `terms`，可选 `case_sensitive` | `output_text` |
| `forbidden-terms` | `terms`，可选 `case_sensitive` | `output_text` |
| `regex` | `pattern` | `output_text` |
| `max-length` | `max_chars` | `output_text` |
| `tool-called` | `tool_name` | `called_tools` |

未知参数、空或重复 terms、非法 regex、非法 JSON Schema、非正整数 `max_chars` 在模型边界失败，不推迟到评估执行。terms 默认大小写不敏感，只有 `case_sensitive=true` 时执行精确大小写匹配。regex pattern 最长 512 字符，执行使用 `regex` 库的 50ms timeout；超时返回 `EVALUATION_RULE_TIMEOUT`，不生成部分 outcome。

submission 必须为 suite 的每个 case 提供且只提供一条 result。`called_tools` 不允许重复。`DeterministicRuleEngine` 根据 canonical case result 计算 checksum；调用方不能直接提交 checksum。

### 3.3 报告与复核

```python
class CaseResultRef(FrozenModel):
    case_id: Slug
    checksum: Sha256
    artifact_uri: AnyHttpUrl | None = None


class RuleResult(FrozenModel):
    rule_id: Slug
    case_id: Slug
    passed: bool
    score: float = Field(ge=0, le=1)
    evidence: JsonObject = Field(default_factory=FrozenJsonObject)


class EvaluationDecision(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    REVIEW_REQUIRED = "review-required"


class EvaluationOutcome(FrozenModel):
    case_results: Annotated[tuple[CaseResultRef, ...], Field(min_length=1)]
    rule_results: Annotated[tuple[RuleResult, ...], Field(min_length=1)]
    hard_rules_passed: bool
    soft_score: float = Field(ge=0, le=1)
    decision: EvaluationDecision


class EvaluationReport(EvaluationOutcome):
    report_id: UUID
    instance_id: UUID
    instance_revision: PositiveInt
    agent_spec_checksum: Sha256
    skill_tree: SkillTreeRef
    suite: EvaluationSuiteRef
    runtime_model: str = Field(min_length=1, max_length=128)
    judge_signals: tuple[JudgeSignal, ...] = ()
    started_at: AwareDatetime
    completed_at: AwareDatetime


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class EvaluationReview(FrozenModel):
    review_id: UUID
    report_id: UUID
    reviewer: Actor
    decision: ReviewDecision
    comment: str = Field(default="", max_length=2_000)
    reviewed_at: AwareDatetime
```

`EvaluationOutcome` 是 M2.1 纯规则引擎的返回值；`EvaluationReport` 在 M2.3 由 application service 将 outcome 与不可伪造的服务端来源字段组合。报告不保存完整 output_text；只保存 result checksum、可选 artifact URI 和最多 4096 bytes、且不含原文的 rule evidence。这样降低数据库和审计泄露风险。没有 artifact URI 时，报告可审计规则结论但不能独立重放原始输出，这是 M2 的明确边界。

决策顺序固定：任一 HARD 失败为 FAIL；HARD 全过但加权 SOFT 分数低于阈值为 FAIL；要求人工复核时为 REVIEW_REQUIRED；其他情况为 PASS。JudgeSignal 不参与上述计算。

## 4. 纯算法

### 4.1 DAG 与拓扑顺序

```python
def topological_order(
    tree: SkillTree,
    active_node_ids: frozenset[Slug],
) -> tuple[SkillNode, ...]: ...


def descendants_of(tree: SkillTree, node_id: Slug) -> frozenset[Slug]: ...
```

拓扑选择每轮按 `node_id` 升序处理 ready nodes，保证同一个 active set 与输入 tuple 顺序无关。unknown node、父节点不在 active set 或运行时发现环均抛稳定领域错误。

### 4.2 全量配置重建

```python
def apply_skill_nodes(
    *,
    base: AgentDefinition,
    tree: SkillTree,
    active_node_ids: frozenset[Slug],
) -> AgentDefinition: ...
```

组合规则：

- system prompt 为 base 加按拓扑顺序排列的 `[skill:{node_id}]` appendix。
- tools 为 base 与所有 granted tools 的并集，最终按名称排序。
- knowledge slots 按名称合并；同名但定义不同立即返回 `SKILL_CONFIGURATION_CONFLICT`。
- output schema override 最多一个 active node 可以声明；多个 override 不做猜测或 deep merge。
- M2.1 纯函数重建后重新运行 AgentDefinition 与 output schema 结构校验；M2.4 application service 再运行需要外部目录或知识包的 ToolPolicy 和 KnowledgeBindingPolicy。

降级也调用同一函数，不实现反向删除 Prompt 或工具的 patch 算法。

## 5. 应用服务

### 5.1 评估

```python
class EvaluateInstanceCommand(FrozenModel):
    submission: EvaluationSubmission
    actor: Actor
    idempotency_key: IdempotencyKey | None = None


async def evaluate_instance(
    self,
    command: EvaluateInstanceCommand,
) -> EvaluationReport: ...
```

执行顺序：验证 expected revision 对应快照存在；解析或首次持久化该 revision 的 AgentSpec；加载 suite 和 instance skill tree；在事务外运行纯规则引擎；进入写 UoW 保存 report、审计和幂等响应。评估期间 head 变化不删除报告，报告仍属于旧 revision。

### 5.2 复核

```python
class ReviewEvaluationCommand(FrozenModel):
    report_id: UUID
    decision: ReviewDecision
    comment: str = Field(default="", max_length=2_000)
    actor: Actor
    idempotency_key: IdempotencyKey | None = None
```

报告不存在或 decision 不是 REVIEW_REQUIRED 时拒绝；同一 report 只接受一个最终 review。review、审计和幂等响应同事务提交。

### 5.3 晋升

```python
class PromoteAgentCommand(FrozenModel):
    instance_id: UUID
    expected_revision: PositiveInt
    target_node_id: Slug
    evaluation_report_id: UUID
    evaluation_review_id: UUID | None = None
    knowledge_selections: tuple[KnowledgeSelection, ...] = ()
    actor: Actor
    idempotency_key: IdempotencyKey | None = None
```

Controller 必须验证：实例可修改且 revision 匹配；skill tree ref 存在且 checksum 正确；目标未激活且父节点全部激活；报告绑定当前 instance/revision/spec/tree/suite；报告 PASS，或 REVIEW_REQUIRED 且存在匹配的 APPROVED review；目标 suite 与报告一致。

随后从来源 Prototype definition 和 `active_nodes | {target}` 构建候选配置，解析工具，合并当前绑定与 command selections，并对候选配置执行完整知识策略。成功时创建 revision + 1；失败时不得留下新绑定、审计或幂等记录。自动晋升禁止。

M2.4 实现将判定拆为纯 `PromotionPolicy` 与 Controller 编排。Policy 不读取仓储，只接收已加载的 Instance、AgentSpec、SkillTree、EvaluationReport 和可选 EvaluationReview；Controller 负责 expected revision、精确来源引用、Prototype 基线重建、工具目录和知识包校验。晋升不会改变生命周期 status，也不会提前生成新 revision 的 AgentSpec；后者仍由 `export_spec()` 首次导出。

知识合并使用追加语义：当前 binding 必须继续满足候选配置，且保留原始 `bound_at` 和 `bound_by`；命令只为新引用生成 binding。重复引用返回 `KNOWLEDGE_ALREADY_BOUND`，同槽位超出基数或缺失新增必填槽由完整 `KnowledgeBindingPolicy` 拒绝。

### 5.4 观察与降级

```python
class RecordTaskOutcomeCommand(FrozenModel):
    instance_id: UUID
    expected_revision: PositiveInt
    task_id: UUID
    skill_node_id: Slug
    passed: bool
    evaluation_report_id: UUID
    actor: Actor
    idempotency_key: IdempotencyKey | None = None


class DegradationCheckResult(FrozenModel):
    instance_id: UUID
    checked_revision: PositiveInt
    degraded: bool
    resulting_revision: PositiveInt
    removed_nodes: frozenset[Slug] = frozenset()
    removed_binding_slots: frozenset[Slug] = frozenset()
```

outcome 必须引用同一实例和技能节点的有效报告。Controller 写入 outcome 后读取固定窗口。样本不足时不降级；连续失败达到阈值或窗口失败率达到阈值时，移除该节点和全部后代。

未降级时只提交 outcome/audit/idempotency，实例 revision 不变。降级时从原型重建配置，保留仍被候选配置声明且继续满足槽约束的 bindings，写入 DEGRADED revision + 1。两条路径都在一个 UoW 中完成。

## 6. 持久化契约

M2 新增 `003_skill_governance.sql`。核心表：

```sql
CREATE TABLE skill_trees (... PRIMARY KEY (tree_id, version));
CREATE TABLE evaluation_suites (... PRIMARY KEY (suite_id, version));
CREATE TABLE evaluation_reports (... report_id TEXT PRIMARY KEY ...);
CREATE TABLE evaluation_reviews (... report_id TEXT UNIQUE ...);
CREATE TABLE task_outcomes (... PRIMARY KEY (task_id, instance_id, skill_node_id));
CREATE TABLE prototype_skill_trees (... PRIMARY KEY (prototype_id, prototype_version));
CREATE TABLE instance_skill_trees (... PRIMARY KEY (instance_id, revision));
```

每张治理快照表保存 canonical `payload_json`、checksum 和必要投影。`prototype_skill_trees` 与 `instance_skill_trees` 作为可查询来源投影，避免重建 M1 主表；Repository 读取时核对投影与 payload。技能树必须先注册，之后原型才可引用。

新增端口：

```python
class SkillTreeRepository(Protocol):
    async def add(self, tree: SkillTree) -> None: ...
    async def get(self, tree_id: str, version: str) -> SkillTree | None: ...


class EvaluationSuiteRepository(Protocol): ...
class EvaluationReportRepository(Protocol): ...
class EvaluationReviewRepository(Protocol): ...
class TaskOutcomeRepository(Protocol): ...
```

现有 UoW 增加相应属性。所有驱动异常继续转换为安全的 `RepositoryUnavailableError`；已知唯一键、revision 和状态冲突转换为稳定业务错误。

M2.4 另新增 forward-only `004_instance_configuration_checksum.sql`。旧 Repository 把 `sha256(instance.configuration)` 与 Prototype definition checksum 直接比较，只适用于未特化配置。`004` 为历史快照回填来源 Prototype checksum，并要求新快照持久化各自的 configuration checksum；Repository 读取时对 payload 重算。Controller 还会从 Prototype definition 与完整 active node 集合重建当前配置并比较，两层校验分别回答“数据是否损坏”和“配置是否来自声明来源”。

## 7. REST 契约

M2 最小路由：

| Method | Path | 作用 |
| --- | --- | --- |
| POST | `/api/v1/evaluation-suites` | 注册不可变套件版本 |
| GET | `/api/v1/evaluation-suites/{id}/versions/{version}` | 查询套件 |
| POST | `/api/v1/skill-trees` | 注册不可变技能树版本 |
| GET | `/api/v1/skill-trees/{id}/versions/{version}` | 查询技能树 |
| POST | `/api/v1/instances/{id}/evaluations` | 提交 case results 并生成报告 |
| POST | `/api/v1/evaluation-reports/{id}/reviews` | 提交一次最终人工复核 |
| POST | `/api/v1/instances/{id}/promotions` | 显式晋升 |
| POST | `/api/v1/instances/{id}/task-outcomes` | 记录观察结果并检查降级 |

所有写路由要求 `X-Actor-ID` 并支持 `Idempotency-Key`。该 actor 仍是不可信标签。Router 只转换 DTO；规则和事务留在 Controller。

新增错误码至少包括：`SKILL_TREE_NOT_FOUND`、`SKILL_TREE_ALREADY_EXISTS`、`SKILL_NODE_NOT_FOUND`、`SKILL_DEPENDENCY_MISSING`、`SKILL_ALREADY_ACTIVE`、`SKILL_CONFIGURATION_CONFLICT`、`EVALUATION_SUITE_NOT_FOUND`、`EVALUATION_SUITE_ALREADY_EXISTS`、`EVALUATION_REPORT_NOT_FOUND`、`EVALUATION_REPORT_ALREADY_EXISTS`、`EVALUATION_SUITE_MISMATCH`、`EVALUATION_REVIEW_CONFLICT`、`TASK_OUTCOME_ALREADY_EXISTS`、`STALE_EVALUATION_REPORT` 和 `PROMOTION_REJECTED`。每个错误必须进入 REST 显式映射集合测试。

## 8. 并发、审计与安全边界

- 评估报告按 report ID 不可变；幂等重放返回同一报告。
- 晋升与降级继续使用 instance snapshot/head CAS，不增加进程内锁作为正确性条件。
- 同一 report 的 review 使用唯一约束；并发提交只有一个成功。
- 审计事件固定为 allowlist：`skill-tree.registered`、`evaluation-suite.registered`、`evaluation.completed`、`evaluation.reviewed`、`skill.promoted`、`task-outcome.recorded`、`skill.degraded`。
- 审计不保存 case input、output_text、完整 structured output、rule evidence 或 review comment 等自由文本。
- evaluation evidence 来自调用方；M2 只能证明规则对该 evidence 的处理可重复，不能证明 evidence 由可信运行时产生。

## 9. 验证方法

- domain unit tests：模型边界、DAG、稳定顺序、RuleKind 参数、规则执行、配置冲突、后代移除和降级阈值。
- compatibility tests：读取 M1 固定 JSON fixture，断言新增可选字段不破坏 Prototype、Instance 和 Spec。
- SQLite integration tests：003 migration、五类治理仓储、来源投影、损坏检测、事务回滚和并发 CAS。
- Controller integration tests：评估、review、晋升知识原子性、stale report、并发晋升、无降级与触发降级。
- REST contract tests：DTO、错误 envelope、actor/idempotency、完整治理链和未知异常脱敏。
- exit test：关闭并重建 app 后恢复技能树、套件、报告、review、实例 revision、TaskOutcome 和审计。
- CI：保留 domain 90%、application 85%、total 80% branch coverage 门槛，构建 wheel 并检查 `003_skill_governance.sql`、`004_instance_configuration_checksum.sql` 与全部 M2 模块。

## 10. M2.1 实现映射

| 契约 | 代码位置 | 直接证据 |
| --- | --- | --- |
| 版本化治理引用 | `domain/references.py` | `test_m2_compatibility.py` |
| AgentSpec 1.0/1.1 与 checksum | `domain/models.py`、`domain/services/spec.py` | M1 golden checksum 与 1.1 来源测试 |
| 技能树与观察模型 | `domain/skills.py` | 非法图、多分支 DAG、阈值测试 |
| 稳定拓扑、后代和全量重建 | `domain/services/skills.py` | 顺序、依赖、冲突和重建测试 |
| 评估契约 | `domain/evaluation.py` | 参数、唯一性、报告不变量测试 |
| 确定性规则引擎 | `domain/services/evaluation.py` | 六类规则、决策顺序和 timeout 测试 |

M2.1 只完成纯领域能力。M2.2 通过数据库外键保证持久化的 `SkillTreeRef` 指向已注册对象；报告的服务端来源构造现已由 M2.3 完成，晋升事务和 REST 暴露仍分别属于 M2.4 与 M2.6，不能由领域层或持久层测试推导为已完成。

## 11. M2.2 实现映射

| 契约 | 代码位置 | 直接证据 |
| --- | --- | --- |
| forward-only v3 schema | `infrastructure/sqlite/sql/003_skill_governance.sql` | migration 重入、checksum 与 v2 升级测试 |
| 五类治理 Repository | `application/repositories.py`、`infrastructure/sqlite/governance_repositories.py` | round trip、唯一键、只读与回滚测试 |
| Tree/Suite/Spec 确定性 checksum | `domain/services/skills.py`、`domain/services/evaluation.py`、`domain/services/spec.py` | `test_m2_checksums.py` 与 M1 golden checksum |
| Prototype/Instance 技能树来源投影 | `infrastructure/sqlite/repositories.py` | 来源往返与投影损坏测试 |
| 报告与观察来源外键 | `003_skill_governance.sql` | 错误 Spec checksum 与跨实例 outcome 拒绝测试 |
| UoW 治理端口 | `application/unit_of_work.py`、`infrastructure/sqlite/unit_of_work.py` | commit、rollback、read-only 与重启恢复测试 |

M2.2 只建立治理数据的可靠存储边界。Repository 不创建服务端报告来源、不决定晋升、不写治理审计或幂等记录；这些跨仓储业务事务从 M2.3 开始由 Controller 组织。

## 12. M2.3 实现映射

| 契约 | 代码位置 | 直接证据 |
| --- | --- | --- |
| Suite/Tree 注册、查询与精确引用校验 | `application/controller.py` | 缺失 Suite、错误 tree checksum、注册幂等测试 |
| 技能树来源传播 | `application/commands.py`、`application/controller.py` | Prototype → Instance → AgentSpec 集成断言 |
| 纯评估端口与默认装配 | `application/ports.py`、`container.py` | 真实 Container 调用 `DeterministicRuleEngine` 的 PASS/FAIL 测试 |
| 服务端报告来源构造 | `application/controller.py` | report 与 instance revision、Spec checksum、Tree/Suite ref 的持久化断言 |
| 最终人工复核 | `application/controller.py` | REVIEW_REQUIRED、重复复核冲突、PASS/FAIL 拒绝测试 |
| allowlist 审计 | `application/audit.py` | case output、suite input、skill prompt、rule evidence、review comment 脱敏测试 |

评估采用“准备、纯计算、提交”三段式：

```text
只读 UoW
  加载指定 Instance revision、Tree、Suite 与已有 AgentSpec
  缺少 Spec 时只在内存中构造候选快照
        |
        v
事务外 DeterministicRuleEngine.evaluate()
        |
        v
最终写 UoW
  再次检查幂等记录与历史 revision
  保存缺失 AgentSpec + EvaluationReport + AuditEvent + IdempotencyRecord
  单次 commit
```

这一区分避免规则计算占用 SQLite 的 `BEGIN IMMEDIATE` 写锁。带幂等键的请求在计算前执行快速重放，并在最终写 UoW 中再次重放；前者减少重复计算，后者负责并发正确性。报告 ID 只在最终写 UoW 内生成，失败事务不会留下一个被误认为已持久化的报告标识。

M2.3 允许显式评估历史 revision：报告记录的是该 revision 已发生的评估事实，即使实例 head 已前移也可以保存。是否允许该报告晋升当前 head 属于 M2.4 的 stale 检查，不能由 M2.3 的“报告已保存”推导为“报告仍可晋升”。提交的 case evidence 和 actor/reviewer 标签仍不可信；本工作包只证明规则处理、来源绑定和审计过程可重复。

## 13. M2.4 实现映射

| 契约 | 代码位置 | 直接证据 |
| --- | --- | --- |
| 显式晋升命令 | `application/commands.py` | revision、节点、报告、复核与知识选择校验测试 |
| 纯晋升判定 | `domain/services/promotion.py` | 状态、节点、依赖、stale、suite、PASS/FAIL/review 单元测试 |
| Prototype 基线全量重建 | `application/controller.py`、`domain/services/skills.py` | 连续两次晋升的 Prompt、工具、active nodes 与 binding 断言 |
| 知识追加与来源保留 | `application/controller.py` | 新增必填知识成功、旧 binding 时间和 actor 不变测试 |
| 原子晋升事务 | `application/controller.py` | 审计故障注入后 snapshot、audit、idempotency 整体回滚测试 |
| 并发 revision CAS | `infrastructure/sqlite/repositories.py` | 同一 expected revision 两个并发请求一成功一冲突 |
| revision 配置完整性 | `004_instance_configuration_checksum.sql`、`infrastructure/sqlite/repositories.py` | v2→v4 回填、晋升快照读回和 checksum 篡改检测 |

晋升事务的固定顺序为：

```text
幂等重放
  -> 读取最新 Instance 并比较 expected revision
  -> 精确加载 Tree、Prototype、Report、Spec 与可选 Review
  -> PromotionPolicy 验证证据
  -> 从 Prototype + 完整 active nodes 重建候选配置
  -> 复验工具与完整知识 binding 集合
  -> 保存 revision + 1 snapshot
  -> 追加 skill.promoted 审计与幂等结果
  -> 单次 commit
```

`skill.promoted` 审计只保存 from/to revision、node ID 和 report ID，不保存 Prompt、知识内容、rule evidence 或 review comment。评估 evidence 和 reviewer 仍是不可信输入；M2.4 只证明给定证据下的晋升决策与配置变换可重复。
