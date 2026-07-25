# M5 实验协议设计说明

## 1. 目的与适用范围

本文冻结 M5 验证实验的证据结构、比较条件、执行门禁、产物规则和解释边界。它是 M5.2-M5.7 的实现约束，不是实验结果报告。

实验只回答当前 Alpha 在固定 Writer 场景中的工程问题。它不验证公网生产能力，不验证 Engineer 代码执行能力，也不证明 Agent Factory 在所有模型、任务和组织中更优。

## 2. 证据结构

### 2.1 主要生成实验

H1、H2、H4 共用一个 24 任务、2 条件、5 次重复的生成实验：

```text
24 个 task
  x MANUAL / FACTORY
  x 5 次 repetition
  = 240 个正式 run
```

主要分析单位是 `task_id`，不是单个 run。5 次重复用于估计同一任务内波动；统计分析必须先按 `task_id + condition` 聚合，禁止把 240 个相关 run 当作 240 个独立任务。

### 2.2 探索性构建案例

H3 使用独立 `BuildSession` 数据。当前只有一个项目 owner 时，它只能描述该操作者在该仓库中的 active build time、wall-clock time 和排除等待时间，不能估计一般开发者群体的效率收益。

### 2.3 确定性审计验证

H5 不依赖模型输出。验证器从固定 Prototype 注册开始，依次执行克隆、知识绑定、AgentSpec 导出、评估和晋升，再通过审计查询重建完整来源链。缺失、重复、checksum 不一致或 revision 错位均判定失败。

## 3. Writer 场景与任务集

### 3.1 选择 Writer 的理由

Writer 输出可由 JSON Schema、预注册事实、禁止事实和读者约束进行确定性评分。Engineer 任务还依赖代码执行、测试框架、依赖解析、文件系统权限和沙箱；这些因素既超出当前 Runtime 边界，也会使“工厂生产方式”与“执行环境质量”难以区分。

### 3.2 合成知识

正式任务使用虚构产品、API 或内部流程的合成知识，不直接采用广为人知的真实技术资料。每个知识包至少包含：

- 唯一 `knowledge_id`、版本和 SHA-256；
- 必须覆盖的原子事实；
- 容易混淆但不应输出的干扰事实；
- 允许同义表达和明确禁止的错误表达；
- 适用读者与输出限制。

合成知识降低训练记忆混杂，但不会自动代表真实企业知识库的复杂度。外部效度必须在报告中单独限制。

### 3.3 任务分层

- 6 个虚构领域，每个领域 4 个任务，共 24 个任务。
- 每个领域包含 2 个一致性任务和 2 个适应性任务。
- 一致性任务强调固定字段、顺序、类型和必需事实。
- 适应性任务在保持事实不变的前提下，面向初学者、技术负责人或业务读者调整术语、解释深度和行动建议。
- 每个任务在正式冻结前必须通过人工可答性检查和离线 Schema/rubric 校验。

## 4. 比较条件与公平性

### 4.1 MANUAL 条件

MANUAL 使用预先冻结的人工编写 system prompt、任务输入、知识正文和输出要求。正式运行期间不允许根据前序输出修改模板。

### 4.2 FACTORY 条件

FACTORY 必须通过真实生产链创建运行输入：

```text
注册 Writer Prototype
  -> 注册 DomainKnowledge
  -> clone instance
  -> bind knowledge
  -> export AgentSpec
  -> condition adapter 渲染 ModelInvocation
```

FACTORY 条件不能绕过 `FactoryController` 直接拼装一个看起来相似的 AgentSpec。

### 4.3 两组固定项

正式实验中以下项目必须完全相同：

- provider 与模型 snapshot/标识；
- SDK 锁定版本；
- temperature、max output tokens 和其他供应商参数；
- 用户任务文本和 reader profile；
- 模型实际可见的知识正文及其字节序列；
- 请求 timeout、重试上限和并发上限；
- 评分代码、事实词表和人工 rubric。

