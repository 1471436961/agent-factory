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

执行计划由固定 `randomization_seed` 生成，包含全部 240 个 run 的唯一顺序。计划生成后保存并计算 SHA-256，正式执行不能重排。

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

M5.2-M5.4 以以下目录为实现目标：

```text
experiments/
├── definitions/
│   └── <experiment_id>/
│       ├── experiment.yaml
│       ├── manifest.json
│       ├── execution-plan.json
│       ├── tasks/
│       ├── knowledge/
│       └── rubrics/
├── runs/
│   └── <experiment_id>/
│       └── <run_id>.json
├── reviews/
│   └── <experiment_id>/
└── analysis/
    └── <experiment_id>/
        ├── metrics.csv
        ├── summary.json
        └── report.md
```

规则：

1. 原始 run 使用临时文件写入并原子重命名到目标路径。
2. 目标 `run_id` 已存在时，读取并校验身份和冻结 manifest；相同则跳过，不同则报冲突。
3. 任何重试都追加 attempt 记录，不能覆盖前一次供应商结果。
4. 原始文件只追加新文件，不原地编辑；评分和报告属于派生产物，可由原始数据重建。
5. API key、Authorization header 和其他凭据禁止进入任何产物。

## 8. 重试、恢复与成本停止

- 每个 run 最多 2 次重试，即最多 3 个 attempt；仅网络错误、429 和供应商 5xx 可重试。
- 4xx 输入错误、内容过滤和解析契约失败默认不自动重试。
- 重试使用相同 run ID、输入和生成参数，并记录供应商 request ID、错误类、时间和退避时长。
- 恢复时先校验 manifest 与计划 SHA-256，再跳过已有终态 run；校验失败必须停止。
- 发起每个新 attempt 前，根据已用量和保守 token 上限估算最坏成本；可能突破硬上限时停止，不先请求后补记。
- 正式执行必须使用显式命令开关，例如 `--execute-live`；CI 和默认测试没有该开关。

## 9. 指标与判定

### 9.1 H1 结构一致性

`schema_pass_rate = schema_passed_runs / terminal_runs`

- 主要效应：FACTORY 与 MANUAL 的任务级通过率差。
- 支持：差值至少 `+0.10`。
- 不支持：差值小于 `+0.05`。
- 中间区间：证据不足。

### 9.2 H2 知识遗漏

每个任务的 required facts 在冻结 rubric 中定义：

`omission_rate = 1 - covered_required_facts / required_facts_total`

- 支持：FACTORY 相对遗漏率降低至少 20%。
- 不支持：无下降或反向增加。
- 其余：证据不足。

事实匹配优先使用结构字段、精确值、预注册正则和同义词表。任何运行后新增的同义词只进入敏感性分析，不能改写主要结果。

### 9.3 H4 个性化适应度

`adaptation = satisfied_constraints / personalization_constraints_total`

- 使用配对任务差 `FACTORY - MANUAL`。
- 非劣界值为 `-0.05`；95% 区间下界不低于该值时支持“未明显损害”。
- 点估计或区间明显低于界值时不支持。

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
3. 使用按 task 分层的 10,000 次 bootstrap 报告 95% 区间。
4. 同时报告绝对差、相对差和原始分母，不只给 p 值。
5. 方差比较属于次要分析；若引入 Brown-Forsythe，依赖和实现必须在 M5.5 前冻结。
6. 报告所有失败和缺失，不对失败样本做无说明的 complete-case 删除。
7. 分析实现必须是可测试的 Python 模块；notebook 不能成为唯一计算来源。

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
- 模型服务随时间漂移，固定随机交错顺序只能降低、不能消除该影响。
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

## 13. M5.1 到 M5.2 的接口

M5.1 只冻结上述协议，不新增生产模型、不发起真实请求。M5.2 必须先实现并测试以下最小对象，再讨论执行器：

- `ExperimentDefinition`
- `ExperimentTask`
- `KnowledgeFixture`
- `RubricDefinition`
- `ExecutionPlan` / `ExecutionPlanItem`
- `ExperimentRun` / `RunAttempt`
- `MetricRecord`
- `BuildSession`
- `AuditVerificationRecord`

字段、枚举、序列化和 checksum 规则将在 M5.2 设计评审后落盘；不能把本协议中的名称视为已经存在的代码能力。
