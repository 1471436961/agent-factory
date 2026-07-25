# M5：验证实验与分析

## 1. 阶段状态

- 状态：进行中；M5.1-M5.3 已实现，M5.4 已进入且 M5.4.2 确定性评分器已实现。
- 开始时间：2026-07-25。
- 进入依据：M4 已由项目 owner 验收并封存；退出候选提交 `4a55d73` 的 GitHub Actions CI #25 通过，M4 封存提交 `346e2fd` 的 CI #26 通过。
- 规划依据：项目 owner 已确认 M5 的证据拆分、工作包、已知风险、备选方案和真实模型调用审批边界。
- 当前限制：已实现离线执行器但未接入真实 provider；尚未冻结正式模型、SDK、预算或价格快照，也未执行真实模型调用。

## 2. 阶段目标

M5 不以“证明工厂一定优于手写 Agent”为目标，而是检验以下更窄、可证伪的问题：

1. 在固定 Writer 任务、模型和知识正文下，工厂生产工作流是否提高结构一致性？
2. 同样条件下，工厂生产工作流是否降低知识遗漏？
3. 结构约束是否在可接受范围内保留读者适应能力？
4. 原型复用是否在单一项目 owner 的重复构建案例中呈现构建时间收益？
5. 已实现的工厂审计链是否能完整恢复 Agent 的生产来源？

阶段证据分成三条互不替代的路径：

```text
240 次 Writer 生成实验 ──► H1 结构一致性、H2 知识遗漏、H4 适应性
单操作者构建案例     ──► H3 构建成本，仅作探索性工程证据
确定性审计检查       ──► H5 追溯完整性，不消耗模型生成样本
```

## 3. 范围与边界

### 3.1 当前范围

- 使用 24 个冻结 Writer 任务，覆盖 12 个一致性任务和 12 个适应性任务。
- MANUAL 与 FACTORY 两个条件各重复 5 次，共计划 240 次正式生成。
- 使用虚构产品或 API 的合成知识包，避免模型训练记忆掩盖知识注入效果。
- 两组固定同一模型标识、生成参数、用户任务、输出 token 上限和字节级一致的可见知识正文。
- 保存执行计划、原始请求、原始响应、失败记录、评分记录、审计快照和分析产物。
- 确定性评分作为主要证据；盲化人工评分作为次要证据；LLM-as-Judge 只作为探索性信号。
- 使用代码模块生成统计结果，确保报告可以由原始数据重新计算。

### 3.2 非当前范围

- 不验证 Engineer Agent 的代码正确性、沙箱安全、文件修改或命令执行能力。
- 不比较多个模型供应商，不把单模型结果外推为所有 LLM 的一般规律。
- 不通过本实验验证公网部署、分布式执行、多租户或生产 SLA。
- 不声称 240 次生成能够单独识别 Prompt、JSON Schema、知识绑定或审计中的某一组件因果效应。
- 不将单个操作者的构建时间案例表述为人群层面的生产效率结论。
- 若没有第二位独立人工评分者，不计算 Cohen's kappa，也不声称人工评分具备评审者间信度。

Writer 场景优先于 Engineer 场景。Engineer 会同时引入代码执行、测试环境、依赖版本、文件权限和沙箱能力等当前 Alpha 尚未支持的混杂因素；Writer 可通过合成知识、JSON Schema 和预注册事实表建立更可控的评价边界。

## 4. 待验证命题与证据

