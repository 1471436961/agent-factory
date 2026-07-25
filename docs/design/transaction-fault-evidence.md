# M4.4 事务与并发故障证据

## 1. 目标与判定口径

本说明回答一个可验证的问题：Agent Factory 当前 15 类写能力在 SQLite 事务失败、幂等重放或同 revision 并发时，是否会留下互相矛盾的业务事实、审计事实和重放事实。

证据分为三层：

1. **入口行为证据**：每个写入口均有集成测试验证成功写入、审计事件、重放或冲突语义。
2. **共享事务证据**：所有入口均通过同一个 `SqliteUnitOfWork` 提交业务 Repository、`AuditRepository` 与 `IdempotencyRepository`；未提交或异常退出统一回滚。
3. **代表性故障注入**：revision 写路径在四个持久化阶段注入失败；ToolCall 终态写路径在三个适用阶段注入失败；migration 使用真实失败 SQL 验证 DDL 与 history 同时回滚。

因此，本矩阵证明的是当前代码结构和文件型 SQLite 下的事务原子性，不声称每个入口都独立执行了四组相同的故障注入。

## 2. 写能力证据矩阵

`IdempotencyRecord` 一栏为 `-` 时，入口使用数据库唯一约束承担重放收敛：AgentSpec 使用 `(instance_id, revision)`，ToolCall 使用 `call_id`。

| 写能力 | 主持久化事实 | revision | 审计事件 | 重放约束 | 入口行为证据 |
| --- | --- | --- | --- | --- | --- |
| `register_prototype` | `AgentPrototype` | 否 | `prototype.registered`；可选 `prototype.published` | `register-prototype` | `test_controller_runs_replayable_audited_production_chain` |
| `publish_prototype` | Prototype status | 否，status CAS | `prototype.published` | `publish-prototype` | `test_controller_publishes_deprecates_and_replays_status_changes` |
| `deprecate_prototype` | Prototype status | 否，status CAS | `prototype.deprecated` | `deprecate-prototype` | `test_controller_publishes_deprecates_and_replays_status_changes` |
| `register_knowledge` | `DomainKnowledge` | 否 | `knowledge.registered` | `register-knowledge` | `test_controller_runs_replayable_audited_production_chain` |
| `clone_agent` | Instance revision 1 + head | 新建 revision 1 | `instance.cloned` | `clone-agent` | `test_controller_runs_replayable_audited_production_chain` |
| `bind_knowledge` | Instance snapshot + head | `N -> N+1` CAS | 每个变更槽位一条 `knowledge.bound` | `bind-knowledge` | `test_controller_enforces_binding_revision_and_replacement_rules`、`test_binding_preserves_untouched_slot_provenance` |
| `transition_instance` | Instance snapshot + head | `N -> N+1` CAS | `instance.transitioned` | `transition-instance` | `test_transition_is_replayable_audited_and_requires_new_spec_export` |
| `register_evaluation_suite` | `EvaluationSuite` | 否 | `evaluation-suite.registered` | `register-evaluation-suite` | `test_controller_registers_governance_and_persists_evaluation_reports` |
| `register_skill_tree` | `SkillTree` | 否 | `skill-tree.registered` | `register-skill-tree` | `test_controller_registers_governance_and_persists_evaluation_reports` |
| `evaluate_instance` | 可选 AgentSpec + `EvaluationReport` | 读取指定 revision | 可选 `spec.exported`；`evaluation.completed` | `evaluate-instance` | `test_controller_registers_governance_and_persists_evaluation_reports`、`test_evaluation_rolls_back_spec_report_and_audit_on_final_write_failure` |
| `review_evaluation` | `EvaluationReview` | 否 | `evaluation.reviewed` | `review-evaluation` | `test_manual_review_is_final_idempotent_and_redacted_from_audit` |
| `promote_agent` | Instance snapshot + head | `N -> N+1` CAS | `skill.promoted` | `promote-agent` | `test_promotion_rebuilds_configuration_and_preserves_binding_provenance`、`test_promotion_rolls_back_snapshot_audit_and_idempotency_on_failure` |
| `record_task_outcome` | `TaskOutcome`；可选 instance snapshot + head | 不降级时不变；降级时 `N -> N+1` CAS | `task-outcome.recorded`；可选 `skill.degraded` | `record-task-outcome` | `test_observation_degrades_from_prototype_and_preserves_independent_branch`、`test_degradation_rolls_back_outcome_snapshot_audit_and_idempotency` |
| `export_spec` | `AgentSpec` | 固定当前 revision | `spec.exported` | `(instance_id, revision)` 唯一约束 + 写事务内二次检查 | `test_concurrent_first_spec_export_persists_one_spec_and_audit` |
| `ToolExecutor` 终态记录 | `ToolCallRecord` | 固定 Spec revision | `tool.called` | `call_id` 唯一约束 | `test_success_record_and_audit_survive_process_rebuild_without_content`、`test_tool_record_and_audit_are_atomic_at_every_persistence_stage` |

