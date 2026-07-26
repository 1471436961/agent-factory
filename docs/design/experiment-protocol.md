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

- `dataset.yaml`；
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
- 默认 CLI 与 CI 不启用 live gateway。M5.5.6 的 `run-pilot-live` 仅在完整 Freeze Manifest 环境验证、显式 live 开关、实验 ID 和硬费用上限同时匹配后可到达 provider；默认测试始终使用 fake client。

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

冻结 fixture 位于 `experiments/definitions/writer-v1/`，包含 6 份知识、24 个任务和 24 份 rubric。M5.2 初始 dataset checksum 为 `673b6866d58853a5c788ccff5b6acdc6511ee01b1085439d3d1353811dd3d51b`；M5.5.3 将 `tasks_per_scenario_per_domain` 纳入定义后，当前 checksum 为 `e8305386e305e39623ab1e852059148ed319ae63fc180a58288f1ac0a3e14a8e`。该 checksum 不包含绝对路径，因此相同字节复制到其他工作目录仍得到同一值。

M5.2 只定义了 `ExecutionPlan`、`ExperimentRun` 等产物契约；计划生成、条件渲染、run 文件写入与恢复已在 M5.3 落地。真实 provider 调用仍未实现，也不得把 fake gateway 的执行能力表述为正式实验已经运行。

## 14. M5.3 执行基础设施落地

M5.3 新增以下可测试模块：

- `experiments/planning.py`：构建、校验和加载规范化执行计划；生成并校验 technical execution manifest。
- `experiments/rendering.py`：渲染 MANUAL 与 FACTORY provider 输入，计算 `prompt_hash`，并校验任务与知识可见性公平。
- `experiments/artifacts.py`：提供 canonical、bounded、path-contained 的 write-once 文件存储。
- `experiments/gateway.py`：定义原始成功/失败证据边界与确定性 fake gateway。
- `experiments/executor.py`：顺序执行固定计划，记录 intent/completion journal，执行有限重试、预算停止和断点恢复。
- `experiments/cli.py`：提供 `plan`、`verify-plan` 与非证据性的 `run-fake` 离线命令；没有 live 子命令。

冻结 Writer fixture 的执行计划共有 240 项；M5.3 初始 checksum 为 `81c535b96bcd3b33ea217dd031953a7f7fc6ae586c995172956324b2b7b7996f`，M5.5.3 绑定新 definition checksum 后的当前值为 `8e8ad93a8cb1b3207580c89917e4af9a6ac0c32c6ab47d83e04c6f04b233e920`。MANUAL prompt 字节与 renderer version 组成的 condition bundle checksum 为 `17781f2fb7d88c4f38edce23580f4eab6b06a4b7e5330b85a20d427fb36b0d76`。FACTORY 条件的集成测试通过真实 `FactoryController` 完成原型注册、知识注册、克隆、绑定和 `AgentSpec` 导出，再与 MANUAL 条件逐字节核对共同 task input。

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

## 21. M5.5.2 冻结候选生成与验证

`FreezeCandidateSpec` 是人工评审输入，不允许调用方填写 Git commit、Python 环境、文件 checksum、analysis config checksum 或最终 Manifest checksum。它使用有界、规范化、末尾换行的 JSON；`created_at` 也来自评审 spec，而不是构建时读取时钟，因此相同输入、源码和环境产生逐字节相同候选。spec 中的文件清单必须排序、去重并显式包含自身与 `uv.lock`。

`FreezeCandidateBuilder` 依次校验 dataset、execution plan、technical manifest、condition bundle、definition checksum、provider/model/SDK 与预算关系，再要求清单覆盖 dataset、知识声明及正文、task、rubric、MANUAL prompt、plan、spec 和 lockfile。构建器不使用 glob 自动决定证据范围；显式清单可以增加经评审的输入，但不能漏掉已知必需项。每个文件按仓库相对 POSIX 路径读取一次，拒绝路径逃逸、符号链接、空文件、超过 2 MiB 的单文件和超过 32 MiB 的总清单。