| 编号 | 命题 | 证据类型 | 主要指标 | 判定边界 |
| --- | --- | --- | --- | --- |
| H1 | 工厂工作流提高结构一致性 | 主要生成实验 | Schema 通过率 | FACTORY 高至少 10 个百分点为支持；差值小于 5 个百分点为不支持；其余为证据不足 |
| H2 | 工厂工作流减少知识遗漏 | 主要生成实验 | 知识遗漏率 | FACTORY 相对降低至少 20% 为支持；无下降或反向增加为不支持；其余为证据不足 |
| H3 | 原型复用降低后续领域构建成本 | 探索性单操作者案例 | 第 2 个及以后领域 active build time | 报告中位数、IQR 和配对差，不作人群推断 |
| H4 | 结构约束未明显损害适应性 | 主要生成实验 | 个性化适应度 | FACTORY 相对 MANUAL 的劣化不超过 0.05 为非劣；超过 0.05 为不支持 |
| H5 | 工厂提高生产来源可追溯性 | 确定性工程验证 | 审计步骤完整率 | 固定链路必须达到 100%；任一步骤不可恢复即失败 |

H1、H2、H4 比较的是“手写 Agent 工作流”和“工厂 Agent 工作流”两个整体条件。即使结果支持命题，也不能单独归因于工厂控制器、Prompt 模板、输出 Schema 或知识绑定中的某一项。

## 5. 工作包

### M5.1 阶段基线与实验协议

- 建立本阶段文档和 [`实验协议设计说明`](../design/experiment-protocol.md)。
- 将 H1/H2/H4、H3、H5 拆分为三类证据。
- 规定 pilot、预注册冻结、不可覆盖产物、失败留痕和真实调用审批边界。
- 更新 README、项目路线图、架构文档和设计纠偏记录。

退出条件：文档之间不存在范围或状态冲突；尚未执行真实模型调用。

### M5.2 实验领域模型与冻结任务集

- 实现 Pydantic v2 实验模型、稳定序列化和哈希规则。
- 建立 24 个任务、合成知识包、输出 Schema、事实词表和适应性规则。
- 为模型校验、任务完整性和知识字节一致性建立单元测试。

退出条件：离线测试能证明任务、知识和 rubric 满足预注册约束；MANUAL/FACTORY 最终输入渲染与字节级公平性由 M5.3 实现和验证。

### M5.3 执行器与不可变原始产物

- 使用 seed 与 run 坐标的 SHA-256 排序生成 `execution-plan.json`，避免依赖 Python PRNG 实现细节。
- 使用 UUID5 生成不依赖执行顺序的稳定 `run_id`，并将数据集、计划、条件 bundle、生成参数与技术预算绑定到 execution manifest。
- 通过真实 `FactoryController` 生产链渲染 FACTORY 条件；MANUAL 与 FACTORY 共用字节级一致的任务和知识正文，并记录 provider-visible `prompt_hash`。
- 以 write-once 文件分别保存 request、attempt intent、attempt completion 和 terminal run；冲突内容拒绝覆盖。
- 抽象实验 gateway；CI 与 CLI 只使用 fake gateway，当前没有可调用真实 provider 的 CLI 子命令。
- 对 network、429、5xx 和 timeout 最多重试 2 次；内容过滤、4xx 与无效响应不自动重试。

退出条件：离线故障注入证明已完成的结果不会丢失、覆盖或重复计入；未完成 attempt 会恢复为 `RESULT_UNKNOWN_AFTER_INTERRUPTION`，但外部 provider 已接收而本地未落盘时，是否产生第二次计费仍取决于 provider 是否支持幂等键。

### M5.4 评分与分析流水线

- 实现 Schema、事实覆盖、知识遗漏、适应性和确定性质量评分。
- 按 task 聚合重复样本，执行分层 bootstrap，报告效应量、95% 区间和失败请求。
- 生成机器可读结果和 Markdown 报告；notebook 只能作为可选探索界面。

退出条件：固定 fixture 可从原始产物重新生成完全一致的统计结果。

### M5.5 Pilot、校准与正式冻结

- 使用不进入正式数据集的 pilot 任务验证请求格式、输出解析、成本计量和 rubric 可用性。
- 冻结 `experiment.yaml`、任务集、知识包、评分规则、执行计划、源代码 commit、模型标识、SDK 版本、价格快照、请求上限、token 上限和成本上限。
- 生成冻结 manifest 及所有输入文件的 SHA-256。