若供应商不支持 seed，配置中记录 `null`。此时只能声称配置和输入可追溯，不能声称模型输出可逐字复现。

### 4.4 允许差异与解释边界

两组允许的差异是 Agent 的生产工作流及其产生的结构约束，包括 Prompt 组织、AgentSpec 输出约束和工厂来源记录。因此结论是“FACTORY 工作流相对 MANUAL 工作流”的整体效应，不是某个单一机制的消融结论。

知识可见性必须保持公平。M5.5 冻结前应选择“双方都以内联正文向模型提供知识”的主实验路径。不得让 FACTORY 使用检索工具而 MANUAL 直接看到完整正文，除非将检索方式注册为新的独立实验因素。

每个 run 必须保存最终 `ModelInvocation` 的规范化表示、`prompt_hash` 和 `knowledge_checksum`，以便核对模型实际收到的输入，而不是只核对上游配置。

## 5. 预注册与冻结

### 5.1 Pilot 与正式实验隔离

Pilot 只验证 API 兼容性、解析稳定性、rubric 可计算性、限流和成本估算。Pilot：

- 使用独立 experiment ID 和 run ID 命名空间；
- 不进入正式统计结果；
- 不用于根据组间表现挑选更有利的任务；
- 允许修复协议缺陷，但每次修改必须记录原因。

### 5.2 正式冻结内容

M5.5 必须冻结：

- `experiment.yaml`；
- 24 个任务及输出 Schema；
- 6 个知识包及事实词表；
- MANUAL prompt 和 FACTORY Prototype/渲染规则；
- 评分规则与统计分析参数；
- `execution-plan.json`；
- source commit、Python 版本、lockfile checksum 和 SDK 版本；
- provider、模型标识、价格来源与价格快照时间；
- 最大请求数、输入/输出 token 和总成本上限。

冻结 manifest 对每个输入文件记录相对路径、字节数和 SHA-256。冻结后任一输入变化都必须创建新 `experiment_id`；不能覆盖原 manifest。

### 5.3 正式执行审批

完成 pilot 和冻结不等于自动开始真实调用。项目 owner 必须在对话和阶段文档中明确批准以下内容后，M5.6 才能运行：

- provider 与模型；
- 预计和最大请求数；
- 预计和最大 token；
- 单价来源、预计成本和硬成本上限；
- 并发、timeout 与重试策略。

## 6. 执行计划与 run 标识

执行计划由固定 `randomization_seed` 生成，包含全部 240 个 run 的唯一顺序。M5.3 对每个 `seed + condition + task_id + repetition` 计算 SHA-256，再按摘要排序；这避免了 `random.shuffle` 对具体语言运行时与 PRNG 实现的依赖。计划生成后以规范化 JSON 保存并计算 SHA-256，正式执行不能重排。

`run_id` 应由 `experiment_id + condition + task_id + repetition` 使用 UUID5 或等价确定性算法生成。执行顺序不是身份的一部分，以免修复计划元数据时改变 run 身份。

每个计划项必须最终处于以下一种状态：

- `succeeded`：保存原始请求、原始响应和使用量；
- `provider_failed`：供应商明确失败；
- `timed_out`：达到单请求 timeout；
- `filtered`：供应商内容策略阻断；
- `invalid_response`：响应存在但无法满足解析契约；
- `budget_stopped`：运行前成本门禁拒绝发起请求。

失败 run 不得从分母中删除。若达到预算上限导致计划未完成，报告必须说明缺失模式，不能只分析已成功部分后声称完成正式实验。

## 7. 产物目录与不可变规则

M5.3 的原始执行产物使用以下目录：

