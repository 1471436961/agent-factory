# M2：技能治理

## 1. 阶段状态

- 状态：进行中。
- 开始时间：2026-07-21。
- 进入依据：M1 已由项目 owner 人工验收并封存；提交 `acf2b5d` 已推送，GitHub Actions CI #14 通过。
- 规划依据：项目 owner 于 2026-07-21 确认本阶段目标、关键取舍、工作包、风险和验收标准后进入 M2。
- 退出决策：本文第 6 节证据齐备后，由项目 owner 人工决定是否结束 M2 并进入 M3。

## 2. 阶段目标

在 M1 可持久化生产链上增加确定性的技能治理闭环：

```text
注册技能树与评估套件
        -> 原型/实例绑定技能树来源
        -> 提交外部评估证据
        -> 规则引擎生成 EvaluationReport
        -> 人工发起晋升并全量重建配置
        -> 记录观察期 TaskOutcome
        -> 达到规则阈值后自动降级
        -> 查询并重放完整审计轨迹
```

M2 验证“能力评级和配置演化能否由确定性规则治理”。它不运行 Agent 任务，也不把规则通过描述为 Agent 语义质量已经得到保证。

## 3. 范围与边界

M2 必须实现：

- `SkillTreeRef`、`SkillNode`、`SkillTree`、`ObservationPolicy` 与 DAG 校验、稳定拓扑排序。
- `EvaluationRule`、`EvaluationSuite`、`EvaluationSubmission`、`EvaluationReport`、`EvaluationReview` 和确定性规则执行器。
- 技能树、评估套件、报告、复核和 TaskOutcome 的 Repository、SQLite migration 与 UoW 扩展。
- 技能树来源随 Prototype、Instance snapshot 和新版 AgentSpec 持久化并参与追溯。
- 人工触发的晋升、原型基线上的配置全量重建、工具/知识/输出 Schema 冲突检查和 revision CAS。
- 观察期窗口、确定性自动降级、依赖后代移除、失效知识绑定清理和审计。
- M2 所需最小 REST 路由、稳定错误映射、幂等、事务、重启恢复和退出测试。

M2 不实现：

- Agent 任务执行、真实 Runtime Adapter、对话记忆或 checkpoint。
- OpenAI/Anthropic 等真实 LLM evaluator；LLM 信号不得进入默认路径或成为单独晋升依据。
- 自动晋升。规则通过只产生评估证据，晋升必须由显式命令触发。
- Python SDK、Tool adapter、Gradio 和可执行工具 handler，这些属于 M3。
- 用户认证、可信 reviewer 身份、PostgreSQL、多进程调度和分布式锁。
- 语义正确性、生产吞吐或公网部署安全性的结论。

## 4. 已确认设计决定

### 4.1 评估输入必须显式携带外部结果

规则引擎不能只接收 AgentSpec 和测试 case；它必须接收每个 case 的实际输出。M2 引入 `EvaluationSubmission`，由测试 fixture 或未来 Runtime Adapter 提供 case result。规则引擎只做纯计算，不负责运行 Agent。

### 4.2 技能树来源必须进入持久化快照

`active_skill_nodes` 单独存在时无法说明节点属于哪棵树。M2 将 `SkillTreeRef(tree_id, version, checksum)` 作为独立治理引用加入 Prototype、Instance 和新版 AgentSpec。M1 历史数据缺失该字段时按 `None` 读取；已发布原型不得原地绑定技能树，应注册新原型版本。

### 4.3 晋升与新增知识绑定原子提交

技能节点可以增加必填知识槽，而 M1 的 `bind_knowledge` 会拒绝尚未声明的槽位。`PromoteAgentCommand` 因此允许携带目标节点新增槽所需的知识选择；Controller 先构建候选配置，再校验最终绑定集合，并把新 revision、知识绑定、审计和幂等响应放入同一 UoW。

### 4.4 stale 是使用时关系，不修改历史报告

`EvaluationReport` 永久绑定生成时的 instance revision 与 AgentSpec checksum。实例后来变化不会改写报告或保存 mutable stale 标志；晋升时发现当前 revision/checksum 不匹配，返回 `STALE_EVALUATION_REPORT`。

### 4.5 人工复核是独立不可变记录

`EvaluationReview` 独立于报告追加，避免修改历史评估事实。M2 没有认证，reviewer 仍是不可信审计标签；只有 owner 控制的本地环境可以把该流程作为人工门禁演示，不能宣称具备可信远程审批。