退出条件：pilot 与正式 run ID 空间隔离；冻结后修改任何输入都必须创建新 experiment ID。项目 owner 明确批准模型、预算和正式执行。

### M5.6 正式执行与盲化评审

- 仅在 M5.5 人工审批后执行 240 次真实模型调用。
- 保留所有成功、失败、超时、重试和供应商 request ID，不删除不利样本。
- 生成不含 condition 的盲化评审包；人工评分与条件映射分离保存。

退出条件：计划中的每个 run 都有终态记录；总调用和成本未突破冻结上限；盲化映射可审计恢复。

### M5.7 复算、报告与阶段封存

- 从冻结 manifest 和原始产物重新执行评分与统计分析。
- 报告支持、不支持和证据不足的命题，并单独讨论失败样本与效度威胁。
- 记录本地和 CI 的离线复算证据，形成阶段结论。

退出条件：新环境可按文档复算结果；报告中的每个数字能追溯到原始 run 或工程验证记录；项目 owner 人工验收。

## 6. 质量与安全门禁

- 默认 `pytest`、CI 和 package smoke 不访问模型供应商。
- 真实 API key 只从环境变量读取，不写入配置、日志、fixture 或产物。
- 正式执行必须使用显式 live 开关、有限并发、单请求 timeout、最大重试和成本停止条件。
- 原始 run 文件写入后不可修改；派生评分与报告必须写入独立目录。
- 同一 `run_id` 已存在时只能校验并跳过，不能覆盖。
- 供应商失败、内容过滤、空响应和解析失败都作为结果的一部分统计。
- 任何预注册输入在正式运行后发生变化，都必须产生新 experiment ID，不能就地改写历史实验。

## 7. 已知风险与备选方案

| 风险 | 影响 | 当前控制 | 备选方案 |
| --- | --- | --- | --- |
| 模型版本漂移 | 不同日期输出不可直接比较 | 尽量使用可固定 snapshot 的模型并记录执行时间 | 无 snapshot 时缩短执行窗口并明确版本限制 |
| 供应商限流或网络中断 | 计划不完整、时间分布偏移 | 固定顺序、有限重试、失败留痕、断点恢复 | 暂停并在同一冻结配置下续跑，不重新随机化 |
| 合成知识过于简单 | 低估真实领域难度 | 多个虚构领域、事实密度和干扰项分层 | 后续追加独立外部效度实验，不修改本次数据 |
| MANUAL 基线过弱或过强 | 组间差异失去解释性 | pilot 只校准格式与公平性，不按结果调优正式任务 | 公开两组完整提示与渲染输入供复核 |
| 单一模型与单一场景 | 外部效度有限 | 结论限定到冻结模型和 Writer 任务集 | 后续跨模型复制实验使用新 experiment ID |
| 单操作者 H3 | 无法估计人群差异 | 降级为探索性案例 | 有多位参与者后再设计独立构建效率实验 |
| 人工评分资源不足 | 无法估计评审者间信度 | 人工评分仅作为次要证据 | 获得第二评分者后预注册复评比例和一致性指标 |

## 8. 阶段验收清单

- [x] M5 范围、证据拆分和工作包经项目 owner 确认。
- [x] M5.1 阶段文档与实验协议建立。
- [x] M5.2 实验模型、24 个任务和冻结 rubric 通过离线测试。
- [x] M5.3 执行计划、条件公平性、不可变产物和恢复机制通过离线故障测试。
- [ ] M5.4 评分与分析可由 fixture 完整复算。
- [ ] M5.5 pilot 完成，正式配置、预算和 manifest 经人工冻结。
- [ ] M5.6 正式调用经人工批准并完整执行。
- [ ] M5.7 报告、原始数据和复算证据完成。
- [ ] 项目 owner 确认结束并封存 M5。

## 9. 当前结论

