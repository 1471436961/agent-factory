# M5：验证实验与分析

## 1. 阶段状态

- 状态：进行中；M5.1-M5.4 已实现，M5.5 已执行两次经批准的 Moonshot 8-run Pilot；MFJS Schema 适配和失败 usage 计量修正已通过本地门禁，等待提交与 clean-commit 重新冻结。
- 开始时间：2026-07-25。
- 进入依据：M4 已由项目 owner 验收并封存；退出候选提交 `4a55d73` 的 GitHub Actions CI #25 通过，M4 封存提交 `346e2fd` 的 CI #26 通过。
- 规划依据：项目 owner 已确认 M5 的证据拆分、工作包、已知风险、备选方案和真实模型调用审批边界。
- 当前限制：绑定 source commit `889807a15b3d1cff9fe5df51f077de2110f6464a` 的 Moonshot `v1.1` Manifest 已解释两次真实执行，但修正会改变 FACTORY 请求字节，故该文件不能授权第三次调用。当前没有新的 live 费用批准；M5.6 也未获授权。

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
- 冻结 `dataset.yaml`、任务集、知识包、评分规则、执行计划、源代码 commit、模型标识、SDK 版本、价格快照、请求上限、token 上限和成本上限。
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
- [x] M5.4 评分与分析可由 fixture 完整复算。
- [x] M5.5 冻结契约、独立 Pilot、Moonshot gateway、受控 launcher 与执行前生产依赖闭环完成。
- [x] 两次真实 Pilot 的成功、失败、request、attempt、terminal 和成本证据均保留并完成复核。
- [ ] MFJS 适配与失败 usage 修正通过门禁，在 clean commit 上重新冻结并完成修正后 Pilot review。
- [ ] M5.5 正式配置、预算和 manifest 经项目 owner 人工冻结与批准。
- [ ] M5.6 正式调用经人工批准并完整执行。
- [ ] M5.7 报告、原始数据和复算证据完成。
- [ ] 项目 owner 确认结束并封存 M5。

## 9. 当前结论

M5.1-M5.4 已建立从冻结 Writer fixture、可恢复执行到离线评分和报告复算的完整工程链。M5.5 已获得真实 Moonshot Pilot 数据，但它只形成兼容性证据：第一次 8-run 因凭据设置失败；第二次 MANUAL 4/4 成功、FACTORY 4/4 因 Structured Output 与 MFJS 子集不兼容而失败。该结果不能用于 H1-H5，不能进入正式统计，也不能解释为框架质量劣于或优于 MANUAL。下一步是完成修正、离线门禁、clean-commit 重新冻结和新的 owner 审批；M5.6 仍保持阻断。

## 10. M5.2 实现证据

M5.2 将实验基础设施放在仓库级 `experiments` package，而不是 `src/agent_factory`。研究与复算代码因此可以复用项目的 `FrozenModel` 和规范化 checksum，但不会进入 Agent Factory 运行时 wheel、Container、REST 或 SDK。

- `experiments/contracts.py` 实现定义、知识、任务、rubric、计划、run/attempt、指标、构建记录和审计验证契约。
- `experiments/loader.py` 使用 `yaml.safe_load()`、UTF-8 与文件大小限制、路径 containment、原始知识字节 SHA-256 和跨文件引用校验。
- `experiments/definitions/writer-v1/` 固定 6 个虚构领域，每个领域包含 2 个一致性任务与 2 个适应性任务，共 24 个任务和 24 份 rubric。
- 每份知识同时包含当前事实和明确标注的 legacy distractor；required fact matcher 必须在知识中有证据，forbidden matcher 必须对应实际 distractor。
- M5.2 实现时数据集 checksum 为 `673b6866d58853a5c788ccff5b6acdc6511ee01b1085439d3d1353811dd3d51b`；M5.5.3 将场景矩阵密度纳入定义契约后，当前 checksum 更新为 `e8305386e305e39623ab1e852059148ed319ae63fc180a58288f1ac0a3e14a8e`，复制到不同根目录后保持一致。
- Ruff 与 mypy strict 已纳入 `experiments`；CI 增加独立 90% 分支覆盖率门禁。
- `uv build` 成功生成 sdist 与 wheel；wheel 共 95 个条目且不存在 `experiments/` 条目，研究基础设施未进入运行时分发包。