```text
规范 Candidate Spec + Dataset + Plan
                │
                ▼
    校验身份、预算与必需文件集合
                │
                ▼
       Git clean snapshot（读取前）
                │
                ▼
     读取文件字节并计算 size/SHA-256
                │
                ▼
       Git clean snapshot（读取后）
                │  两次必须完全相同
                ▼
  采集 CPython/SDK → 计算 Manifest checksum
                │
                ▼
       write-once canonical JSON candidate
```

Git 通过参数数组、`shell=False`、10 秒 timeout 和输出大小上限读取，不执行任何仓库写操作。候选输出位于仓库外或被 Git 忽略的 `.tmp/`，避免生成行为本身污染被冻结工作树。`freeze-candidate` 负责生成，`verify-freeze` 默认同时执行内容和当前环境校验；显式 `--content-only` 只验证可移植文件证据，不检查当前 Git/Python/SDK，输出中必须标明该较弱范围。

M5.5.2 只实现机制并使用合成 Provider、模型和价格做离线测试。它不访问价格来源 URL，不读取 API key，不执行 provider 请求，也不生成获得批准的真实冻结候选。Manifest checksum 只能发现非同步篡改，不能提供签名真实性；正式冻结仍需后续 pilot 证据、项目 owner 审批与外部只读归档。

M5.5.2 的 CI 同构 experiment 门禁为 `173 passed`，`experiments` 分支覆盖率 92.59%，其中 `freezing.py` 为 90%、`cli.py` 为 98%；全仓回归为 `582 passed`。Ruff format、Ruff lint、全量 mypy strict 和既有契约快照检查均通过。测试没有网络请求或真实模型调用。

## 22. M5.5.3 Pilot 定义与预算预检

Pilot 不是从 24 个正式任务中抽样。`writer-pilot-v1` 使用 2 个新的 synthetic domain，每个领域各有 1 个 consistency 和 1 个 adaptation 任务；1 次重复、2 个条件共产生 8 个 run。`ExperimentDefinition.tasks_per_scenario_per_domain` 参与定义和数据集 checksum，loader 同时校验声明任务总数与逐领域实际矩阵，避免为 Pilot 放宽正式 fixture 的完整性规则。

`validate_pilot_preflight()` 接受 Pilot dataset/plan、`FreezeCandidateSpec` 与 formal dataset/plan。检查顺序为：分别校验两份计划和 Pilot technical manifest；校验候选 purpose、definition 与 condition bundle；证明 experiment/domain/task/rubric/knowledge/run 身份集合不相交；最后重算 request、prompt token、completion token 和微美元成本。预检要求 `repetitions=1`、1+1 场景矩阵、固定 model snapshot 与 `concurrency=1`，并要求 technical limits 精确等于 `run_count * max_attempts` 的最坏边界，不能通过虚高预算绕开评审。