M5.1-M5.3 已建立实验设计基线、严格契约、冻结 Writer fixture 和可恢复的离线执行基础设施；M5.4.1-M5.4.2 已固定评分证据并实现离线确定性评分器。仓库尚无正式实验数据，不能声称 H1-H5 获得支持，也不能把 fake gateway 结果当作模型质量证据。下一步是 M5.4.3：实现任务级配对聚合与确定性 bootstrap。

## 10. M5.2 实现证据

M5.2 将实验基础设施放在仓库级 `experiments` package，而不是 `src/agent_factory`。研究与复算代码因此可以复用项目的 `FrozenModel` 和规范化 checksum，但不会进入 Agent Factory 运行时 wheel、Container、REST 或 SDK。

- `experiments/contracts.py` 实现定义、知识、任务、rubric、计划、run/attempt、指标、构建记录和审计验证契约。
- `experiments/loader.py` 使用 `yaml.safe_load()`、UTF-8 与文件大小限制、路径 containment、原始知识字节 SHA-256 和跨文件引用校验。
- `experiments/definitions/writer-v1/` 固定 6 个虚构领域，每个领域包含 2 个一致性任务与 2 个适应性任务，共 24 个任务和 24 份 rubric。
- 每份知识同时包含当前事实和明确标注的 legacy distractor；required fact matcher 必须在知识中有证据，forbidden matcher 必须对应实际 distractor。
- 数据集 checksum 为 `673b6866d58853a5c788ccff5b6acdc6511ee01b1085439d3d1353811dd3d51b`，复制到不同根目录后保持一致。
- Ruff 与 mypy strict 已纳入 `experiments`；CI 增加独立 90% 分支覆盖率门禁。
- `uv build` 成功生成 sdist 与 wheel；wheel 共 95 个条目且不存在 `experiments/` 条目，研究基础设施未进入运行时分发包。

本地定向门禁：`44 passed`，`experiments` 分支覆盖率 92.29%。全量回归为 `453 passed`，生产代码总覆盖率 92%，其中 domain 96%、application 94%；Ruff format、Ruff lint、mypy strict 和既有契约快照检查均通过。反例覆盖知识篡改、非 UTF-8、危险 YAML tag、路径逃逸、缺失文件、无效 JSON Schema、引用错位、未知 fact、matcher 无证据、matcher 超时、场景矩阵错误、失败 run 伪装成功和审计完整率造假。该证据只证明 fixture 和契约结构自洽，不代表任务具有外部效度，也不构成任何模型质量结果。

## 11. M5.3 实现证据

M5.3 在 `experiments` package 中新增计划、渲染、产物、gateway、执行器和离线 CLI，不修改 Agent Factory 的运行时服务边界：

- `experiments/planning.py` 使用 `SHA-256(seed + coordinate)` 排序全部 240 个坐标，使用 UUID5 派生 `run_id`。提交的计划 checksum 为 `81c535b96bcd3b33ea217dd031953a7f7fc6ae586c995172956324b2b7b7996f`。
- `experiments/rendering.py` 将两组共同的任务、读者信息与原始知识字节放入同一 `task_input`；MANUAL 使用人工 prompt 与文本化 Schema，FACTORY 使用真实控制器导出的 `AgentSpec` 与 provider-level Schema。条件 bundle checksum 为 `17781f2fb7d88c4f38edce23580f4eab6b06a4b7e5330b85a20d427fb36b0d76`。
- `experiments/artifacts.py` 使用临时文件、`fsync` 与同文件系统 hard link 发布 write-once 规范化 JSON。相同内容重放是幂等的，不同内容报冲突；路径逃逸和符号链接被拒绝。
- `experiments/executor.py` 在调用 gateway 前持久化 attempt intent，在调用后写 completion，最后写 terminal run。恢复时从 journal 重建保守 token/request 预算，已有终态只校验并跳过。
- `experiments/gateway.py` 定义保留原始成功/错误 payload 的实验专用边界；`FakeExperimentGateway` 是当前唯一落地实现，不访问网络。
- `python -m experiments verify-plan` 离线校验计划；`run-fake` 只做执行器 smoke，不产生正式实验数据。M5.3 仅支持 `concurrency=1`。
- 发行检查生成 95 项 wheel，确认不存在 `experiments/` 条目；隔离 wheel 安装、Uvicorn/SDK、SQLite 重启恢复、extras 与凭据扫描 smoke 全部通过。