本地定向门禁：`44 passed`，`experiments` 分支覆盖率 92.29%。全量回归为 `453 passed`，生产代码总覆盖率 92%，其中 domain 96%、application 94%；Ruff format、Ruff lint、mypy strict 和既有契约快照检查均通过。反例覆盖知识篡改、非 UTF-8、危险 YAML tag、路径逃逸、缺失文件、无效 JSON Schema、引用错位、未知 fact、matcher 无证据、matcher 超时、场景矩阵错误、失败 run 伪装成功和审计完整率造假。该证据只证明 fixture 和契约结构自洽，不代表任务具有外部效度，也不构成任何模型质量结果。

## 11. M5.3 实现证据

M5.3 在 `experiments` package 中新增计划、渲染、产物、gateway、执行器和离线 CLI，不修改 Agent Factory 的运行时服务边界：

- `experiments/planning.py` 使用 `SHA-256(seed + coordinate)` 排序全部 240 个坐标，使用 UUID5 派生 `run_id`。M5.3 实现时计划 checksum 为 `81c535b96bcd3b33ea217dd031953a7f7fc6ae586c995172956324b2b7b7996f`；M5.5.3 因 definition checksum 更新而重新生成的当前 checksum 为 `8e8ad93a8cb1b3207580c89917e4af9a6ac0c32c6ab47d83e04c6f04b233e920`，240 个 run 坐标未改变。
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

## 14. M5.4.3 任务级统计分析

- `AnalysisConfig` 固定 analyzer version、bootstrap seed、10,000 次重复、95% 置信水平和 H2 至少 95% 有效相对 bootstrap 样本的要求。
- `ExperimentAnalyzer` 在计算前要求 240 个计划项各有且只有一个 `RunScoreRecord`，逐项核对 experiment、plan、condition、task、repetition、execution order、scenario、rubric checksum 和 rubric 明细。
- 每个 `task_id + condition` 先聚合 5 次重复。主要 intention-to-treat 分析保留全部执行失败：H1 按 Schema 未通过、H2 按 required facts 全遗漏、H4 按个性化约束全未满足计入；`succeeded-only` 只作为不产生正式判定的敏感性分析。
- H1/H2 在 consistency 与 adaptation strata 内分别重采样，H4 只在 12 个 adaptation task 内重采样。每次仍抽取原 strata 数量的 task，5 次重复始终嵌套在 task 聚合中，不被当成独立样本。
- Bootstrap 索引由 SHA-256 和 rejection sampling 确定性派生，不使用 Python PRNG；95% 区间显式使用 Type-7 分位数。相同评分集合即使输入顺序不同，也产生相同 `score_set_checksum` 和 `AnalysisSummary`。
- H2 的相对遗漏降低计算为 `(mean_manual_omission - mean_factory_omission) / mean_manual_omission`；每个 bootstrap 样本的 MANUAL 分母为零时该样本无效。有效样本不足 95% 时正式结论为“证据不足”，但绝对遗漏率差及其区间仍保留。
- `HypothesisResult` 只允许 ITT 返回“支持”“不支持”“证据不足”；成功样本敏感性固定为 `not-evaluated`，避免次要分析替代主要结论。

M5.4.3 定向分析测试为 `16 passed`；完整实验门禁为 `124 passed`，`experiments` 分支覆盖率 93%，其中 `analysis.py` 为 99%；全量回归为 `533 passed`。Ruff format、Ruff lint 与全量 mypy strict 均通过。测试使用完整 240-run 合成评分集，只证明统计实现、失败映射和契约防线可重复，不构成任何正式 H1/H2/H4 实验结论。

## 15. M5.4.4 可验证报告产物

- `AnalysisArtifactManifest` 固定 `summary.json`、`metrics.csv`、`report.md` 的规范顺序、媒体类型、SHA-256 和字节数；manifest 自身是 package 完整发布标志。
- `AnalysisReportPublisher` 使用 `sha256_model(AnalysisSummary)` 形成 content-addressed 路径 `analysis/<experiment_id>/<analysis_checksum>/`。不同 score set 或 analysis config 不会争用同一目录。
- 发布器先在内存中确定全部字节，再通过既有 `ArtifactStore` 依次发布 JSON、CSV、Markdown，最后发布 `artifact-manifest.json`。manifest 前中断会留下可恢复的未提交 package；重试只补齐缺失文件。
- `summary.json` 是机器事实源。CSV 使用固定 17 列和稳定的 population/task/condition 顺序；Markdown 只展示身份、执行完整性、ITT 结果、成功样本敏感性与复算边界，不保存原始响应、Prompt 正文、凭据或人工评分。
- verifier 同时检查 manifest 路径身份、每个文件的长度和 SHA-256、summary 的 analysis checksum，并从 summary 重渲染 CSV 与 Markdown 逐字节比较。即使展示文件和 manifest 被同步改写，派生内容不一致仍会被发现。
- 多文件发布不是单次文件系统事务，完整性依赖 manifest-last 协议。本地 checksum 不能防止管理员同时改写 summary、展示文件与 manifest；正式归档仍需要外部 checksum 清单、只读权限或对象锁。

