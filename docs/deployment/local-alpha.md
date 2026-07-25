# Agent Factory 本地 Alpha 部署说明

## 1. 支持状态

当前唯一受支持的部署形态是**单机、单 Uvicorn 进程、单个文件型 SQLite、仅绑定 loopback**。本说明是 M4.5 的制品与部署契约，不是公网生产部署指南。

```text
本机浏览器 / Python SDK
          │ HTTP + Bearer Token
          ▼
127.0.0.1 上的单 Uvicorn 进程
          │ 单写进程
          ▼
本机文件型 SQLite + wheel 内置 001-006 migrations
```

可选 Gradio Demo 作为同机第二个客户端进程访问 API，但不直接连接数据库。多个 API 进程、共享网络文件系统、容器集群和远程数据库均不属于当前支持范围。

## 2. 本地保证与非保证

当前自动化证据保证：

- sdist/wheel 包含 API、SDK、Demo 入口和 001-006 migration；
- minimal 安装不需要 `gradio` 或 `openai`；
- `demo`/`llm` extras 可从同一 lockfile 单独安装；
- 服务从隔离环境中的 wheel 导入，不依赖工作区 `src/`；
- Uvicorn 经 loopback readiness 后可接受已安装 SDK 的认证写请求；
- 正常停止并使用同一 SQLite 重启后，已写入 Prototype 可恢复；
- 两次服务日志均不包含 smoke 使用的原始 Bearer Token。

当前不保证：TLS、反向代理、OAuth/OIDC、Token 轮换、公网限流、租户隔离、多进程写入、高可用、在线备份、不可信工具沙箱或外部写工具副作用回滚。

## 3. 前置条件

- Python 3.11；
- uv；
- 本地磁盘上的可写数据目录；
- 至少 32 字符的随机认证 Token；
- 服务端口只绑定 `127.0.0.1`。

构建和首次安装依赖可能访问 Python package index。Agent Factory 应用 smoke 本身只访问 loopback，不调用模型 API 或其他外部服务。

## 4. 配置契约

服务读取 `AGENT_FACTORY_*` 环境变量。最小配置如下：

```dotenv
AGENT_FACTORY_ENVIRONMENT=local-alpha
AGENT_FACTORY_DATABASE_URL=sqlite+aiosqlite:///E:/Agent-Factory-Runtime/data/agent_factory.db
AGENT_FACTORY_DATA_DIR=E:/Agent-Factory-Runtime/data
AGENT_FACTORY_AUTH_TOKEN=replace-with-at-least-32-random-characters
AGENT_FACTORY_AUTH_SUBJECT=local-owner
AGENT_FACTORY_AUTH_ROLES=["admin"]
```

约束：

| 配置 | 要求 |
| --- | --- |
| `DATABASE_URL` | 只支持当前文件型 SQLite 路径；目录必须预先可写 |
| `DATA_DIR` | 与数据库、日志和临时制品放在空间充足的本地磁盘 |
| `AUTH_TOKEN` | 32-4096 字符，不得写入 Git、命令行参数或日志 |
| `AUTH_SUBJECT` | 当前单一人工 owner 的稳定审计身份 |
| `AUTH_ROLES` | 本地 owner 使用 `["admin"]`；不是多用户权限系统 |

未设置 `AUTH_TOKEN` 时服务 fail-closed：`/health/live` 仍返回 200，`/health/ready` 和业务操作不可用。

## 5. 启动与停止

在源码开发环境中：

```powershell
uv sync --locked --extra dev --extra test
uv run uvicorn agent_factory.interfaces.api.main:app --host 127.0.0.1 --port 8000
```

在安装后的 wheel 环境中，工作目录不得是仓库 `src`，且不得设置指向工作区的 `PYTHONPATH`：

```powershell
python -I -m uvicorn agent_factory.interfaces.api.main:app --host 127.0.0.1 --port 8000
```

