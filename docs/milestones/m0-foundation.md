# M0：文档与工程骨架

## 1. 目标

建立后续业务实现可依赖的最小工程基线：固定 Python 与依赖环境，提供可启动的 FastAPI 应用、可重复的 SQLite migration、可替换的系统端口，以及本地和 CI 共用的质量命令。

## 2. 范围

M0 实现：

- `src` layout 与包构建配置。
- `Settings`、依赖容器和应用生命周期。
- `Clock`、`IdGenerator`、`CorrelationContext` 端口及默认实现。
- SQLite migration 发现、校验、执行和历史记录。
- liveness/readiness 健康检查。
- 单元测试、集成测试和 GitHub Actions。

M0 不实现：

- 原型、知识包、实例和 AgentSpec 的业务模型与仓储。
- 工厂控制器公开操作。
- LLM、运行时适配器、Gradio 或向量数据库。
- 用户认证与对外生产部署。

## 3. 关键取舍

| 决策 | 当前选择 | 原因 | 后续触发条件 |
| --- | --- | --- | --- |
| Python 基线 | 3.11 | 与架构类型目标一致，验证最低支持版本 | 明确停止支持 3.11 |
| 包结构 | `src` layout | 避免测试误导入仓库根目录 | 无 |
| 依赖管理 | uv + `uv.lock` | 本地与 CI 使用同一解析结果 | uv 无法满足部署环境 |
| Migration | SQLite 专用轻量 runner | Alpha 仅单机 SQLite，避免提前引入多后端抽象 | 引入 PostgreSQL 或复杂回滚需求 |
| API 启动 | FastAPI lifespan | 迁移完成后才接收请求 | 无 |

## 4. 验收标准

- [x] `uv sync --extra dev --extra test` 成功。
- [x] `uv run ruff format --check src tests` 通过。
- [x] `uv run ruff check src tests` 通过。
- [x] `uv run mypy src tests` 通过。
- [x] `uv run pytest -q` 通过。
- [x] 新数据库从 migration 0 升级到最新版本。
- [x] 重复运行 migration 不改变已应用历史。
- [x] 修改已应用 migration 后会检测 checksum 冲突。
- [x] FastAPI lifespan 成功迁移数据库并返回 readiness。
- [x] wheel 与 sdist 构建成功，wheel 包含 `001_initial.sql`。
- [x] 隔离环境安装 wheel 后可从 package 路径完成 migration。
- [x] GitHub Actions 首次远程运行通过。

## 5. 阶段报告

当前状态：已完成并封存；项目已进入 M1。

- 完成时间：2026-07-18。
- 交付摘要：已建立 Python 3.11、uv 锁文件、应用配置、系统端口、SQLite migration runner、FastAPI lifespan、健康检查、测试和 CI workflow。
- 测试结果：11 passed，分支覆盖口径总覆盖率 91%。
- 静态检查：Ruff format、Ruff lint、mypy strict 全部通过。
- 打包结果：成功构建 `agent_factory-1.0.0a1` wheel 与 sdist；隔离 Python 3.11 环境安装 wheel 后，从 `site-packages` 内置 SQL 完成 migration 并进入 ready。
- 运行结果：本地 uvicorn 的 liveness/readiness 均返回 `ok`，数据库已应用 `001_initial.sql`。
- 远程证据：GitHub Actions [`CI #1`](https://github.com/1471436961/agent-factory/actions/runs/29647313254) 在提交 `712bb32` 上通过，运行耗时 21 秒。
- 未解决风险：SQLite migration runner 暂不支持多进程并发迁移和 downgrade；当前远程 CI 仅覆盖 Ubuntu + Python 3.11，尚未强制覆盖率门禁和 wheel 隔离安装冒烟测试。
- 进入 M1 的人工结论：项目 owner 于 2026-07-18 确认 M0 验收通过，正式进入 M1。