M5.4.4 定向报告测试为 `11 passed`；完整实验门禁为 `135 passed`，`experiments` 分支覆盖率 93%，其中 `reporting.py` 为 100%；全量回归为 `544 passed`。Ruff format、Ruff lint 与全量 mypy strict 均通过。测试覆盖幂等重放、同身份冲突、manifest-last 中断恢复、digest 篡改、manifest 身份错位、summary 身份错位、同步篡改后的重新渲染校验和 24 task / 96 aggregate 输出规模。这些产物均由合成 `AnalysisSummary` 生成，不是正式实验报告。

## 16. M5.4.5 一键离线复算

- `ExperimentEvidenceLoader` 按冻结计划读取 `execution-manifest.json`、240 个 request、完整 attempt intent/completion journal 和 terminal run，并交叉校验 execution identity、generation、Prompt/知识来源、token reservation、重试顺序和时间关系。它不调用 `ExperimentExecutor`，因此不会补写中断结果或触发 provider。
- `ArtifactStore.list_files()` 对实验目录执行最多 10,000 个普通文件的稳定枚举，拒绝符号链接；加载器据此拒绝缺失和计划外产物。该检查防止孤立或额外 terminal 文件被静默忽略，但不抵御拥有本地管理权限的同步篡改。
- 每个 `RunScoreRecord` 先发布到 `scores/<experiment_id>/<execution_manifest_checksum>/records/`，`score-manifest.json` 最后发布并绑定 dataset、plan、execution manifest、逐 run checksum、逐 score checksum 和规范 `score_set_checksum`。中断时不存在 commit marker，使用相同输入可幂等补齐。
- `OfflineAnalysisPipeline` 从磁盘重新读取并校验已发布评分，再执行 `ExperimentAnalyzer` 和 `AnalysisReportPublisher`；`AnalysisSummary.score_set_checksum` 必须与评分 Manifest 一致。CLI `python -m experiments analyze` 不接受 provider、API key 或 live 开关，只能执行离线派生计算。
- 完整 fake journal 测试覆盖 240 个 terminal run、240 条评分、96 个 aggregate、6 个 hypothesis、Manifest 中断恢复、逐字节重放、journal/score 篡改和来源变化冲突。fake 输入只证明工程复算链路，不构成 H1-H5 的模型质量证据。

M5.4.5 的 CI 同构 experiment 门禁为 `149 passed`，`experiments` 分支覆盖率 92.99%，其中 `score_artifacts.py` 为 98%；全仓回归为 `558 passed`。Ruff format、Ruff lint、全量 mypy strict 和契约快照检查均通过。测试执行没有网络请求或真实模型调用。

## 17. M5.5.1 冻结与成本契约

- `ExperimentPurpose` 将 `pilot` 和 `formal` 固定为不同证据用途。`FrozenExperimentManifest` 在 formal 模式下强制引用 `PilotEvidenceRef`，且 Pilot experiment ID 不得与正式 experiment ID 相同；Pilot Manifest 自身不得反向声明 Pilot 证据。
- `SourceSnapshot` 记录 40-64 位 Git commit、干净工作树声明、CPython 精确版本和 `uv.lock` checksum；`FrozenArtifact` 使用排序、去重的相对路径、字节数和 SHA-256 清单绑定冻结输入。
- `ProviderSnapshot` 记录 provider、模型、API、官方 SDK 版本及模型是否为不可变 snapshot；`PriceSnapshot` 记录 USD、每百万 token 的输入/缓存输入/输出微美元整数单价、HTTPS 来源和采集时间。
- `CostBudget` 的金额字段使用 strict integer 微美元，拒绝 float。`calculate_conservative_cost_usd_micros()` 对输入与输出分量分别向上取整；Manifest 重算估算成本，并要求估算 usage 不超过 technical limits、硬成本上限不低于估算且不高于 token 上限对应的保守成本 ceiling。
- Manifest 内联 technical `ExecutionManifest` 和 `AnalysisConfig`，交叉校验 experiment、provider、model、SDK、价格和 analysis config checksum。`manifest_checksum` 的实际计算、文件读取和 Git 状态验证属于 M5.5.2，本工作包只固定模型结构与跨字段不变量。

M5.5.1 没有选择真实模型或价格，没有读取 API key，也没有网络或模型调用。单元测试中的 Provider、模型、价格 URL 和 checksum 均为合成值，不构成冻结候选。