测试函数分别位于：

- 核心生产链：`tests/integration/test_factory_controller.py`
- 生命周期：`tests/integration/test_lifecycle_controller.py`
- 评估：`tests/integration/test_evaluation_controller.py`
- 晋升与降级：`tests/integration/test_promotion_controller.py`、`tests/integration/test_degradation_controller.py`
- 工具终态：`tests/integration/test_tool_execution.py`
- M4.4 故障与并发：`tests/integration/test_transaction_fault_injection.py`

## 3. 故障注入模型

`tests/support/fault_injection.py` 装饰真实 `UnitOfWork` 与 Repository。被代理的方法先在真实文件型 SQLite 事务中完成写入，再抛出 `InjectedTransactionFailure`，由原 UoW 的异常退出路径执行 rollback。只读 UoW 不注入故障，便于在失败后从数据库重新读取事实。

```text
save entity/snapshot
    ├─ fail: AFTER_ENTITY_WRITE
    ▼
append audit
    ├─ fail: AFTER_AUDIT_WRITE
    ▼
save idempotency result
    ├─ fail: AFTER_IDEMPOTENCY_WRITE
    ▼
commit
    └─ fail: BEFORE_COMMIT
```

`transition_instance` 覆盖上述四点，并逐项断言：

- head 仍指向原 revision；
- 新 snapshot 不存在；
- `instance.transitioned` 审计不存在；
- `IdempotencyRecord` 不存在；
- 恢复原 UoW 后，使用同一命令和幂等键可以成功重试。

ToolCall 不写 `IdempotencyRecord`，因此覆盖 `AFTER_ENTITY_WRITE`、`AFTER_AUDIT_WRITE` 和 `BEFORE_COMMIT`，逐项断言 `ToolCallRecord` 与 `tool.called` 审计均不存在。

## 4. 并发与 migration 证据

| 风险 | 自动化证据 | 判定 |
| --- | --- | --- |
| 同 revision 并发状态转换 | `test_concurrent_transitions_from_same_revision_have_one_winner` | 仅一个请求更新 head，另一个收到 revision conflict |
| 同 revision 并发晋升 | `test_concurrent_promotions_with_same_revision_allow_one_success` | 仅一个 `N+1` snapshot 和一次晋升事实 |
| 并发跨越降级阈值 | `test_concurrent_threshold_crossing_creates_one_degraded_revision` | TaskOutcome 不重复消费，最多生成一个降级 revision |
| 首次并发导出 AgentSpec | `test_concurrent_first_spec_export_persists_one_spec_and_audit` | 两个 Container 返回相同 Spec，数据库仅一条 Spec 和一条审计 |
| 原幂等键重放 | 各 Controller 集成测试；`test_transition_and_idempotency_replay_survive_container_restart` | 返回已保存结果；参数摘要不一致则拒绝复用 |
| 待执行 migration 中途失败 | `test_pending_migration_failure_rolls_back_schema_history_and_can_retry` | 部分 DDL 和版本 7 均不存在；修正未应用脚本后可重试 |
| 已应用 migration 被修改 | `test_modified_applied_migration_is_rejected` | checksum 不匹配时拒绝启动，不静默接受历史漂移 |

## 5. 已知边界

- `BEFORE_COMMIT` 在调用真实 `commit()` 之前抛错，证明未成功提交的事务会回滚；它不模拟断电、进程被强杀、磁盘损坏或 SQLite WAL/fsync 的崩溃持久性。
- 当前结论仅适用于单机文件型 SQLite 和现有 `BEGIN IMMEDIATE` 写事务，不能外推到 PostgreSQL、多个服务进程或分布式事务。
- Tool handler 在终态记录提交前已经执行。SQLite 只能回滚本地 `ToolCallRecord` 和审计，不能撤销付款、发信或文件修改等外部副作用。当前默认 `document-search` 是只读工具；未来外部写工具必须另外设计 remote idempotency、intent/outbox 或补偿协议。
- migration 只有在成功执行 SQL 并写入 `schema_migrations` 后才成为不可修改历史。尚未应用且失败的脚本没有 history 记录，因此可以修正后重试。
- 故障注入代码只存在于 `tests/support`，未向生产 Settings、Command、Repository 或公开 API 添加测试开关。

## 6. 可复现命令

```bash
uv run pytest -q \
  tests/integration/test_transaction_fault_injection.py \
  tests/integration/test_tool_execution.py \
  tests/integration/test_migrations.py
```

该定向门禁由 `.github/workflows/ci.yml` 单独执行，同时完整 pytest 继续覆盖所有入口行为证据。