```text
experiments/
├── definitions/
│   └── <experiment_id>/
│       ├── experiment.yaml
│       ├── execution-plan.json
│       ├── conditions/
│       ├── tasks/
│       ├── knowledge/
│       └── rubrics/
├── runs/
│   └── <experiment_id>/
│       ├── execution-manifest.json
│       ├── requests/
│       │   └── <run_id>.json
│       ├── attempts/
│       │   └── <run_id>/
│       │       ├── 001-started.json
│       │       └── 001-completed.json
│       └── terminal/
│           └── <run_id>.json
├── reviews/
│   └── <experiment_id>/
└── analysis/
    └── <experiment_id>/
        └── <analysis_checksum>/
            ├── summary.json
            ├── metrics.csv
            ├── report.md
            └── artifact-manifest.json
```

规则：

1. 每个 JSON 先写同目录临时文件并 `fsync`，再用同文件系统 hard link 发布到尚不存在的目标名；不使用可能覆盖目标的 replace。
2. 目标已存在时比较规范化字节；完全相同视为幂等重放，不同则报冲突。
3. request、attempt intent、attempt completion 与 terminal run 分文件记录；重试只能追加新 attempt，不能重写旧证据。
4. 原始文件只追加新文件，不原地编辑；评分和报告属于派生产物，可由原始数据重建。
5. API key、Authorization header 和其他凭据禁止进入任何产物。
6. 本地 store 要求临时文件和目标位于支持 hard link 的同一文件系统；对象存储或跨文件系统部署需实现新的 write-once backend。
7. “不可变”指 ArtifactStore API 不覆盖既有路径，不代表本地文件具备数字签名或防管理员篡改能力；正式归档需要只读权限、外部 checksum 清单或对象锁提供更强防篡改证据。
8. 分析 package 先发布三份数据文件，最后发布 `artifact-manifest.json`；消费者只把具有有效 manifest 的目录视为完整 package。中断后使用相同 summary 重试，不删除已发布文件。

## 8. 重试、恢复与成本停止

- 每个 run 最多 2 次重试，即最多 3 个 attempt；仅网络错误、429、供应商 5xx 和 timeout 可重试。
- 4xx 输入错误、内容过滤和解析契约失败默认不自动重试。
- 重试使用相同 run ID、输入和生成参数，并记录供应商 request ID、错误类、时间和退避时长。
- 调用 gateway 前必须先持久化 attempt intent；完成后另写 completion。只有 intent 而没有 completion 时，恢复为 `RESULT_UNKNOWN_AFTER_INTERRUPTION`，不得把它伪装为从未调用或成功结果。
- 恢复时先校验 execution manifest 与计划 SHA-256，再跳过已有终态 run；校验失败必须停止。已有 terminal run 必须与 request 和完整 attempt journal 一致。
- 发起每个新 attempt 前，按所有已落盘 intent 重建 request/prompt/completion token 的保守预算占用；可能突破上限时生成 `budget-stopped` 终态，不先请求后补记。
- M5.3 的 execution manifest 是技术身份，不是 M5.5 正式冻结 manifest；当前预算也只有 request/token 上限，没有价格快照和货币成本上限。
- 执行器当前只支持 `concurrency=1`。并发执行需要额外的全局预算协调和 attempt 租约，不能只提高配置数字。
- 当前 CLI 只暴露 fake gateway。未来 live 命令即使加入，也必须要求显式开关并经过 M5.5 人工审批；CI 和默认测试始终不得启用。

恢复保证的边界是“不会覆盖或重复计入本地结果”，不是“外部调用恰好一次”。若 provider 已接收请求而进程在 completion 落盘前中断，缺少 provider 幂等键时无法排除再次调用产生第二次计费；M5.3 通过 unknown 终态显式保留这种不确定性。

## 9. 指标与判定

### 9.1 H1 结构一致性

`schema_pass_rate = schema_passed_runs / terminal_runs`

- 主要效应：FACTORY 与 MANUAL 的任务级通过率差。
- 支持：95% bootstrap 区间下界至少为 `+0.10`。
- 不支持：95% bootstrap 区间上界低于 `+0.05`。
- 其余区间：证据不足。

### 9.2 H2 知识遗漏

每个任务的 required facts 在冻结 rubric 中定义：

`omission_rate = 1 - covered_required_facts / required_facts_total`