M5.5.1 的 CI 同构 experiment 门禁为 `158 passed`，`experiments` 分支覆盖率 93.30%，其中扩展后的 `contracts.py` 为 92%；全仓回归为 `567 passed`。Ruff format、Ruff lint 和全量 mypy strict 均通过。

## 18. M5.5.2 冻结候选生成与离线验证

- `FreezeCandidateSpec` 仅保存人工评审输入；source commit、CPython/SDK 版本、文件 checksum、analysis checksum 和 Manifest checksum 均由机器派生。
- `FreezeCandidateBuilder` 强制绑定 dataset、知识声明与正文、task、rubric、MANUAL condition、execution plan、candidate spec 和 `uv.lock`。清单显式排序、去重，可增加文件但不能漏掉必需输入。
- `SubprocessGitSnapshotReader` 使用无 shell 的只读 Git 命令采集仓库根、HEAD 和 porcelain status；文件读取前后快照必须相同且干净，避免 check-then-read 竞态。
- 文件解析执行路径 containment、逐级符号链接拒绝、2 MiB 单文件与 32 MiB 总量上限；候选以 canonical JSON write-once 发布到仓库外或 `.tmp/`。
- verifier 将内容证据与当前环境证据分层：两者都验证 Manifest 自 checksum、dataset/plan/execution identity 和文件字节；默认模式额外要求当前 Git commit、干净工作树、CPython 和 SDK 与冻结值一致，`--content-only` 不声称执行环境已就绪。
- CLI 新增 `freeze-candidate` 和 `verify-freeze`，均没有 API key、provider 调用、live switch 或网络配置。

M5.5.2 没有选择真实模型或官方价格，没有读取 API key、访问价格 URL 或执行模型调用。测试使用合成 Provider、模型、价格和 fake Git/environment reader，因此只证明冻结机制的结构和失败防线，不构成 Pilot 或正式冻结证据。候选 checksum 也不是数字签名，正式证据仍需 owner 审批与外部只读归档。

M5.5.2 的 CI 同构 experiment 门禁为 `173 passed`，`experiments` 分支覆盖率 92.59%，其中 `freezing.py` 为 90%、`cli.py` 为 98%；全仓回归为 `582 passed`。Ruff format、Ruff lint、全量 mypy strict 和既有契约快照检查均通过。

## 19. M5.5.3 Pilot 配置与离线预检

- `ExperimentDefinition.tasks_per_scenario_per_domain` 将每领域的场景密度从 loader 常量提升为冻结定义字段；`expected_task_count` 必须等于领域数、两种场景和该密度的乘积。正式 Writer 保持每领域 2+2，Pilot 使用每领域 1+1。
- `experiments/definitions/writer-pilot-v1/` 使用 Aerilon Routing 与 Brivane Storage 两个独立合成领域，共 4 个任务、4 份 rubric、1 次重复和 8 个 MANUAL/FACTORY run。Pilot 数据集 checksum 为 `4651ae511935d2c9e1312b67fcb568669e4ea993f37059939249e9e83255d9aa`，计划 checksum 为 `ed7a237fd48280e6fcefda742a6336f70cf58170362800e5897ab7db43eb480d`。
- `validate_pilot_preflight()` 交叉验证 Pilot 与 formal 的 experiment、domain、task、rubric、knowledge 和 run 身份没有交集，并拒绝非单次重复、非 1+1 矩阵、非固定模型 snapshot、并发大于 1 或预算等式漂移。
- 经项目 owner 确认的离线候选使用 OpenAI Responses API、固定快照 `gpt-4.1-mini-2025-04-14` 和 SDK `2.46.0`。2026-07-26 从 OpenAI 官方模型页面核验的每百万 token 单价为输入 `$0.40`、缓存输入 `$0.10`、输出 `$1.60`；预算不假设缓存命中。
- 8 个 run 按一次 attempt 估算 8 次请求、32,000 输入 token、8,192 输出 token 和 `$0.025908`；最多 2 次 attempt 对应 16 次请求、64,000 输入 token、16,384 输出 token 和 `$0.051815` 硬上限。`verify-pilot` 只执行离线身份与预算预检。
- 候选 inventory 显式绑定 Pilot/Formal fixture、全部 `experiments/*.py`、`pyproject.toml` 与 `uv.lock`。当前 tracked spec 不是 `FrozenExperimentManifest`：clean source commit、逐文件 checksum 和最终 manifest checksum 必须在本工作包提交后由 M5.5.2 builder 派生。