## 5. 实施工作包

### M2.1 领域契约与纯算法（已完成）

- 完成技能、评估、复核、观察模型与稳定错误码。
- DAG 拒绝环、缺失父节点、自依赖、重复节点和未知 active node。
- 实现确定性 rule engine、拓扑排序、后代计算和 `apply_skill_nodes()` 全量重建。
- 固定 Prompt appendix、工具、知识槽和 output schema 的组合与冲突规则。
- 增加向后兼容测试：M1 Prototype、Instance 和 AgentSpec JSON 仍可读取。

### M2.2 SQLite 持久化（已完成）

- 新增 forward-only `003_skill_governance.sql`，不修改 `001_initial.sql` 或 `002_persistence_contracts.sql`。
- 增加 SkillTree、EvaluationSuite、EvaluationReport、EvaluationReview、TaskOutcome 仓储。
- 扩展 UoW，并保证治理对象、实例 revision、审计和幂等响应同事务提交。
- 保存 canonical payload 与关系型投影；读取时校验 ID、版本、checksum、来源引用和外键。
- 覆盖 migration 重入、损坏检测、round trip、唯一键与回滚测试。

### M2.3 确定性评估服务（未开始）

- 注册并查询技能树和评估套件。
- 调用 M2.1 已验证的 `DeterministicRuleEngine`，将纯 `EvaluationOutcome` 与服务端生成的报告 ID、Spec 来源和时间组合为 `EvaluationReport`。
- 输出只由 HARD/SOFT 规则和显式人工复核决定；JudgeSignal 仅可作为非阻断性附加信息。
- 评估报告绑定 instance revision、AgentSpec checksum、suite version 和 case-result checksum。
- 评估期间实例发生变化时仍可保存关于旧 revision 的报告，但该报告不能晋升当前 revision。

### M2.4 晋升与配置重建

- 只有显式 `PromoteAgentCommand` 可以触发晋升。
- 校验实例状态、expected revision、技能树来源、父节点、suite、报告、复核和知识要求。
- 从来源原型 definition 与完整 active node 集合重新构建配置，不在当前配置上做反向 patch。
- 原子保存 `revision + 1`、新增知识绑定、active node、审计事件和幂等结果。
- 并发晋升依赖现有 snapshot/head CAS，只允许一个事务成功。

### M2.5 观察期与降级

- `TaskOutcome` 追加到独立表；未触发降级时不增加实例 revision。
- 按固定窗口计算连续失败数和失败率，达到任一阈值后触发确定性降级。
- 移除目标节点及依赖它的所有后代，保留无依赖关系的其他分支。
- 从原型重新构建配置，仅保留候选配置仍声明且继续匹配的知识绑定。
- 生成 `DEGRADED` 的新快照，并记录触发窗口、移除节点、移除绑定和 resulting revision。

### M2.6 REST 与闭环验收

- 增加技能树、评估套件、评估、复核、晋升和 TaskOutcome 的最小路由。
- Router 只做 DTO/Command 转换，领域错误继续使用统一 envelope。
- 实现 `test_evaluate_promote_observe_degrade`，从 M1 实例完成 M2 主链。
- 关闭并重建 app 后重放树、套件、报告、复核、实例 revision 与审计。
- 执行 Ruff、mypy strict、pytest、三层 branch coverage、sdist/wheel 和 GitHub Actions。

## 6. 验收标准

- [x] 技能树非法图和有效多分支 DAG 均有确定性单元测试。
- [x] 相同原型与 active node 集合不受输入顺序影响，生成相同配置与 checksum。
- [x] 评估规则覆盖全部 M2 RuleKind，HARD/SOFT 与人工复核决策有边界测试。
- [x] EvaluationReport 永久绑定 instance revision、AgentSpec checksum 和 suite version。
- [ ] stale report、错误 suite、缺失父节点和未通过报告均禁止晋升。
- [ ] 晋升新增知识、实例快照、审计和幂等响应同时成功或同时回滚。
- [ ] 同一 expected revision 的并发晋升只有一个成功。
- [ ] 未达到观察阈值时 revision 不变；达到阈值后移除目标及后代并产生新 revision。
- [ ] 降级后的配置从原型重建，不残留已移除节点独有工具、Prompt 或知识绑定。
- [ ] 所有治理写操作均有审计、幂等和失败原子性证据。
- [ ] M1 历史快照可读取，M2 数据在应用重启后可完整恢复。
- [x] domain、application 和全项目 branch coverage 分别不低于 90%、85% 和 80%。
- [ ] Ruff、mypy strict、pytest、sdist/wheel 与 M2 退出候选 GitHub Actions 全部通过。