- 支持：相对遗漏率降低的 95% bootstrap 区间下界至少为 20%。
- 不支持：该区间上界不大于 0，即没有可辨认的下降。
- 其余：证据不足；MANUAL 遗漏率为 0 时相对降低未定义，只报告绝对差并判为证据不足。

事实匹配优先使用结构字段、精确值、预注册正则和同义词表。任何运行后新增的同义词只进入敏感性分析，不能改写主要结果。

### 9.3 H4 个性化适应度

`adaptation = satisfied_constraints / personalization_constraints_total`

- 使用配对任务差 `FACTORY - MANUAL`。
- 非劣界值为 `-0.05`；95% 区间下界不低于该值时支持“未明显损害”。
- 95% 区间上界低于该值时不支持；跨越界值时证据不足。

### 9.4 次要指标

- 同一任务 5 次确定性质量分的样本方差；
- latency、prompt/completion tokens 和估算成本；
- 无效响应率、超时率和供应商失败率；
- 盲化人工评分；
- LLM-as-Judge 探索性评分。

次要指标不能替代 H1/H2/H4 的预注册主要指标。

### 9.5 H3 与 H5

H3 报告第一个领域的初始化成本，以及第 2 个及以后领域的 active seconds、wall-clock seconds、排除等待时间、中位数、IQR 和配对差。单操作者数据不做显著性检验。

H5 的 expected steps 在运行前冻结。每一步必须同时匹配 event type、entity ID、revision、Prototype/Knowledge/AgentSpec checksum 和发生顺序。固定路径的完整率必须为 100%。

## 10. 统计分析

1. 先按 `task_id + condition` 聚合 5 次重复。
2. H1/H2/H4 使用相同 task 的配对差。
3. 使用以 task 为抽样单位、按 scenario 保持 strata 规模的 10,000 次 bootstrap 报告 95% 区间；H4 只包含 adaptation stratum。
4. 同时报告绝对差、相对差和原始分母，不只给 p 值。
5. 方差比较属于次要分析；若引入 Brown-Forsythe，依赖和实现必须在 M5.5 前冻结。
6. 报告所有失败和缺失，不对失败样本做无说明的 complete-case 删除。
7. 分析实现必须是可测试的 Python 模块；notebook 不能成为唯一计算来源。

主要分析采用 intention-to-treat：provider、timeout、filter、invalid-response 和 budget-stopped run 在 H1 中按 Schema 未通过、H2 中按 required facts 全部遗漏、H4 中按个性化约束全部未满足计入。只分析成功 run 的结果必须明确标注为次要敏感性分析，不能替代主要结论。

结论只使用“支持”“不支持”“证据不足”。即使区间和阈值均有利，也只能限定到冻结模型、Writer 任务集和本次 workflow bundle。

## 11. 人工评审与盲化

- 评审包使用与 condition 无关的随机 `review_item_id`，不展示 run ID、Prompt、AgentSpec 或审计信息。
- condition 映射保存在独立文件，在评分冻结后才合并。
- 人工评分者不得根据格式猜测后修改 rubric。
- 若只有一名评分者，只报告其评分分布和抽样范围。
- 只有至少两名独立评分者复评不少于 20% 的预注册样本时，才计算加权 Cohen's kappa；否则不作评审者间信度声明。

## 12. 效度威胁

### 12.1 内部效度

- 两组 Prompt 组织不同，因此比较的是整体工作流而非单一组件。
- 模型服务随时间漂移，固定确定性混排只能降低、不能消除该影响；当前计划不保证两条件逐项严格交替。
- rubric 可能偏向结构化输出；必须公开任务、Schema 和两组最终输入。

### 12.2 构念效度

- Schema 通过和事实覆盖不等于完整语义质量。
- 合成知识降低记忆混杂，但可能比真实知识更规整。
- 个性化约束只能近似“适应性”，不能覆盖写作风格的全部维度。

### 12.3 外部效度