M5.5.3 没有读取 API key、实例化 live gateway 或调用模型。定向测试覆盖独立 fixture、正式/Pilot 身份重叠、预算漂移、真实候选 spec 的 fake Git/environment 冻结构建与 CLI 预检；这些证据只证明配置自洽，不证明 API 请求已经兼容，也不构成 Pilot 结果。CI 同构 experiment 门禁为 `181 passed`，`experiments` 分支覆盖率 92.69%，其中 `pilot.py` 为 96%、`freezing.py` 为 90%；全仓回归为 `590 passed`。Ruff format、Ruff lint、全量 mypy strict 和契约快照检查均通过。真实调用必须在 live gateway 完成 mock 契约测试后由项目 owner 单独批准。

## 20. M5.5.4 OpenAI 实验 gateway 与离线契约

- `GatewayRequest.expected_output_schema` 由 executor 从冻结任务表注入，只用于本地共同验证，不进入 provider-visible invocation 或 `prompt_hash`。MANUAL 映射为 `json_object`，FACTORY 映射为 strict `json_schema`；两组输出最终都按同一个 Draft 2020-12 Schema 校验。
- `OpenAIExperimentGateway` 使用官方 Responses API，发送冻结 model、instructions、task input、temperature、max output tokens 和 timeout，并设置 `store=False`。SDK 内建重试固定为零，避免绕过 executor 的 journal、attempt 和预算控制。
- 成功结果保留 provider request ID、原始响应、输出正文、结构化对象和输入/输出 token；响应与错误 body 分别限制为 1 MiB 和 64 KiB。异常正文不进入产物，timeout、429、5xx、4xx、网络、过滤和无效响应使用稳定 error code 分类。
- gateway 的 `is_live=True` 继续受 executor `allow_live=False` 默认防线约束。M5.5.4 当时尚无 live CLI，也不从环境变量隐式读取 API key；构造函数只接受调用方显式提供的 key，且 gateway 对象不保存该字符串。
- Pilot freeze candidate inventory 新增 `experiments/openai_gateway.py`，共 61 项显式输入。该 tracked candidate 仍不是 clean-commit `FrozenExperimentManifest`。

M5.5.4 的 31 项定向测试全部使用 fake client，并额外检查本地锁定 OpenAI SDK `2.46.0` 的 Responses 参数签名，不访问网络。CI 同构 experiment 门禁为 `211 passed`，总分支覆盖率 `93.04%`，其中 `openai_gateway.py` 为 `97%`；全仓回归为 `621 passed`。Ruff format、Ruff lint、全量 mypy strict 和契约快照检查通过。mock 证据不能证明真实账户权限、模型 snapshot 可用性、provider 限流或账单行为。真实 Pilot 仍须先生成 clean-commit freeze manifest，再由项目 owner 单独批准。

## 21. M5.5.5 Pilot freeze manifest 生成与归档

