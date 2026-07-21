# SQLite Persistence Design Note

## 1. 解决的问题与边界

本文起源于 M1.2，并在 M2.2、M2.4 扩展。它把不可变领域快照连接到文件型 SQLite，并建立应用服务可以依赖的 Repository 与 Unit of Work 契约。它负责持久化、事务、乐观并发、canonical JSON、关系投影和安全错误转换；不实现原型状态策略、知识槽匹配、评估决策、晋升/降级、Controller 或 REST 路由。

## 2. 依赖方向

```text
domain/audit.py          application/queries.py
       |                         |
       +------------+------------+
                    v
        application/repositories.py
                    |
                    v
        application/unit_of_work.py
                    ^
                    |
 infrastructure/sqlite/repositories.py
 infrastructure/sqlite/unit_of_work.py
                    |
                    v
       SQLite 001 + 002 + 003 migrations
```

- application 只声明 Protocol，不导入 SQLite。
- infrastructure 实现端口，可以导入 application/domain，反向依赖禁止。
- `Container` 只组装 `SqliteUnitOfWorkFactory`；连接在进入 UoW 时创建，在退出时关闭。
- M1 暴露 Prototype、Knowledge、Instance、AgentSpec、Audit 和 Idempotency 仓储；M2.2 增加 SkillTree、EvaluationSuite、EvaluationReport、EvaluationReview 和 TaskOutcome 仓储。

## 3. 事务生命周期

```text
uow_factory(read_only=False)
          |
          v
  open independent connection
          |
          +-- write --> BEGIN IMMEDIATE
          |
          +-- read  --> PRAGMA query_only=ON + BEGIN
          |
          v
 repositories share this connection
          |
          +-- commit() ------------> durable business + audit data
          |
          +-- rollback()/exception -> discard all writes
          |
          v
       close connection
```

写事务提前取得 SQLite 写锁，避免“先读后写”升级锁时出现不可预测的 `SQLITE_BUSY`；代价是 Alpha 单机部署同一时刻只有一个写事务。只读事务不占写锁，并由 `query_only` 阻止误写。`busy_timeout` 默认 5 秒，可通过 `AGENT_FACTORY_SQLITE_BUSY_TIMEOUT_MS` 调整。

UoW 是一次性的。进入前、退出后、重复进入、重复 commit/rollback 都 fail fast。`__aexit__` 只要发现连接仍处于事务中就执行 rollback，因此忘记 commit 和异常路径不会留下部分数据。应用服务应把 `commit()` 作为上下文内最后一个有副作用的动作，不应捕获仓储异常后继续提交同一 UoW。

## 4. Repository 契约

- `PrototypeRepository.replace(prototype, expected_status)` 只执行状态 compare-and-swap。应用层负责构造满足 `published_at`、`deprecation_reason` 等不变量的新快照。
- `InstanceRepository.save_snapshot(instance, expected_revision)` 要求 `instance.revision == expected_revision + 1`，先追加快照，再按 expected revision 更新 head。
- `AgentSpecRepository.add_if_absent(spec)` 使用目标明确的 `ON CONFLICT(instance_id, revision) DO NOTHING`，返回是否由当前事务完成首次写入。
- `get()` 在记录不存在时返回 `None`；M1.3 Controller 再转换为 `*_NOT_FOUND`。
- prototype/knowledge 唯一键冲突分别转换为稳定的 `PrototypeAlreadyExistsError` 和 `KnowledgeAlreadyExistsError`。

M2.2 治理仓储保持不可变快照语义：Tree/Suite/Report/Review 使用 `add/get`，TaskOutcome 使用 `append/list_for_node`。观察窗口查询先选取时间最新的 N 条，再按时间正序返回，调用方可以直接按发生顺序计算连续失败。`limit` 固定为 `1..100`，避免无界读取。

原型列表为保证 SemVer 数值顺序，会在 Alpha 中读取筛选后的快照并在 Python 中稳定排序，再执行分页。这避免把 `1.10.0` 错排在 `1.2.0` 之前，代价是数据量增大后需要把 SemVer 拆列或引入数据库排序表达式。

## 5. Canonical JSON 与损坏检测

写入流程固定为：

```text
Pydantic snapshot
  -> model_dump(mode="json", exclude_none=False)
  -> canonical_json_bytes()
  -> UTF-8 TEXT
```

读取使用 `model_validate_json()` 恢复强类型快照，然后核对关系型投影列与 payload：稳定 ID、版本、状态、来源原型、时间和 checksum 必须一致。原型 definition、实例 configuration、AgentSpec 和 INLINE 知识等可本地重算的 checksum 会再次计算。非法 JSON、Pydantic 校验失败或投影不一致均转换为 `RepositoryUnavailableError`，details 只包含 repository 和安全 reason；原始异常保存在 `__cause__` 供服务端诊断，不进入接口响应。