- 单 Writer 场景、单模型和 24 个任务不能外推到 Engineer、多 Agent 或真实企业生产。
- H3 的单操作者结果不代表团队或新人。

### 12.4 结论效度

- 任务数为 24，阈值附近结果可能不稳定，应完整报告区间。
- 5 次重复不是 5 个独立任务，错误地按 run 推断会夸大样本量。
- 无第二评分者时，人工评分的可靠性未知。

## 13. M5.2 契约与 fixture 落地

M5.2 已在仓库级 `experiments` package 实现并测试以下对象：

- `ExperimentDefinition`
- `ExperimentTask`
- `KnowledgeFixture`
- `RubricDefinition`
- `ExecutionPlan` / `ExecutionPlanItem`
- `ExperimentRun` / `RunAttempt`
- `MetricRecord`
- `BuildSession`
- `AuditVerificationRecord`

具体字段和跨字段校验以 `experiments/contracts.py` 为唯一代码真相源。全部模型继承现有 `FrozenModel`，拒绝额外字段并使用规范化 JSON checksum。知识正文是例外：它不进入会去除字符串首尾空白的 Pydantic 模型，而由 `LoadedExperimentDataset.knowledge_bytes` 以只读原始字节保存并直接计算 SHA-256。

`experiments/loader.py` 只接受 fixture 根目录内的相对路径，使用 `yaml.safe_load()`，限制 YAML 为 256 KiB、知识正文为 128 KiB，并验证 UTF-8、JSON Schema Draft 2020-12、知识 checksum、事实 matcher、rubric 引用和每领域 `2 consistency + 2 adaptation` 矩阵。

冻结 fixture 位于 `experiments/definitions/writer-v1/`，包含 6 份知识、24 个任务和 24 份 rubric，dataset checksum 为 `673b6866d58853a5c788ccff5b6acdc6511ee01b1085439d3d1353811dd3d51b`。该 checksum 不包含绝对路径，因此相同字节复制到其他工作目录仍得到同一值。

M5.2 只定义了 `ExecutionPlan`、`ExperimentRun` 等产物契约；计划生成、条件渲染、run 文件写入与恢复已在 M5.3 落地。真实 provider 调用仍未实现，也不得把 fake gateway 的执行能力表述为正式实验已经运行。

## 14. M5.3 执行基础设施落地

M5.3 新增以下可测试模块：

- `experiments/planning.py`：构建、校验和加载规范化执行计划；生成并校验 technical execution manifest。
- `experiments/rendering.py`：渲染 MANUAL 与 FACTORY provider 输入，计算 `prompt_hash`，并校验任务与知识可见性公平。
- `experiments/artifacts.py`：提供 canonical、bounded、path-contained 的 write-once 文件存储。
- `experiments/gateway.py`：定义原始成功/失败证据边界与确定性 fake gateway。
- `experiments/executor.py`：顺序执行固定计划，记录 intent/completion journal，执行有限重试、预算停止和断点恢复。
- `experiments/cli.py`：提供 `plan`、`verify-plan` 与非证据性的 `run-fake` 离线命令；没有 live 子命令。

冻结 Writer fixture 的执行计划共有 240 项，checksum 为 `81c535b96bcd3b33ea217dd031953a7f7fc6ae586c995172956324b2b7b7996f`。MANUAL prompt 字节与 renderer version 组成的 condition bundle checksum 为 `17781f2fb7d88c4f38edce23580f4eab6b06a4b7e5330b85a20d427fb36b0d76`。FACTORY 条件的集成测试通过真实 `FactoryController` 完成原型注册、知识注册、克隆、绑定和 `AgentSpec` 导出，再与 MANUAL 条件逐字节核对共同 task input。

离线定向门禁为 `83 passed`，`experiments` 分支覆盖率 92.84%；全量回归为 `492 passed`，生产代码总覆盖率 92%，其中 domain 96%、application 95%。这组证据证明代码路径、契约和故障恢复行为满足 M5.3 规格，不证明真实模型质量、正式预算安全或实验命题成立。