## 7. 验收证据矩阵

| 验收面 | 计划证据 |
| --- | --- |
| DAG、拓扑与配置重建 | domain unit tests |
| 规则执行与决策 | evaluation engine unit tests |
| M1 快照兼容 | model/SQLite compatibility tests |
| migration 与仓储 | file-backed SQLite integration tests |
| 晋升、降级与事务 | Controller integration tests |
| revision 与 stale report | concurrency integration tests |
| REST 契约与脱敏错误 | API contract tests |
| 重启恢复主链 | `test_evaluate_promote_observe_degrade` |
| 覆盖率、构建与资源 | CI workflow 与 wheel 内容检查 |

## 8. 已知风险与处理

| 风险 | M2 处理 | 重新评估条件 |
| --- | --- | --- |
| M1 快照新增可选字段 | 默认 `None`、旧 fixture 回放、旧 Spec 版本继续读取 | 发现无法无损读取历史 payload |
| 外部评估结果来源不可信 | 明确称为 submitted evidence，保存 checksum，不宣称独立测量 | Runtime Adapter 与认证进入范围 |
| 多分支配置冲突 | 稳定顺序、明确冲突、fail fast，不自动 merge | 出现经过验证的合并业务规则 |
| SQLite 单写者 | UoW + CAS 保证正确性，不增加应用锁 | busy/revision conflict 成为实测瓶颈 |
| 自动降级误触发 | 最小样本、窗口、连续失败和失败率均可审计 | 真实任务数据表明阈值不稳定 |
| M2 范围扩大 | 按 M2.1-M2.6 独立设计、测试和提交 | 单个工作包仍难以审查时继续拆分 |

## 9. 阶段报告

当前状态：M2.1 已完成并提交；M2.2 已完成本地实现和第一轮质量门禁，尚未形成实现提交；M2.3 尚未开始。该状态不代表完整技能治理闭环或 M2 阶段已经验收。

- M1 封存提交：`acf2b5d docs: close M1 milestone`。
- M1 封存远程证据：GitHub Actions CI #14 在提交 `acf2b5d` 上通过。
- M2 规划确认时间：2026-07-21。
- M2.1 实现提交：`8500ac1 feat: add deterministic M2 skill domain`。
- M2.1 代码边界：新增技能/评估模型、稳定错误码、DAG/配置重建、六类确定性规则、regex 超时保护和 M1 Spec 兼容 checksum；未新增 M2 migration、Repository、Controller 或路由。
- M2.1 本地测试：修改前基线 `87 passed`；实现后 `114 passed`。
- M2.1 本地质量证据：Ruff 通过、mypy strict 通过；branch coverage 为 domain 96%、application 93%、total 93%；sdist/wheel 构建成功，wheel 包含全部 M2.1 模块及两份已发布 migration。
- M2.1 兼容证据：M1 Prototype、Instance 和 AgentSpec 固定 JSON 可读取；修改前记录的 AgentSpec 1.0 golden checksum `e979beccb60f339b3a846fd5ca8c1916a341b0a29daa1260fb0396e14a59dc0b` 保持不变。
- M2.2 代码边界：新增 `003_skill_governance.sql`、五类治理 Repository、UoW 端口、Prototype/Instance 技能树来源投影，以及 SkillTree、EvaluationSuite、AgentSpec 1.1 的确定性 checksum；未新增 Controller、REST、晋升或降级流程。
- M2.2 持久化约束：报告通过复合外键绑定现有 AgentSpec revision/checksum、SkillTree 和 EvaluationSuite；TaskOutcome 通过复合外键绑定同一 instance revision 的报告；review 对 report 保持唯一。
- M2.2 兼容证据：包含 Prototype、Instance 和 AgentSpec 的真实 v2 数据库可迁移到 v3 并继续读取；AgentSpec 1.0 golden checksum 保持不变。
- M2.2 本地测试：开始前基线 `114 passed`；第一轮完整门禁 `124 passed`，Ruff 与 mypy strict 通过。
- M2.2 本地质量证据：branch coverage 为 domain 96%、application 94%、total 93%；sdist/wheel 构建成功，且两种产物均包含 `003_skill_governance.sql`、治理仓储和共享 SQLite 基类。
- 第 6 节只勾选已经由当前测试直接证明的五项；其余条目等待后续工作包产生证据。