- source commit 固定为 `5a5d58cb42b62e3d2e10a060fea72d4ae0a97498`。生成前后 Git porcelain 均为空，Manifest 记录 `working_tree_clean=true`。
- 运行环境固定为 CPython `3.11.15`、OpenAI SDK `2.46.0`；`uv.lock` checksum 为 `2abf80af28081e5fabd22f3bc44df6a867a1d2e56ed598eac34325ef4dd83828`。
- Manifest 绑定 61 项文件、8 个预期请求、最多 16 次请求和 51,815 微美元硬上限。内部 Manifest checksum 为 `2673435ce2623c7c5bfaeb4a011c72f0558ef557c3506bba6685d114357bb6af`。
- 默认 `content-and-environment` 与 `--content-only` 验证均在干净 source commit 上通过。验证后的原始字节现保留为 [`freeze-manifest-m5.5.5.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.5.json)，文件 SHA-256 为 `a3216e6b292126c5041ab701c1864c53e56ba15faac3d33ecd55c69d3a59d7b2`。
- 归档提交位于 source commit 之后，因此日常 CI 只做 content-only 验证；需要再次声明环境一致时必须 checkout source commit。该关系由文档显式保留，不通过修改 Manifest 伪装成同一提交。

M5.5.5 未读取 API key、未实例化真实 client、未发起 provider 请求，也未产生费用。CI 同构 experiment 门禁为 `212 passed`，总分支覆盖率 `93.04%`；全仓回归为 `622 passed`。Ruff format、Ruff lint、全量 mypy strict、契约快照、Pilot 预算预检和归档 Manifest content-only 验证均通过。Manifest checksum 和 Git 历史能够发现普通漂移，但不是数字签名，不能抵御具有仓库写权限者同步改写全部证据。最终审计同时确认 source commit 尚无受控 live CLI；本 Manifest 不得用于真实执行授权，launcher 实现后必须重新冻结并再次提交 owner 评审。

## 22. M5.5.6 受控 Pilot launcher

- `run-pilot-live` 固定执行完整 Pilot plan，不提供 `--max-items` 或 API key 参数。命令必须同时提供 Freeze Manifest、独立输出目录、`--allow-live`、完全匹配的 experiment ID 和 51,815 微美元硬上限确认。
- 启动顺序固定为 dataset/plan 加载、完整 Manifest 环境验证、Pilot/Formal 隔离与预算预检、双重人工确认、输出路径检查、真实 `FactoryController` 准备与全部条件配对校验，最后才读取进程环境中的 `OPENAI_API_KEY` 并创建 client。
- 每个 Pilot domain 通过原型注册、知识注册、克隆、绑定和 `AgentSpec` 导出形成一个 revision 2 工厂实例。两个 AgentSpec 与 12 条审计事件写入带 checksum 的 write-once preparation；恢复时复用该证据，拒绝身份、来源或审计链漂移。
- factory preparation 位于 `_factory-preparation/<experiment_id>/`，不混入 executor 的 `<experiment_id>/requests|attempts|terminal` 证据树。仓库内输出只允许位于 `.tmp/`，也可使用仓库外目录；现存符号链接路径被拒绝。
- OpenAI SDK 仍禁用隐藏重试。启动器向 executor 显式传入 `allow_live=True`，但只有全部前置门禁通过后才可到达；成功、异常和中断路径统一异步关闭 SDK client。终端汇总实际可观测 token、保守实际费用、attempt 数及冻结硬上限，失败样本和未知结果不删除。
- M5.5.5 的 61 项 Manifest 继续作为历史执行前证据，其 source commit 不包含 launcher。tracked candidate 已将 `experiments/pilot_launcher.py` 纳入新 inventory；M5.5.6 提交后必须在 clean HEAD 重新生成最终 Manifest，旧归档不能用于费用授权。

本工作包没有读取真实 API key、没有网络或模型调用，也没有产生费用。所有 live 路径测试均使用本地 fake client；CI 同构 experiment 门禁为 `225 passed`，总分支覆盖率 `92.34%`，`pilot_launcher.py` 为 `87%`；全仓回归为 `634 passed`，全量 mypy strict 检查 `193` 个源文件通过，Ruff 与契约快照门禁保持通过。

## 23. M5.5.7 最终 Pilot Manifest 生成与归档

- 最终 freeze 在干净 source commit `d3c19beb75587b5cc9963c05832c918694dfa9e1` 上生成。生成前 Git porcelain 为空，Manifest 记录 CPython `3.11.15`、OpenAI SDK `2.46.0` 和 `uv.lock` checksum `2abf80af28081e5fabd22f3bc44df6a867a1d2e56ed598eac34325ef4dd83828`。
- 62 项 inventory 包含 `experiments/pilot_launcher.py`、OpenAI gateway、executor、冻结与评分源码、Pilot/Formal fixture、`pyproject.toml` 和 `uv.lock`。预算仍固定为 8 次预期请求、最多 16 次请求及 51,815 微美元硬上限。
- Manifest 先写入 Git 忽略的 `.tmp/m5.5.7-freeze/`。在任何 tracked 文件变化前，`content-and-environment` 与 `--content-only` 验证均通过；内部 Manifest checksum 为 `6514a01799af9b6585f4ff009ad11c887439a324200771d0cae479f28f630d22`，原始 JSON 文件 SHA-256 为 `994f0d46557adeea77703849b0eb3978abe3d9fe89a1741c01b802ffcd2d2740`。
- M5.5.5 原始字节移至 [`freeze-manifest-m5.5.5.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.5.json)；M5.5.7 原始字节现保存在 [`freeze-manifest-m5.5.7.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-m5.5.7.json)。两份历史文件分别由回归测试固定，没有被重写成新身份。
- Manifest 不能把归档后的自身提交纳入 inventory，因此归档提交的 HEAD 必然晚于 source commit。日常 CI 对 canonical 文件执行 content-only 验证；需要再次声明环境级一致性时必须 checkout `d3c19be`。

M5.5.7 没有读取 API key、创建 provider client、调用模型或产生费用。该 Manifest 冻结了 launcher 与实验输入，但收尾审计确认其 62 项 inventory 没有包含 `FactoryController` 实际执行所需的任何 `src/agent_factory` 文件，因此不能作为真实费用授权依据。CI 同构 experiment 门禁当时为 `226 passed`，总分支覆盖率 `92.34%`；全仓回归为 `635 passed`。

## 24. M5.5 收尾修正：生产依赖冻结闭环

- 项目 owner 确认不新增 M5.5.8-M5.5.11；生产源码补冻、真实 Pilot、评审和正式冻结继续作为原 M5.5 的退出任务。
- Pilot candidate 从 62 项扩展为 153 项，新增 `src/agent_factory` 下全部 85 个 Python 文件和 6 个 migration SQL。测试按当前文件系统重新枚举这 91 项生产输入，任何新增文件遗漏都会阻断门禁。
- 新 Manifest 在 clean source commit `e76adc778300b73b5973920fbaaa72275501db8d` 上生成，记录 CPython `3.11.15`、OpenAI SDK `2.46.0`、同一 lockfile、8/16 次请求边界和 51,815 微美元硬上限。
- 生成文件先写入 `.tmp/m5.5-closure-freeze/`，在 tracked 工作树仍为空时通过 `content-and-environment` 与 `--content-only` 双重验证。内部 Manifest checksum 为 `58afac123924e0604ec4067f0492781e7115a97b6c14900aee5bcff8fcd05713`，原始文件 SHA-256 为 `9758d465b44663baf18ced7f06ef51292d57037e1840c7dc17ba63fb94a1cecf`。
- 当时的 OpenAI canonical Manifest 指向新的 153 项证据；供应商切换后其原字节保留为 [`freeze-manifest-openai-pre-switch.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-openai-pre-switch.json)，M5.5.5 与 M5.5.7 文件继续保留为历史检查点。归档提交晚于 source commit，环境级复核必须 checkout `e76adc7`。