## 15. M5.4.1 评分证据契约

M5.4.1 在 `experiments/contracts.py` 固定逐 run 评分证据，尚未实现 matcher、Schema 执行或统计分析。`RunScoreRecord` 必须同时引用源 `ExperimentRun` checksum 和 `RubricDefinition` checksum；成功记录包含排序后的 Schema 违规、required fact 覆盖、forbidden matcher 违规和个性化约束明细，失败记录不携带这些派生评分。

确定性质量分只作为次要摘要：Schema 通过率、required fact 覆盖率、存在 forbidden matcher 时的合规率，以及存在个性化约束时的满足率组成适用分量，取等权平均并舍入到 12 位小数。模型会根据逐项证据重新计算分母、分子和总分，拒绝客户端直接提交不一致汇总。人工评分不进入 `RunScoreRecord`，继续由独立盲化评审流程管理。

M5.4.1 离线定向门禁为 `93 passed`，`experiments` 分支覆盖率 92.75%；全量回归为 `502 passed`。这些结果不包含任何真实模型输出或统计结论。

## 16. M5.4.2 确定性评分器

`DeterministicScorer.score(run)` 是纯函数式评分入口：它只读取已验证 dataset 和不可变 terminal run，不访问 provider、网络、数据库或时钟。评分前必须匹配 experiment ID、任务、repetition、知识 checksum、rubric 和条件来源；FACTORY 必须具有 AgentSpec checksum，MANUAL 不得声明该来源。

Schema 使用 `Draft202012Validator` 执行。违规记录只包含 RFC 6901 风格的实例路径、Schema 路径和 validator 名称；相同违规去重并排序，不保存可能回显输出正文的 message。required fact 的 accepted matcher 按冻结顺序检查并记录首个命中索引；forbidden matcher 按 rubric 顺序逐项记录。结构化输出只将 value 递归展平为文本，对象 key 排序、数组顺序保留，字段名本身不进入匹配文本。

loader 与评分器共用 `experiments/matching.py`。exact matcher 是可配置大小写的子字符串匹配；regex matcher 使用相同 flags 与 100ms timeout。timeout 是评分失败而不是“未命中”。personalization 声明 `target_field` 时只读取该字段，避免其他字段中的相同词造成假阳性；未声明时读取完整输出。

M5.4.2 完整实验门禁为 `108 passed`，分支覆盖率 93.03%，`scoring.py` 覆盖率 96%；全量回归为 `517 passed`。该门禁使用合成 fixture 和离线 terminal run，不构成正式实验结果。

## 17. M5.4.3 任务级聚合与确定性 Bootstrap

`experiments/analysis.py` 只接受冻结 dataset、与其完全一致的 `ExecutionPlan` 和完整 `RunScoreRecord` 集合。分析前必须证明 240 个 `run_id` 各出现一次，并逐项核对 condition、task、repetition、execution order、scenario、plan checksum、rubric checksum 与 rubric 明细。评分集合按 execution order 规范化后计算 `score_set_checksum`；输入迭代顺序不进入分析身份。

聚合产物为 `TaskConditionAggregate`。每个 task 的 5 次重复先合并为 Schema 通过率、required fact 遗漏率和适用的个性化满足率，再形成 MANUAL/FACTORY 配对。主要 population 为 `intention-to-treat`：执行失败仍进入固定分母并映射为三项最差结果。`succeeded-only` 允许某个 task-condition 无样本，此时 rate 为 null；只有两侧均存在成功样本的 task 才进入敏感性配对，且 `HypothesisResult.decision` 强制为 `not-evaluated`。

H1 和 H2 分别在 consistency、adaptation strata 内按原 task 数有放回抽样，H4 只对 12 个 adaptation task 抽样。索引由 analysis seed、hypothesis、population、scenario、replicate、draw 和 nonce 的规范化 JSON 计算 SHA-256，再对前 64 位执行 rejection sampling，避免直接取模偏差。该算法不依赖 Python `random`、NumPy、SciPy 或 notebook。95% percentile interval 显式使用 Hyndman-Fan Type-7 线性插值，所有对外浮点结果保留 12 位小数。