评审候选固定 OpenAI Responses API、`gpt-4.1-mini-2025-04-14`、OpenAI SDK `2.46.0`、`temperature=0`、`max_output_tokens=1024`、60 秒 timeout、最多 2 次 attempt 和单并发。价格于 2026-07-26 从 [OpenAI GPT-4.1 mini 官方模型页](https://developers.openai.com/api/docs/models/gpt-4.1-mini)核验：每百万 token 输入 400,000 微美元、缓存输入 100,000 微美元、输出 1,600,000 微美元。预算始终按未缓存输入计算。

预期一次 attempt 对应 8 次请求、32,000 prompt token、8,192 completion token 和 25,908 微美元；最坏两次 attempt 对应 16 次请求、64,000 prompt token、16,384 completion token 和 51,815 微美元。M5.5.3 当时的 tracked `freeze-candidate.json` 固定这些人工评审输入及 60 项显式 inventory，但不伪造 source commit 或文件 checksum。只有工作包提交后，`freeze-candidate` 才能在干净工作树上派生真正的 `FrozenExperimentManifest`。

Pilot 候选使用以下纯离线命令预检；它没有 API key 参数、live switch 或 provider client：

```bash
uv run python -m experiments verify-pilot \
  --definition-root experiments/definitions/writer-pilot-v1 \
  --spec experiments/definitions/writer-pilot-v1/freeze-candidate.json
```

M5.5.3 的测试使用 fake Git/environment 构建并验证候选，只证明 fixture、身份和预算自洽；实际 Pilot 运行仍须项目 owner 单独批准。

## 23. M5.5.4 OpenAI 实验 gateway

`GatewayRequest.expected_output_schema` 是 executor 从冻结任务表注入的本地验证契约，不进入 provider-visible invocation，也不改变 `prompt_hash`。MANUAL invocation 保持 `output_schema=null` 并映射为 Responses API `text.format={"type":"json_object"}`；FACTORY invocation 必须携带与 expected schema 字节等价的 Schema，并映射为 strict `json_schema`。两组返回值都要解析为 JSON object，再由同一 Draft 2020-12 Schema 校验，因此不会以 FACTORY 的 provider 约束替代共同的离线评分入口条件。

`create_openai_experiment_gateway()` 只接收显式传入的 API key，并以 `max_retries=0` 构造官方 `AsyncOpenAI`。SDK 不拥有隐藏重试权，429、5xx、timeout 和明确网络错误由 gateway 分类后交给 executor 的 write-once journal 与两次 attempt 上限处理；未知 SDK 异常按不可自动重试的 client error 处理。成功证据保留不超过 1 MiB 的规范化原始响应、request ID、输出正文和 usage；错误证据上限为 64 KiB，异常字符串不写入产物，畸形或超长 request ID 被丢弃。

M5.5.4 没有新增 live CLI，没有读取环境变量或 API key，也没有发起网络请求。31 项 gateway 定向测试使用 fake client 验证 MANUAL/FACTORY 请求映射、SDK `2.46.0` Responses 方法签名、结构化输出、usage、过滤与异常分类；CI 同构 experiment 门禁为 `211 passed`，总分支覆盖率 `93.04%`，其中 `openai_gateway.py` 为 `97%`。这些证据证明本地 adapter 契约与锁定 SDK 表面兼容，不证明真实 API 权限、模型可用性、限流和计费行为；下一步必须先在干净提交上生成并验证 Pilot freeze manifest，再由项目 owner 单独批准真实 Pilot。

## 24. M5.5.5 Pilot freeze manifest 归档

freeze 必须在任何归档或文档修改前，从干净 commit 生成。M5.5.5 使用 source commit `5a5d58cb42b62e3d2e10a060fea72d4ae0a97498`，先输出到 Git 忽略的 `.tmp/m5-freeze/`，随后在工作树仍干净时依次执行默认 `content-and-environment` 验证与 `--content-only` 验证。默认验证额外检查当前 HEAD、porcelain status、CPython `3.11.15` 和 OpenAI SDK `2.46.0`；content-only 验证只检查 Manifest 自身份、dataset/plan/execution identity、候选字段和 61 项实际文件字节。

通过验证的原始字节不经重新序列化，现保留为 [`experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.5.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.5.json)。内部 `manifest_checksum` 为 `2673435ce2623c7c5bfaeb4a011c72f0558ef557c3506bba6685d114357bb6af`，整个 JSON 文件的 SHA-256 为 `a3216e6b292126c5041ab701c1864c53e56ba15faac3d33ecd55c69d3a59d7b2`。前者排除 `manifest_checksum` 字段后计算结构身份，后者覆盖最终落盘字节；回归测试固定两者、source commit 和 inventory 数量。

Manifest 不能把自身作为被哈希输入，因此归档证据必然位于 source commit 之后的提交。归档后的当前 HEAD 不应冒充冻结执行环境：默认 verifier 因 commit 或工作树不匹配而拒绝属于正确行为；需要环境级复核时必须 checkout source commit，归档提交日常只运行 content-only 验证。本地 Git 历史提供 Alpha 追溯，但不能替代签名、外部只读归档或对象锁。

最终审计确认 source commit 中只有 `run-fake` CLI，没有把 `OpenAIExperimentGateway`、归档 Manifest、显式 live 开关和输出目录装配成可复现命令。通过仓库外临时脚本执行会引入未冻结代码，不满足本实验的证据要求。因此本 Manifest 只承担执行前源码与配置检查点；M5.5 后续必须先实现受控 Pilot launcher，提交后重新生成 Manifest，旧 Manifest 保留为历史证据但不得用于费用授权。M5.5.5 不读取 API key、不调用 provider，也不请求项目 owner 批准真实 Pilot。

M5.5.5 的 CI 同构 experiment 门禁为 `212 passed`，总分支覆盖率 `93.04%`；全仓回归为 `622 passed`。Ruff format、Ruff lint、全量 mypy strict、契约快照、Pilot 预算预检和归档 Manifest content-only 验证均通过。

## 25. M5.5.6 受控 Pilot launcher 与准备证据

`run-pilot-live` 是固定 Pilot 的窄入口，不是通用 provider runner。它没有 API key 参数和部分计划开关；调用方必须提供 Manifest 与输出根，并逐字确认 `writer-pilot-v1` 和 `51815` 微美元硬上限。启动器始终执行 Manifest 内联的全部 8 个计划坐标，模型、参数、重试、token 和请求上限均来自冻结 technical manifest，不接受 CLI 覆盖或 fallback。

```text
Dataset + Plan + FrozenExperimentManifest
                  │
                  ▼
      content-and-environment verifier
                  │
                  ▼
       Pilot/Formal isolation + budget
                  │
                  ▼
      live / experiment / cost confirmations
                  │
                  ▼
   safe output root + FactoryController prepare
                  │
                  ▼
       validate all MANUAL/FACTORY pairs
                  │
                  ▼
       read OPENAI_API_KEY → create client
                  │
                  ▼
       recover or execute all eight runs
                  │
                  ▼
             close client in finally
```

`PilotFactoryPreparation` 绑定 experiment、dataset 与 execution manifest checksum，并按 domain 保存 controller 导出的 `AgentSpec` 和完整审计事件。准备链固定为 published prototype、URI 型 synthetic knowledge package、clone、bind 和 export；冻结知识正文仍由 dataset loader 按原始字节提供给两组 renderer，避免 Pydantic 字符串规范化改变 Markdown checksum。preparation 使用 write-once canonical JSON；已有文件只可逐字重放，SQLite 只用于首次控制器事务与审计生成，不成为恢复时的第二真相源。

输出根位于仓库内时必须处于 `.tmp/`，也可在仓库外；现存路径段中的符号链接被拒绝。factory preparation 使用 `_factory-preparation/` 前缀，与 evidence loader 只读取的 `<experiment_id>/` journal 分离。API key 只通过 `EnvironmentApiKeySource` 在全部离线门禁和工厂准备完成后读取，不进入 argparse、Pydantic 模型、日志或产物。

启动器对实际已记录 usage 使用冻结单价重新计算保守微美元费用，同时保留 request/token/费用硬上限。provider 不提供幂等键时，intent 已写但 completion 未落盘的窗口仍可能重复计费；现有恢复语义会记录 `RESULT_UNKNOWN_AFTER_INTERRUPTION`，并由最多两次 attempt 与总预算限制风险，不能宣称 exactly-once 外部调用。

M5.5.6 的 tests 以真实 `FactoryController` 和 live 标记的 fake gateway 验证 2 个 domain、12 条审计事件、8 个坐标、重放零新增请求、Key 读取顺序、路径拒绝、异常关闭和 secret 不落盘。CI 同构 experiment 门禁为 `225 passed`、总分支覆盖率 `92.34%`；全仓回归为 `634 passed`。这些结果只证明 launcher 的工程约束，不证明真实 API、计费或模型质量。M5.5.5 归档保持历史不变；launcher 提交后必须重新冻结，才可进入真实 Pilot 审批。

## 26. M5.5.7 最终 Pilot Manifest 与双归档

M5.5.7 先提交 launcher，再以 clean source commit `d3c19beb75587b5cc9963c05832c918694dfa9e1` 构建最终 Manifest。生成与两种验证全部发生在 `.tmp/m5.5.7-freeze/`，此时 tracked 工作树没有变化：默认 verifier 同时核对 HEAD、porcelain、CPython `3.11.15`、OpenAI SDK `2.46.0` 和 62 项文件；content-only verifier 独立复核 Manifest、dataset、plan、execution identity 及文件字节。两者均通过后才复制已经验证的原始 JSON 字节。

```text
clean d3c19be + 62-item inventory
                │
                ▼
        build under ignored .tmp
                │
                ▼
 content-and-environment + content-only
                │
                ▼
 archive exact bytes, never re-serialize
        ┌───────┴────────┐
        ▼                ▼
 historical M5.5.5   canonical final
 61 files, no CLI    62 files, launcher
```

M5.5.7 Manifest 现以原始字节保存在 [`freeze-manifest-m5.5.7.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.7.json)，内部 checksum 为 `6514a01799af9b6585f4ff009ad11c887439a324200771d0cae479f28f630d22`，文件 SHA-256 为 `994f0d46557adeea77703849b0eb3978abe3d9fe89a1741c01b802ffcd2d2740`。M5.5.5 文件仍保存在 [`freeze-manifest-m5.5.5.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.5.json)，旧 checksum、source commit 和 61 项 inventory 保持不变。

归档提交不能与 Manifest 的 source commit 相同，这是自引用证据的结构性结果，不是漂移。M5.5.7 不读取 API key、不创建真实 client，也不调用 provider。后续收尾审计发现该文件虽包含 launcher，却没有冻结 launcher 调用的 `agent_factory` 生产代码，因此它降级为历史检查点，不再承担费用授权入口。

## 27. M5.5 收尾：生产依赖冻结闭环

`PilotFactoryPreparation` 不是只依赖 `experiments` package 的纯渲染器。它通过 `build_container()` 和 `FactoryController` 执行 migration、原型注册、知识注册、克隆、绑定与 AgentSpec 导出。仅冻结直接 import 文件仍可能漏掉 Controller 的传递依赖和 SQL 资源，因此收尾方案选择完整纳入 `src/agent_factory` 下所有 `.py` 与 `.sql`，而不是维护易漂移的手工调用图。

```text
Pilot fixture + experiments package
                 │
                 ├── run-pilot-live
                 │        │
                 │        ▼
                 │   FactoryController
                 │        │
                 │        ├── domain/application services
                 │        └── SQLite repositories + migrations
                 │
                 ▼
       153-item frozen inventory
```

修正后的 candidate 包含原 62 项输入和 91 项生产文件。新 canonical [`freeze-manifest.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest.json) 绑定 source commit `e76adc778300b73b5973920fbaaa72275501db8d`，内部 checksum 为 `58afac123924e0604ec4067f0492781e7115a97b6c14900aee5bcff8fcd05713`，文件 SHA-256 为 `9758d465b44663baf18ced7f06ef51292d57037e1840c7dc17ba63fb94a1cecf`。该文件在任何 tracked 归档变化前通过环境级和 content-only 验证；归档后日常只做 content-only，环境级复核必须 checkout source commit。

这次修正不新增 M5.5 子里程碑编号。真实运行使用 E 盘 detached worktree checkout `e76adc7`，Manifest 从归档提交以绝对路径只读加载，运行证据写到 worktree 与主仓库之外。该环境已通过 content-and-environment verifier；省略 `--allow-live` 的拒绝性预检没有创建输出目录。真实 Pilot 前仍需独立确认 snapshot、完整 8-run 和 51,815 微美元本地硬上限；Manifest 是审批前置证据，不是 provider 账单上限，也不是 Pilot 结果。最终离线门禁为 `227 passed`、实验 package 分支覆盖率 `92.34%`，全仓回归为 `636 passed`；Ruff、mypy strict、契约快照、预算预检和 canonical content-only verifier 均通过。

## 28. M5.5 收尾：Moonshot/Kimi 供应商迁移

OpenAI Pilot 在读取 key 和产生费用之前终止，旧 Manifest 的冻结事实仍保留，但其 provider、模型和美元预算不能迁移到另一个供应商。当前迁移只替换模型调用与费用契约，不改变 Pilot 的任务、知识、rubric、两组渲染公平性、执行顺序或 8-run 规模。

`FrozenExperimentManifest`、`FreezeCandidateSpec` 与内层 `ExecutionManifest` 升级为 schema `1.1`。`PriceSnapshot` 使用 `currency`、`input_micros_per_unit`、`cached_input_micros_per_unit` 和 `output_micros_per_unit`；`CostBudget` 使用 `currency`、`estimated_cost_micros` 和 `hard_cost_limit_micros`。规范 JSON 不再包含 USD 专用字段，loader 仅为历史调用方保留旧字段名 validation alias。candidate、manifest 与 launcher 都要求 pricing/budget/人工确认的 currency 一致，避免把 `858368` 错当成另一货币。

当前 Kimi profile 是不可分割的冻结输入：

```text
provider/base URL : moonshot / https://api.moonshot.cn/v1
API               : OpenAI-compatible Chat Completions
model             : kimi-k2.6 (mutable provider alias)
thinking          : disabled
stream            : true, include_usage=true
sampling          : temperature=0.6, top_p=0.95, n=1, seed=null
output            : MANUAL json_object / FACTORY strict json_schema
limits            : 1024 output tokens, 60s timeout, 2 attempts, concurrency=1
credential        : MOONSHOT_API_KEY
SDK               : openai==2.46.0, max_retries=0
```

`MoonshotExperimentGateway` 只接受上述 profile。它消费 async stream，拒绝变化的 request ID、非零 choice index、非 `stop` finish reason、意外 `reasoning_content`、缺失 usage、非 JSON object 和本地 Schema 不通过的结果；成功原始证据限制为 1 MiB，错误证据限制为 64 KiB。timeout、429、5xx、4xx、网络与无效响应沿用稳定 gateway error code，executor 继续拥有唯一 attempt/journal 控制权。API key 只作为 `AsyncOpenAI` 构造参数，不进入 request、异常文本或产物。

价格在 2026-07-26 从 [Kimi 开放平台](https://platform.kimi.com/) 核验，单位均为每百万 token：未缓存输入 `¥6.50`、缓存输入 `¥1.10`、输出 `¥27.00`。预算仍按未缓存输入保守计算：

```text
expected = ceil(32,000 × 6.50 / 1,000,000)
         + ceil( 8,192 × 27.00 / 1,000,000)
         = ¥0.429184

hard max = ceil(64,000 × 6.50 / 1,000,000)
         + ceil(16,384 × 27.00 / 1,000,000)
         = ¥0.858368
```

Kimi 文档当前没有为 `kimi-k2.6` 提供带日期的不可变模型 snapshot，因此预检明确要求 `model_is_immutable_snapshot=false`，同时拒绝把该别名伪装成固定 snapshot。源码、请求参数、输入、输出与 usage 仍可由 Manifest 和 journal 复核，但供应商在同一别名下更新模型是无法由本仓库控制的外部复现风险。若未来出现官方不可变版本，必须生成新 candidate 和新 experiment freeze，不能原地修改旧 Manifest。

原 OpenAI production-closure Manifest 已原样归档为 [`freeze-manifest-openai-pre-switch.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-openai-pre-switch.json)。新的 Moonshot canonical Manifest 只能从 clean source commit 生成；生成前 launcher 测试使用明确标记的临时 `v1.1` Manifest，不把测试 fixture 描述为正式冻结。旧 OpenAI 费用批准已经失效，新真实调用必须逐字确认 provider/model/profile、完整 8-run、最多 16 次 attempt 和 `CNY 858368 micros` 硬上限。