本次收尾修正没有读取 API key、创建 provider client、调用模型或产生费用。E 盘 detached worktree 固定在 `e76adc7`，按 `uv.lock` 创建 CPython `3.11.15` / OpenAI SDK `2.46.0` 隔离环境后，canonical Manifest 的环境级验证通过；不带 `--allow-live` 的启动预检在创建 `E:\Agent-Factory-Pilot-Evidence` 前按设计拒绝。Manifest 闭环只使项目具备申请真实 Pilot 审批的工程前置条件，M5.5 仍需完成 8-run、Pilot review、正式 Manifest 与 owner 审批后才能退出。CI 同构 experiment 门禁为 `227 passed`，总分支覆盖率 `92.34%`；全仓回归为 `636 passed`。Ruff format、Ruff lint、全量 mypy strict（`193` 个源文件）、契约快照、Pilot 预算预检和 canonical content-only verifier 均通过。

## 25. M5.5 收尾修正：Pilot 供应商切换

- 项目 owner 在真实调用前确认 OpenAI API 与 ChatGPT Plus 是独立产品，现有付款和地区条件无法形成可执行的 OpenAI API 路径，因此原 OpenAI 费用批准失效；该变更不修改 Writer 任务、MANUAL/FACTORY 对照条件或 8-run Pilot 规模。
- 当前 provider 固定为中国区 Moonshot API `https://api.moonshot.cn/v1`，模型为 `kimi-k2.6`，使用 Kimi 文档支持的 OpenAI-compatible Chat Completions。请求固定为非思考模式、`stream=true`、`stream_options.include_usage=true`、`temperature=0.6`、`top_p=0.95`、`n=1`、`max_output_tokens=1024`、60 秒 timeout、最多 2 次 attempt 和单并发。
- MANUAL 条件使用 `response_format={"type":"json_object"}`；FACTORY 条件使用 strict JSON Schema。流式 chunk 在本地聚合后仍执行同一 Draft 2020-12 Schema 校验，记录 request ID、usage 和有界响应证据；SDK 隐藏重试为零，key 只从 `MOONSHOT_API_KEY` 读取。
- 冻结契约从 `1.0` 升级为 `1.1`。`PriceSnapshot` 与 `CostBudget` 显式携带 `CNY`，规范字段改为货币中立的 `*_micros`；旧 `*_usd_micros` 只保留为读取兼容别名。CLI 审批同时匹配 currency 和 hard-cost micros，不能只确认一个裸金额。
- 2026-07-26 按 Kimi 开放平台公开价格记录：每百万 token 未缓存输入 `¥6.50`、缓存输入 `¥1.10`、输出 `¥27.00`。8 次预期请求、32,000 输入 token 和 8,192 输出 token 的估算为 `¥0.429184`；16 次最大 attempt、64,000 输入 token 和 16,384 输出 token 的本地硬上限为 `¥0.858368`。
- Kimi 官方当前只提供 `kimi-k2.6` 别名，未提供带日期的不可变 snapshot。因此 candidate 必须声明 `model_is_immutable_snapshot=false`；这降低 provider 级复现强度，不能通过伪造 snapshot 标志掩盖。每次后续真实执行仍保存供应商 request ID、原始响应、usage、时间和本地请求 hash。
- OpenAI 生产依赖闭环 Manifest 原始字节保留为 [`freeze-manifest-openai-pre-switch.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-openai-pre-switch.json)，只承担历史解释与回归证据。当时的 Moonshot canonical Manifest 从 clean source commit `889807a15b3d1cff9fe5df51f077de2110f6464a` 生成，绑定 CPython `3.11.15`、OpenAI SDK `2.46.0`、`uv.lock` 与 154 项输入；内部 checksum 为 `edd5cf3f304742398cc9d6ec4fa7be4c6cd14f90769393b589d67616a6eec5ac`，文件 SHA-256 为 `ae4c0727a2082bed55713147b3a28ec96fb4843d12fa96a074bebc03991c5cdd`。两次真实 Pilot 后，该文件降级归档为 [`freeze-manifest-moonshot-pre-mfjs.json`](../../experiments/evidence/writer-pilot-v1/freeze-manifest-moonshot-pre-mfjs.json)，当前 canonical 槽位保持空缺，等待修正后重新冻结。

本次切换阶段只实现货币中立契约、Moonshot gateway、launcher/CLI 映射、离线测试、文档同步和新 Manifest 归档，不读取真实 key、不访问付费接口，也不产生 Pilot 数据。归档前 CI 同构实验门禁为 `251 passed`、分支覆盖率 `91.43%`；加入 canonical Manifest 回归后，全仓基线为 `661 passed`。Ruff、mypy strict（`196` 个源文件）、契约快照和 Pilot 预检均通过。新的 Manifest 与精确人工批准缺一不可；旧的“OpenAI `gpt-4.1-mini-2025-04-14`、最多 `$0.051815`”批准不得迁移解释为 Moonshot 授权。

## 26. M5.5 Moonshot Pilot 执行与纠偏

- 第一次真实执行使用错误的本地凭据设置，8 个 run 均为 `provider-failed`，共 8 个 attempt；证据根 `E:\Agent-Factory-Kimi-Pilot-Evidence` 原样保留。
- 更换有效开放平台凭据并获得第二次明确费用批准后，完整 8-run 于 `2026-07-26T14:18:25.419052Z` 至 `2026-07-26T14:19:19.235537Z` 执行。MANUAL 4/4 `succeeded`，FACTORY 4/4 `invalid-response`，共 8 个 attempt，无重试；证据根为 `E:\Agent-Factory-Kimi-Pilot-Evidence-Retry-20260726`。
- 第二次旧 journal 记录 MANUAL 的 1,416 input + 612 output token，终端汇总 `CNY 25728 micros`。FACTORY 原始失败 chunk 另含 872 input + 49 output，完整可观察成本应为 `CNY 32719 micros`。该值低于 `CNY 858368 micros` 硬上限；调用前预算 reservation 未失效。
- 四个 FACTORY 错误码均为 `MOONSHOT_OUTPUT_NOT_JSON_OBJECT`。对照请求和 Moonshot 官方文档后，根因定位为完整 Draft 2020-12 Schema 超出 MFJS Structured Output 子集；旧 mock 测试只覆盖 SDK 参数形状，没有覆盖供应商真实约束子集。
- 修正实现对 provider schema 做确定性 MFJS projection；本地专属约束转为 description hint，未知结构关键字在调用前拒绝，完整 Draft Schema 继续承担共同本地终验。失败 gateway outcome 可携带成对 usage，executor 将其写入 `RunAttempt` 并纳入成本汇总。
- 旧 Manifest 与两个外部证据目录保持不变。修正改变请求字节和 journal 行为，后续必须提交、重新冻结并获得新的付费批准，不能在当前回合自动执行第三次 Pilot。

详细证据、计算和后续门禁见 [`M5.5 Moonshot Pilot 执行与纠偏报告`](../reports/m5.5-moonshot-pilot-review.md)。当前仍不存在正式实验结论，M5.5 未结束。

2026-07-28 本地纠偏门禁结果：全仓 `666 passed`；`tests/unit/experiments` 为 `256 passed`，`experiments` 分支覆盖率 `90.12%`；Ruff format、Ruff lint、全量 mypy strict（`196` 个源文件）与三项契约快照检查均通过。测试只使用 fake client 和历史本地证据，没有读取 API key 或调用 provider。