H2 同时报告绝对遗漏率差和相对遗漏率降低。相对值以成对 task 的平均 MANUAL 遗漏率为分母；分母为零的 bootstrap replicate 记为 invalid，而不是填充 0 或删除后不留痕。有效 replicate 少于请求数的 95%，或总体 MANUAL 遗漏率为零时，主要判定固定为“证据不足”，绝对差区间仍然输出。H1、H2、H4 的支持/不支持阈值直接读取冻结 `ExperimentDefinition.thresholds`，阈值之间的区间统一返回“证据不足”。

M5.4.3 完整实验门禁为 `124 passed`，`experiments` 分支覆盖率 93%，`analysis.py` 覆盖率 99%；全量回归为 `533 passed`。这些测试使用完整坐标规模的合成评分证据，验证的是算法可重复性和错误拒绝逻辑，不是 Writer 模型质量结果。

## 18. M5.4.4 确定性报告与 Manifest-last 发布

`experiments/reporting.py` 将一个已经验证的 `AnalysisSummary` 映射为 content-addressed package。package 身份是 `sha256_model(summary)`，路径为 `analysis/<experiment_id>/<analysis_checksum>/`。发布器不读取 terminal run、不重新评分、不访问网络、时钟或模型；输入变化会产生新的 package 路径，输入不变则逐字节幂等重放。

`summary.json` 使用项目规范化 JSON，是唯一机器事实源。`metrics.csv` 固定 17 列，每个 `population + task + condition` 一行；空的非适用 rate 输出为空字段，不写 `N/A`。`report.md` 展示分析 checksum、数据集/计划/score set/config 身份、两组计划与失败数、ITT 的 H1/H2/H4、成功样本敏感性和证据边界。报告模板为 ASCII 英文，避免 Python 源码编码和标点 lint 差异；实验协议与解释文档继续使用中文。

发布顺序是 `summary.json -> metrics.csv -> report.md -> artifact-manifest.json`。前三个文件均成功后，manifest 才作为 commit marker 发布；它按固定顺序记录每个文件的媒体类型、字节数和 SHA-256。崩溃若发生在 manifest 之前，目录不被视为完整，使用相同输入重试可幂等补齐。多文件并不具备单事务原子性，这一协议提供的是可检测、可恢复的提交边界。

验证器先核对 manifest 与 package 路径的 experiment/analysis 身份，再核对三个文件的长度与 checksum，并读取规范化 `AnalysisSummary` 重算 analysis checksum。最后重新渲染 CSV 和 Markdown 并逐字节比较。因此，仅同步改写展示文件和 manifest 仍不能伪装成由 summary 生成的展示结果；若管理员同时修改所有文件和 summary，本地 package 仍缺少外部签名提供的防篡改保证。

M5.4.4 完整实验门禁为 `135 passed`，`experiments` 分支覆盖率 93%，`reporting.py` 覆盖率 100%；全量回归为 `544 passed`。该工作包没有读取正式模型数据。

## 19. M5.4.5 只读证据回放与评分提交

`ExperimentEvidenceLoader` 是独立于执行器的只读边界。它按 `ExecutionPlan.execution_order` 加载 execution manifest、request、attempt intent/completion 和 terminal run，逐层核对 run/manifest/plan identity、generation、Prompt hash、知识 checksum、AgentSpec 来源、token reservation、重试顺序与时间关系。分析入口不得调用 `ExperimentExecutor.execute()`，因为执行器在孤立 intent 后会补写 `RESULT_UNKNOWN_AFTER_INTERRUPTION`，这与离线复算不得修改原始证据的要求冲突。