`instance_snapshots.created_at` 在当前 Schema 中表示该 revision 的快照时间，对应 `AgentInstance.updated_at`；实例最初创建时间仍保存在完整 payload 的 `created_at` 字段中。

M2.2 对包含无序集合的 checksum 做显式规范化：SkillTree 的 parents、granted tools 和 knowledge kinds，AgentSpec 1.1 的 active nodes 与 permission tags 均先排序再哈希。AgentSpec 1.0 继续走原有算法，已发布的 golden checksum 不变。

## 6. Migrations 002、003 与 004

`001_initial.sql` 已受 migration checksum 保护，不允许原地修改。`002_persistence_contracts.sql`：

- 为审计表增加 `causation_id`，保证 `AuditEvent` 元数据无损往返；
- 将幂等记录改为 application operation、请求哈希、结构化响应和创建/过期时间；
- 删除 HTTP `response_status`，让 M1.3 Controller 和未来 SDK/Tool adapter 不依赖 REST 语义；
- 重建旧幂等表前通过临时 CHECK guard 要求记录数为零，发现未知旧数据时整个 migration 回滚。

该 guard 合理的前提是 001 发布时项目尚未存在任何写入幂等表的 Repository。若未来迁移已投入真实使用的表，必须提供显式的数据转换或人工迁移流程，不能沿用“必须为空”的策略。

`003_skill_governance.sql` 只向前新增治理结构，不修改 001/002：

- 五类治理快照表保存 canonical payload 和关键投影；
- `skill_node_suites` 保证技能节点引用已注册且 checksum 一致的 EvaluationSuite；
- `prototype_skill_trees` 与 `instance_skill_trees` 保存来源投影，读取时与 JSON 快照双向核对；
- EvaluationReport 通过复合外键绑定现有 AgentSpec 的 instance、revision 和 checksum，同时绑定已注册 Tree/Suite；
- TaskOutcome 通过 `(report_id, instance_id, instance_revision)` 复合外键禁止跨实例复用报告；
- review 对 report 使用唯一约束，TaskOutcome 使用 `(task_id, instance_id, skill_node_id)` 唯一标识。
- Report、Review 和 TaskOutcome 额外保存完整 canonical payload 的 SHA-256，读取时重算，以覆盖未拆为投影列的证据、评论等字段。

`004_instance_configuration_checksum.sql` 修正技能特化后的配置投影：

- 为 `instance_snapshots` 增加 revision 级 `configuration_checksum`；
- M2.4 之前的受支持写路径不会改变 configuration，因此历史行可由来源 Prototype checksum 回填；
- insert/update trigger 拒绝缺失或长度非法的 checksum；
- Repository 对每个新 revision 写入实际 configuration checksum，并在读取时从 payload 重算比较；
- Prototype checksum 继续只表示来源 definition，不再被误用为所有后续配置的 checksum。

## 7. 并发与错误边界

实例并发写入可能在两个位置冲突：相同 `N+1` 快照的主键，或 head compare-and-swap 的 `rowcount != 1`。两者统一表现为 `RevisionConflictError`，并依赖 UoW 回滚整个事务。服务端不自动合并两个并发配置变更。

SQLite 驱动错误统一转换为 `RepositoryUnavailableError`；响应不得包含 SQL 文本、数据库路径或驱动错误。已知的业务唯一键冲突单独映射，其他约束错误视为基础设施不可用或持久化契约被破坏。

## 8. 已知限制与替代方案

- 直接使用 `aiosqlite` 使事务和 SQL 可见，但需要手工维护行映射。出现 PostgreSQL、多后端或大量关系查询时，重新评估 SQLAlchemy Core/ORM 与 Alembic。
- 每个 UoW 新建连接，适合 Alpha 的本地文件数据库；连接建立成为实测瓶颈后再评估连接池。
- SQLite 单写者限制吞吐；持续出现高比例 busy/revision conflict 时迁移 PostgreSQL，而不是继续增加应用锁。
- JSON 快照便于演进和回放，但无法依赖列约束检查内部字段；高频 JSON 字段查询出现后再拆分投影列和索引。

## 9. 验证方法

- migration 集成测试证明 001/002/003/004 顺序执行、重复运行幂等，以及 002 guard 失败时新增列、历史版本和旧记录全部回滚。
- v2 升级测试先写入 M1 Prototype、Instance 和 AgentSpec，再应用 003/004，证明旧快照完成 checksum 回填后仍可读取。
- 真实 SQLite 测试覆盖 M1 六类仓储和 M2 五类仓储往返、canonical 快照、来源外键、稳定唯一键错误、状态 CAS、审计查询和观察窗口。
- 两个并发写 UoW 同时保存 revision 2，断言一个提交、一个 `REVISION_CONFLICT`，head 和历史快照保持一致。
- 故障注入在业务写和审计写之后抛出异常，断言两者都不存在。
- 只读 UoW 写入、损坏 JSON、关系投影和 configuration checksum 篡改均返回安全的 `REPOSITORY_UNAVAILABLE`。
