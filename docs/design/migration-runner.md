# SQLite Migration Runner 设计说明

## 1. 解决的问题

M0 必须证明一个全新 SQLite 数据库能够在应用启动前升级到已知版本，并且同一组 migration 可重复执行。仅调用 `CREATE TABLE IF NOT EXISTS` 不能证明数据库历史可信，因为它无法识别已执行 SQL 后来被修改的情况。

## 2. 文件与历史模型

迁移文件作为 package data 存放在 `agent_factory/infrastructure/sqlite/sql`，采用 `NNN_name.sql`，版本必须从 `001` 连续递增。默认路径由已安装模块位置解析，保证源码 checkout 和 wheel 安装使用同一份 SQL；环境变量只用于显式覆盖。Runner 读取原始字节并计算 SHA-256，在数据库的 `schema_migrations` 表保存：

```text
version, name, checksum, applied_at
```

启动时出现以下情况会直接失败：

- 文件名不合法、版本重复或不连续。
- 数据库存在本地已缺失的历史版本。
- 已应用版本的名称或 checksum 与本地文件不一致。
- 配置不是文件型 SQLite 数据库。

## 3. 事务边界

Python `sqlite3.executescript()` 不会自动为脚本提供完整事务语义，因此 Runner 将以下内容组合成同一次脚本执行：

```sql
BEGIN IMMEDIATE;
-- migration SQL
INSERT INTO schema_migrations (...);
COMMIT;
```

业务 DDL 与历史记录由同一事务提交。任何 SQL 错误都会触发 rollback，并转换为 `MigrationExecutionError`，应用 lifespan 因此中止，服务不会进入 ready 状态。

Migration 是仓库内受信任代码，不接受用户输入。名称由文件名正则约束，checksum 为内部 SHA-256，时间由 `Clock` 生成；写入脚本前仍执行 SQL literal escaping。

## 4. 已知限制

- 只支持 `sqlite` 与 `sqlite+aiosqlite` 文件 URL。
- 只支持 forward migration，不实现自动 downgrade。
- M0 假设单进程启动，不承诺多个进程同时迁移。
- 切换 PostgreSQL 或出现复杂回滚要求时，应评估 Alembic，而不是扩展 SQLite 专用逻辑。

## 5. 验证

- 新数据库执行 `001_initial.sql` 并记录历史。
- 第二次运行不重复应用。
- 修改已执行文件后抛出 `MigrationChecksumError`。
- FastAPI lifespan 在接受请求前完成迁移。