加载器使用 `ArtifactStore.list_files()` 比较计划期望文件集合和实际普通文件集合。枚举上限为 10,000，结果按 root-relative POSIX 路径排序，目录树中出现符号链接或特殊文件即失败。固定计划中缺少 request/terminal/attempt，或出现计划外 terminal 和孤立 journal，都在评分前拒绝。

评分 package 路径为 `scores/<experiment_id>/<execution_manifest_checksum>/`。240 个 `RunScoreRecord` 先写入 `records/<run_id>.json`，最后写 `score-manifest.json` 作为提交标志。`ScoreArtifactManifest` 绑定 dataset、plan、execution manifest、run count、连续 execution order、逐 run checksum、逐 score checksum、字节数和整个规范评分集合的 checksum。验证器从磁盘读取每条评分后重新计算这些身份；原 terminal run 改变时，同一 package 路径上的 write-once 冲突阻止新证据覆盖旧评分。

`OfflineAnalysisPipeline.run()` 的固定顺序是 `journal validation -> deterministic scoring -> score package commit -> persisted score verification -> paired analysis -> report package commit`。`python -m experiments analyze --runs-root <runs> --output-root <derived>` 是该流水线的唯一 CLI 入口，不包含 provider、凭据、live switch 或网络配置。相同完整输入和 `AnalysisConfig` 必须产生逐字节相同的 score/report package；缺少评分 Manifest 或报告 Manifest 的目录都只属于可恢复的未提交产物。

该链路可以证明“报告可由指定本地执行证据重复派生”，不能证明 fake run 是正式实验，也不能防止本地管理员同步改写全部文件和 checksum。正式 M5.6 仍必须由 M5.5 冻结 Manifest、外部归档和项目 owner 审批建立证据身份。

M5.4.5 的 CI 同构 experiment 门禁为 `149 passed`，分支覆盖率 92.99%；全仓回归为 `558 passed`。完整链路测试使用 fake gateway 生成的合成终态，只验证 240-run 工程规模、恢复语义和可重复派生，不产生正式实验结论。

## 20. M5.5.1 正式冻结与精确成本契约

M5.5.1 将冻结证据建模为 `FrozenExperimentManifest`，而不是继续扩充 M5.3 `ExecutionManifest`。后者仍是 executor 恢复所需的技术身份；前者内联并绑定它，同时增加 `AnalysisConfig`、source commit、CPython/SDK 版本、lockfile、Provider/模型、价格、成本和文件清单。这样旧的离线 journal 保持兼容，正式身份通过外层 checksum 链补充。

金额统一使用 integer USD micros，单价统一表示为“每 1,000,000 token 的微美元整数”。估算成本对 uncached input 和 output 分别执行整数向上取整后相加，不依赖 binary float。缓存单价只作为冻结事实记录；预算预留默认按完整 input 单价计算，不能提前假设缓存命中。估算请求/token 必须位于 technical limits 内，hard cost limit 必须覆盖估算，同时不得宽于全部 token 上限按冻结单价换算的保守 ceiling。

`ExperimentPurpose` 明确区分 `pilot` 和 `formal`。formal Manifest 必须引用另一 experiment ID 的 `PilotEvidenceRef`；pilot Manifest 不得引用 Pilot 结果。该约束阻止同一 run ID 空间同时承担调试与正式统计身份，但不证明 Pilot 已真实执行，后者必须由 M5.5 后续报告 checksum 和人工审查建立。

文件 inventory 必须按路径排序、去重，并且唯一 `uv.lock` 条目的 checksum 必须与 `SourceSnapshot` 一致。M5.5.1 的 Pydantic 模型只验证已提交字段之间的关系，不读取 Git、文件系统或官方价格页面，也不自行验证自引用 `manifest_checksum`；这些 I/O 和 checksum 责任由 M5.5.2 的候选生成器与 verifier 承担。

M5.5.1 的 CI 同构 experiment 门禁为 `158 passed`，分支覆盖率 93.30%；全仓回归为 `567 passed`。所有价格、Provider 和模型输入均为测试合成值，无网络或真实模型调用。