定向门禁为 `83 passed`，`experiments` 分支覆盖率 92.84%。全量回归为 `492 passed`，生产代码总覆盖率 92%，其中 domain 96%、application 95%。故障测试覆盖计划篡改、条件知识错位、AgentSpec 来源错位、非法 live gateway、预算提前停止、重试分类、时钟回拨、写入中断、孤立 intent 恢复、终态重放和内容冲突。

当前 execution manifest 只是一份技术执行身份，绑定数据集、计划、条件 bundle、生成参数和 request/token 上限。它不包含 source commit、Python/SDK 版本、provider 模型、价格快照和货币成本，因此不能替代 M5.5 的正式冻结 manifest。另一个不可消除的边界是：若进程在 provider 接收请求后、本地 completion 落盘前崩溃，M5.3 会将该 attempt 标记为结果未知并避免重复计入，但无法在缺少 provider 幂等支持时保证不会发生第二次外部计费。

## 12. M5.4.1 评分契约

- `MetricRecord` 增加 forbidden matcher 总数与违规数；失败 run 仍不得伪造任何确定性分数。
- `SchemaViolation` 只记录实例路径、Schema 路径和 validator，不复制可能包含敏感正文的错误消息。
- `FactCheck` 记录覆盖结果及命中的预注册 matcher 索引；`ForbiddenMatcherCheck` 和 `PersonalizationCheck` 保留逐项布尔证据。
- `RunScoreRecord` 绑定 run checksum、rubric checksum、实验坐标和 scorer version；成功 run 的明细计数必须等于 `MetricRecord`，失败 run 的检查明细必须为空。
- 确定性质量分是 Schema、事实覆盖、禁用信息合规和适用的个性化约束四类分量的等权平均，保留 12 位小数；它只是次要指标，不替代 H1/H2/H4。
- provider 成功但 Schema 不通过仍属于可评分成功 run；provider/timeout/filter/budget 等执行失败则由主要 ITT 分析按最差值映射，不能伪装成 Schema 评分记录。

M5.4.1 定向门禁为 `93 passed`，`experiments` 分支覆盖率 92.75%；全量回归为 `502 passed`。Ruff format、Ruff lint 和 mypy strict 均通过。该证据只证明评分契约能拒绝结构与汇总矛盾，实际 matcher、Schema 执行、bootstrap 和命题结论尚不存在。

## 13. M5.4.2 确定性评分器

- `experiments/matching.py` 成为 loader 与评分器唯一 matcher 实现，保持 exact 子字符串、大小写选项、regex flags 和 100ms timeout 一致。
- `experiments/scoring.py` 校验 run 与冻结 task/rubric/knowledge 的来源关系，并为成功 run 计算 Schema、事实、legacy forbidden 和个性化逐项证据。
- 结构化输出递归提取字符串、数字、布尔和 null 值；对象按 key 排序，数组保留顺序。matcher 不检查字段名，避免字段名造成事实假命中。
- personalization 有 `target_field` 时只检查目标字段；无目标字段时检查完整输出。regex timeout 转换为 `ScoringError`，不静默记为未命中。
- provider 成功但 Schema 不通过仍保留评分；执行失败生成无检查明细的 `RunScoreRecord`，由后续 ITT 聚合映射为最差结果。

M5.4.2 定向 matcher/loader/scoring 测试为 `38 passed`；完整实验门禁为 `108 passed`，`experiments` 分支覆盖率 93.03%，其中 `scoring.py` 为 96%；全量回归为 `517 passed`。统计聚合与命题判定尚未实现，因此这些分数不能形成 H1/H2/H4 结论。