启动 lifespan 会先执行 migration，再把 Container 标记为 ready。只有以下两个检查均成功后才能接受业务流量：

```text
GET /health/live  -> 200 {"status":"ok"}
GET /health/ready -> 200 {"status":"ok"}
```

停止时向 Uvicorn 发送平台支持的终止信号并等待 application shutdown 完成。不要在数据库写入期间直接删除进程或数据库文件。Windows 下 Uvicorn 捕获 SIGBREAK 后日志必须同时包含 `Application shutdown complete.` 和 `Finished server process`；M4.5 smoke 只在这两个标记存在时接受平台退出码 3。

## 6. 制品验收命令

从仓库根目录运行：

```powershell
uv --cache-dir E:/Agent-Factory/.tmp/uv-cache run python -m scripts.local_alpha_smoke --work-root E:/Agent-Factory/.tmp/local-alpha-smoke --uv-cache-dir E:/Agent-Factory/.tmp/uv-cache
```

该命令会：

1. 在唯一临时 run 目录中重新构建 sdist/wheel；
2. 验证 archive 路径、metadata、extras、entry point 与 001-006 migration；
3. 从 `uv.lock` 导出 minimal 依赖并以 `--no-deps` 安装 wheel；
4. 从隔离环境和工作区外目录启动 Uvicorn；
5. 使用已安装 SDK 注册固定 Prototype；
6. 正常停止、重启并查询同一 Prototype；
7. 验证数据库 migration history 恰好为 1-6；
8. 在第二个环境安装并导入 `demo`/`llm` extras；
9. 扫描服务日志中的原始 Token，确认成功后清理 run 目录。

成功输出包含 wheel 名、migration 版本和固定 Prototype ID。失败时保留 run 目录用于诊断，但不回显 Token。使用 `--keep-workdir` 可在成功后保留隔离环境；完成检查后应手动删除以释放磁盘空间。

## 7. 数据恢复与备份

Alpha 采用文件型 SQLite。可辩护的备份流程是：

1. 停止 Uvicorn 并确认 application shutdown 完成；
2. 确认没有残留 API 进程持有数据库；
3. 复制数据库文件到访问受限的备份目录；
4. 记录应用版本、最新 migration 版本和备份时间；
5. 恢复时先保留原文件，再用同版本 wheel 启动并检查 readiness；
6. 升级 wheel 后由启动 lifespan 执行 forward-only migration。

当前不宣称支持运行中直接复制数据库、跨版本降级或自动 disaster recovery。已应用 migration 的 checksum 不允许修改；回滚应用版本不能自动回滚数据库 Schema。

## 8. 故障排查

| 现象 | 检查顺序 |
| --- | --- |
| live 200、ready 503 | Token 是否设置；migration 是否成功；SQLite 文件和目录是否可写 |
| wheel 启动却导入工作区源码 | 清除 `PYTHONPATH`；离开仓库目录；使用隔离解释器和 `-I` |
| `database is locked` | 是否启动了多个 API 写进程；上次进程是否真正退出 |
| migration checksum changed | 已应用 SQL 是否被修改；不得覆盖 history 或伪造成功版本 |
| 端口占用 | 选择新的 loopback 端口；smoke 只对明确 bind conflict 有限重试 |
| C 盘空间不足 | 显式把 work root、uv cache、数据库和日志放在 E 盘；不要使用默认系统临时目录 |
| smoke 安装失败 | 区分 package index/代理问题与应用启动问题；失败 run 目录保留诊断日志 |

## 9. 安全阻断项

在单独批准并完成 Productionization 里程碑前，禁止：

- 将 Uvicorn 绑定为 `0.0.0.0` 或暴露到互联网；
- 把 `.env`、Token、数据库或运行日志提交到仓库；
- 通过多个 Uvicorn worker 共享当前 SQLite；
- 注册文件、shell、任意网络或外部写工具并宣称已有沙箱保护；
- 把本地 smoke 结果表述为公网生产安全、高可用或灾难恢复证明。
