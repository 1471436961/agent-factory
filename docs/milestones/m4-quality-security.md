# M4：Alpha 安全、回归与发布门禁

## 1. 阶段状态

- 状态：已完成并封存。
- 开始时间：2026-07-24。
- 结束时间：2026-07-25。
- 进入依据：M3 已由项目 owner 验收并封存，退出候选提交 `d2edef7` 的 GitHub Actions CI #20 通过。
- 规划依据：项目 owner 于 2026-07-24 确认 M4 的范围、工作包、风险、备选方案与退出标准。
- 退出依据：项目 owner 于 2026-07-25 确认结束 M4；退出候选提交 `4a55d73` 的 GitHub Actions [`CI #25`](https://github.com/1471436961/agent-factory/actions/runs/30148036514) 通过。
- 阶段定位：以生产级工程标准验证本地 Alpha 的安全、回归和发布边界，不宣称已经具备公网生产部署能力。

## 2. 阶段目标

M4 不增加 Agent 生产或技能治理能力，而是验证 M1-M3 已实现能力在契约漂移、恶意输入、事务故障、发布制品和真实本地进程启动条件下仍然可重复、可解释、可审计。

```text
公共契约 ──► OpenAPI / AgentSpec / Audit 回归快照
安全边界 ──► 认证、授权、输入上限、脱敏、只读工具
持久化   ──► 事务故障注入、revision CAS、幂等与 migration
发布制品 ──► sdist/wheel、package data、extras、entry point
部署检查 ──► 隔离 wheel、独立 Uvicorn、loopback HTTP smoke
```

阶段退出必须回答五个问题：

1. 公共 HTTP 和稳定语义对象发生变化时，CI 能否明确发现？
2. 当前真实攻击面上的越权、伪造、超限和敏感信息泄漏能否被自动化测试阻断？
3. 写操作在审计、幂等或 commit 失败时是否会留下部分事实？
4. 用户安装的 wheel 是否包含运行所需代码、migration、extras 和入口？
5. 安装后的制品能否在独立本地进程中启动、认证、访问 SQLite 并重启恢复？

## 3. 范围与边界

### 3.1 当前范围

- 冻结并校验公开 OpenAPI operation、请求/响应 Schema 和安全声明。
- 为固定 Writer AgentSpec 与审计时间线建立稳定语义回归快照。
- 建立 `tests/security` 与 `tests/regression` 分层，不机械搬迁已有测试。
- 补齐认证/授权、请求体边界、错误脱敏、默认无外网和只读工具能力清单测试。
- 为认证失败和授权拒绝增加不含凭据的结构化安全日志。
- 为写操作建立审计、幂等、revision 与事务故障证据矩阵。
- 构建并检查 sdist/wheel、package data、optional extras 和 console entry point。
- 从隔离 wheel 启动独立 Uvicorn 进程，经 loopback TCP 完成最小认证 smoke 和重启恢复。
- 将上述门禁纳入 GitHub Actions，记录本地与远程退出证据。

### 3.2 非当前范围

- OAuth/OIDC、JWT、多用户目录、Token 轮换、撤销和租户隔离。
- WAF、公网限流、TLS 终止、反向代理和互联网暴露。
- PostgreSQL、多进程写入、分布式锁、Runtime 租约与任务接管。
- Docker/进程级不可信代码沙箱，以及文件、shell、任意网络或外部写工具。
- 真实 LLM 质量判断、成本评估与 240 次对照实验；这些属于 M5。

上述能力不是通过补几条测试就能诚实宣称完成的生产平台特性。M4 只对当前 Alpha 实际存在的接口、工具和部署方式建立可辩护证据。

## 4. 工作包

### M4.1 阶段基线与设计冻结

- 创建本阶段文档和 [`安全、回归与发布门禁设计说明`](../design/security-regression-gates.md)。
- 建立攻击面、敏感数据、写操作和现有测试证据矩阵。
- 修正“完整认证与公网安全由 M4 验收”的过度表述。
- 更新 README、项目路线图和架构文档状态。
- 重跑 M3 完整质量门禁，记录 M4 起始基线。

退出条件：范围经项目 owner 确认；文档之间无矛盾；基线门禁有可复现命令和结果。

### M4.2 公共契约与回归快照

- 生成并提交 `docs/generated/openapi-v1.json`。
- 建立 OpenAPI 精确快照测试与可重复生成/检查命令。
- 冻结固定 Writer AgentSpec 和审计时间线的稳定语义投影。
- 规定快照变更必须说明 PATCH、MINOR 或 MAJOR 契约影响。
- 保持 SDK operation manifest 与 OpenAPI 双向相等。

退出条件：生成两次字节一致；未更新快照的契约漂移会失败；动态字段规范化规则显式且有测试。

### M4.3 API 与执行安全回归

- 建立 `tests/security`，集中组织当前真实安全不变量。
- 覆盖认证 503/401、授权 403、actor 单一真相源、请求体上限和非法 Header。
- 扫描响应、日志、审计、异常和 repr，确保测试 Secret、Prompt、知识正文与工具参数不泄漏。
- 证明未授权工具不调用 handler，默认 Registry 只包含固定只读工具，默认路径不访问外网。
- 增加并验证 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。
- 增加不含凭据和请求体的认证/授权安全事件日志。

退出条件：拒绝矩阵全通过；失败发生在 Controller/handler 之前；日志和错误 envelope 保持稳定、脱敏。

### M4.4 事务与并发故障注入

- 为全部写操作建立“实体、revision、审计、幂等”证据矩阵。
- 在实体写入后、审计前，审计后、幂等前，以及 commit 时注入失败。
- 验证 snapshot/head/audit/idempotency 不产生部分提交。
- 补齐同 revision 并发单胜、原幂等键重放和 migration 失败回滚证据。
- 验证 ToolCallRecord 与 `tool.called` 审计保持同一 UoW 原子性。

退出条件：每项写能力都有现有证据引用或新增测试；故障注入优先使用真实文件型 SQLite，不在生产 API 中加入测试开关。

### M4.5 发布制品与本地部署检查

- 编写 `docs/deployment/local-alpha.md`，明确唯一支持的本地部署拓扑和阻断项。
- 从 `uv.lock` 安装依赖，并以 `--no-deps` 安装刚构建的 wheel。
- 检查最小安装、`demo`/`llm` extras、001-006 migration 和 console entry point。
- 从安装后的 wheel 启动独立 Uvicorn 进程，使用临时 SQLite、随机 Token 和 loopback 端口完成 readiness、认证 SDK 请求、正常关闭与重启恢复。
- 扫描子进程输出，确保 Token 不进入日志。

退出条件：smoke 不导入工作区源码；进程有超时、有限轮询和强制清理；测试不访问外部网络。

### M4.6 CI 门禁与阶段退出

- CI 执行 Ruff、mypy strict、分层测试、三档 branch coverage、快照检查、构建、资源检查、隔离安装和真实进程 smoke。
- M1-M3 回归继续通过，默认测试不需要模型 API key 或互联网。
- 记录测试数量、覆盖率、构建产物、提交哈希和 GitHub Actions run。
- 项目 owner 人工确认 M4 退出后再更新路线图为完成。

退出条件：全部本地门禁和远程 CI 通过，且没有通过降低 strict、删除断言或弱化快照规避失败。

## 5. 风险与取舍

| 风险 | 当前取舍 | 重新评估条件 |
| --- | --- | --- |
| 快照因时间/UUID频繁变化 | 只冻结公共契约和显式稳定语义投影 | 动态字段本身成为公共契约 |
| 安全测试与既有测试重复 | 先建证据矩阵，只补缺口 | 原测试难以表达安全不变量 |
| 为故障注入污染生产代码 | 使用 UoW/Repository 测试包装器 | 需要正式 chaos/fault framework |
| 真进程 smoke 时序波动 | loopback、临时目录、有限轮询、可靠清理 | 进入容器或多服务部署 |
| 覆盖率诱导无意义测试 | 保留阈值，优先检查不变量和副作用 | 核心层门槛无法反映真实风险 |
| 外部漏洞库导致门禁不确定 | 不进入默认阻断门禁，后续做独立定时审计 | 项目进入持续发布与依赖响应流程 |
| “安全门禁”被误读为公网安全 | 文档统一使用“Alpha 安全边界” | 单独批准 Productionization 里程碑 |

## 6. 验收标准

- [x] OpenAPI 与稳定语义快照可重复生成并由 CI 检查。
- [x] 当前认证、授权、请求和错误攻击面有集中安全回归测试。
- [x] 测试 Secret、Prompt、知识正文和工具参数不进入响应、日志或审计。
- [x] 全部写操作具备幂等/审计/事务证据矩阵。
- [x] 关键写路径故障注入后不留部分状态。
- [x] migration 从空库升级并在失败时不记录虚假版本。
- [x] wheel 包含代码、001-006 migration、extras 和入口。
- [x] 安装后的 wheel 能经独立 Uvicorn/loopback HTTP 启动和重启恢复。
- [x] domain/application/全项目 branch coverage 不低于 90%/85%/80%。
- [ ] Ruff、mypy strict、全部 pytest、构建、隔离安装和 GitHub Actions 通过。
- [x] README 与部署文档明确不支持公网生产部署。

## 7. M4.1 起始基线

执行日期：2026-07-24。以下结果均为进入 M4 后重新执行，不复制 M3 封存值。

| 门禁 | 可复现命令 | 结果 |
| --- | --- | --- |
| 格式 | `.venv\Scripts\ruff.exe format --check src tests` | 通过，137 个文件已格式化 |
| 静态检查 | `.venv\Scripts\ruff.exe check src tests` | 通过 |
| 类型检查 | `.venv\Scripts\mypy.exe src tests` | 通过，137 个 source file 无问题 |
| 全量测试 | `.venv\Scripts\pytest.exe -q --basetemp=.tmp/pytest-m4-escalated-20260724 --cov --cov-report=term-missing` | 通过，351 项测试，60.36 秒 |
| Domain branch coverage | `.venv\Scripts\coverage.exe report --include="src/agent_factory/domain/*" --fail-under=90` | 96%，通过 90% 门槛 |
| Application branch coverage | `.venv\Scripts\coverage.exe report --include="src/agent_factory/application/*" --fail-under=85` | 94%，通过 85% 门槛 |
| 全项目 branch coverage | `.venv\Scripts\coverage.exe report --fail-under=80` | 92%，通过 80% 门槛 |
| 构建 | `uv --cache-dir E:\Agent-Factory\.tmp\uv-cache build` | 通过，生成 `agent_factory-1.0.0a1.tar.gz` 与 `agent_factory-1.0.0a1-py3-none-any.whl` |
| wheel 资源 | 使用 `zipfile` 对 CI 资源清单取子集 | 通过，49 个预期代码与 `001`-`006` migration 资源存在 |
| extras 隔离安装 | 从 `uv.lock` 导出 `demo,llm` 依赖，在 `.tmp/m4-baseline-wheel-20260724` 安装依赖并以 `--no-deps` 安装 wheel | 通过，`demo`、`llm` 元数据和 `agent-factory-demo` entry point 可发现 |

环境证据：

- 仓库现有 `.venv` 用于静态检查和测试；构建 cache、依赖导出和隔离环境全部位于 E 盘仓库 `.tmp`，未新增 C 盘依赖安装。
- 受限沙箱内两次测试分别使用默认 `.tmp/pytest` 和新目录 `.tmp/pytest-m4-baseline-20260724`，均因沙箱拒绝 Pytest 清理 basetemp 而产生 setup 级 `PermissionError`；其中第一次仍有 255 项测试通过。经批准在沙箱外使用新的 E 盘 basetemp 重跑后，351 项全部通过，因此该现象归类为执行环境权限限制，不是产品测试失败。
- 沙箱内构建和 extras 安装因无法连接 PyPI 失败；经批准联网后，依赖和 cache 仍写入 E 盘并成功完成。M4.5 将把上述手工制品步骤固化为可重复脚本和真实独立进程 smoke。

## 8. M4.2 契约快照证据

M4.2 于 2026-07-24 完成以下交付：

- `docs/generated/openapi-v1.json`：冻结 19 个 path、20 个 operation、全部请求/响应 Schema 和安全声明。
- `tests/regression/snapshots/writer-agent-spec-v1.json`：冻结 revision 2、AgentSpec 1.1、Prototype/Knowledge/SkillTree checksum、`document-search@1.0.0` Schema 与 `read-only` 权限、输出 Schema 和 Spec checksum。
- `tests/regression/snapshots/writer-audit-timeline-v1.json`：冻结注册、发布、知识注册、克隆、绑定和 Spec 导出六事件顺序及 allowlisted payload；事件 UUID、时间和实例 UUID 不进入投影。
- `scripts.contract_snapshots`：提供显式 `--write` 与只读 `--check`；使用规范 UTF-8 JSON 和同目录原子替换，缺失或字节漂移返回非零退出码。
- `interfaces.api.app.create_app`：从 ASGI 全局入口中拆出无副作用工厂，生成 OpenAPI 时传入无凭据的确定性 Settings，不启动 lifespan、migration、数据库或网络。
- SDK operation manifest 契约从 method/path 集合比较加强为 method/path/authenticated 双向映射比较。
- Ruff、mypy strict 与 GitHub Actions 已纳入 `scripts`；CI 在测试前执行 `python -m scripts.contract_snapshots --check`，且 wheel 资源清单包含新的应用工厂模块。

当前快照哈希：

| 快照 | SHA-256 |
| --- | --- |
| OpenAPI v1 | `801fe9977ec213336828acad0227c552415df1e3644f2b6e4aacc70b64e08af1` |
| Writer AgentSpec v1 | `a430dec2f6e0304604683f2469e51961850b5a4d4580781f357daa44b30a7c42` |
| Writer Audit Timeline v1 | `de953ecdf8af0f11f81f33f7d65ccf1f31c3c68432deef721af2a83eca1b5cc6` |

这三份文件是首次建立的 `v1` 基线，不分类为对既有公开快照的 PATCH/MINOR/MAJOR 变更。后续任何 `--write` 结果必须在提交说明中标注影响级别，CI 不允许自动接受差异。

本地门禁结果：

| 门禁 | 结果 |
| --- | --- |
| 快照检查 | 三份文件通过 `--check`，连续生成字节一致 |
| M4.2 定向测试 | 7 项通过，其中新增 5 项回归测试 |
| Ruff format/check | 141 个文件通过 |
| mypy strict | 141 个 source file 无问题 |
| 全量 pytest | 356 项通过，67.08 秒 |
| Domain/Application/全项目 branch coverage | 96% / 94% / 92%，通过 90% / 85% / 80% 门槛 |
| sdist/wheel | `agent_factory-1.0.0a1` 构建成功；`app.py` 已包含，仓库 `scripts` 未进入 wheel |

M4.2 完成时尚无对应远程结果，因此该工作包只记录 CI 配置已接入；最终跨平台证据统一记录在第十二节，未使用后续结果反向改写当时的本地证据。

## 9. M4.3 API 与执行安全证据

M4.3 于 2026-07-25 完成以下本地实现：

- `RequestContextMiddleware` 对所有响应强制设置 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff` 和 correlation Header；`Content-Length` 必须是单个非负十进制整数，实际流式字节仍受同一上限约束。
- `agent_factory.security` 只记录认证/授权拒绝的固定事件名、correlation ID、拒绝类别和凭据存在布尔值；认证失败不进入业务审计。
- API 未知异常不再通过 traceback 格式化原始异常消息，只记录 exception type 与 correlation ID。
- `tests/security` 集中覆盖 HTTP 拒绝矩阵、响应安全头、敏感信息跨制品扫描、固定只读 Registry 和默认 socket guard。
- ToolExecutor 既有 pre-execution 拒绝矩阵增加 handler 调用次数断言，证明未授权、版本错误、身份错误、状态错误和非法输入均不会执行 handler。
- GitHub Actions 增加独立 `tests/security` 步骤；wheel 资源清单包含 `security_events.py`。

定向门禁：

| 门禁 | 可复现命令 | 结果 |
| --- | --- | --- |
| M4.3 安全与工具定向测试 | `uv run pytest -q tests/security tests/integration/test_tool_execution.py` | 32 项通过 |
| 定向 Ruff | `uv run ruff check src/agent_factory/interfaces/api tests/security tests/integration/test_tool_execution.py` | 通过 |
| 定向 mypy strict | `uv run mypy src/agent_factory/interfaces/api tests/security tests/integration/test_tool_execution.py` | 通过 |

当前无文件、shell 或网络工具，socket guard 只证明固定 `document-search` 默认路径没有创建外部连接，不能替代未来网络工具的 SSRF、防重定向或 DNS rebinding 设计。公网限流、TLS、OIDC、多租户和不可信代码沙箱仍不在 M4 范围。

完整本地门禁：

| 门禁 | 结果 |
| --- | --- |
| 契约快照 | 三份快照通过 `--check`，SHA-256 与 M4.2 基线一致 |
| Ruff format/check | 145 个文件通过 |
| mypy strict | 145 个 source file 无问题 |
| 全量 pytest | 368 项通过，69.58 秒 |
| Domain/Application/全项目 branch coverage | 96% / 94% / 92%，通过 90% / 85% / 80% 门槛 |
| CI YAML | 可解析，独立安全回归步骤已接入 |
| sdist/wheel | `agent_factory-1.0.0a1` 构建成功，wheel 包含 `security_events.py` |

受限沙箱内的首次全量测试仍因 pytest 无权在 session finish 清理 E 盘 basetemp 而失败；在获批的沙箱外使用新的 E 盘目录重跑后 368 项全部通过。首次隔离构建因沙箱网络不可用而无法解析 hatchling；获批联网后使用 `E:\Agent-Factory\.tmp\uv-cache` 成功构建，未向 C 盘安装新依赖。该工作包完成时远程结果尚未产生，最终跨平台证据见第十二节。

## 10. M4.4 事务与并发故障证据

M4.4 于 2026-07-25 完成以下本地实现：

- 新增 [`事务与并发故障证据`](../design/transaction-fault-evidence.md)，逐项记录 14 个 Controller 写操作和 1 个 ToolExecutor 终态写操作的主实体、revision、审计、重放约束与测试引用。
- 新增 `tests/support/fault_injection.py`。测试装饰器代理真实文件型 SQLite UoW，在实体写入后、审计写入后、幂等写入后和 commit 前抛出稳定异常；生产代码与公开 API 未增加故障开关。
- `transition_instance` 在四个故障点均证明 head、`N+1` snapshot、审计与幂等记录不会部分提交，并证明恢复正常 UoW 后可使用原命令重试。
- ToolExecutor 在三个适用故障点均证明 `ToolCallRecord` 与 `tool.called` 审计共同回滚。
- 两个独立 Container 首次并发导出同一 revision 的 AgentSpec 时，返回相同对象，数据库只保存一条 Spec 和一条 `spec.exported` 审计。
- 合成的待执行 `007` migration 在 DDL 后失败时，不保留部分表或虚假版本；修正尚未应用的脚本后可重试成功。
- GitHub Actions 增加独立事务故障回归步骤；`tests/support` 不进入 wheel。

本地门禁：

| 门禁 | 可复现命令 | 结果 |
| --- | --- | --- |
| M4.4 定向测试 | `uv run pytest -q tests/integration/test_transaction_fault_injection.py tests/integration/test_tool_execution.py tests/integration/test_migrations.py` | 34 项通过，12.98 秒 |
| Ruff format/check | `uv run ruff format --check src tests scripts`；`uv run ruff check src tests scripts` | 149 个文件通过 |
| mypy strict | `uv run mypy src tests scripts` | 149 个 source file 无问题 |
| 契约快照 | `uv run python -m scripts.contract_snapshots --check` | 三份 SHA-256 与 M4.2 基线一致 |
| 全量 pytest | `uv run pytest -q --cov --cov-report=term-missing` | 377 项通过，92.54 秒 |
| Domain/Application/全项目 branch coverage | 三档 `coverage report --fail-under` | 96% / 94% / 92%，通过 90% / 85% / 80% 门槛 |
| CI YAML | 使用 PyYAML 解析 workflow | 可解析，独立事务故障步骤已接入 |
| sdist/wheel | `uv --cache-dir E:\Agent-Factory\.tmp\uv-cache build` | `agent_factory-1.0.0a1` 构建成功；001-006 migration 存在，测试支持代码未进入 wheel |

`BEFORE_COMMIT` 在真实 commit 调用前注入异常，因此只证明未成功提交的事务会回滚，不模拟断电、进程强杀、磁盘损坏或 WAL 持久性。Tool handler 的外部副作用也无法由本地 SQLite 回滚；当前默认工具为只读，未来外部写工具必须设计独立的幂等或 outbox/补偿协议。上述结论只适用于当前单机文件型 SQLite，不外推到 PostgreSQL 或分布式事务。

受限沙箱中的 build 因无法访问 PyPI 解析 `hatchling` 而失败；获批联网后仍使用 E 盘 uv cache 成功构建。全量 pytest 使用获批的沙箱外 E 盘 basetemp，避免已知的 session cleanup 权限假失败。该工作包完成时远程结果尚未产生，最终跨平台证据见第十二节。

## 11. M4.5 发布制品与本地部署证据

M4.5 于 2026-07-25 完成以下本地实现：

- 新增 `scripts/local_alpha_smoke.py`。编排器只依赖标准库，不导入工作区 `agent_factory`，所有构建、环境、数据库、日志和 uv cache 均显式位于 E 盘项目 `.tmp`。
- 每次运行在唯一目录构建全新 sdist/wheel，拒绝多个陈旧制品，并检查 archive 路径安全、metadata、`demo`/`llm` extras、`agent-factory-demo` entry point 和 001-006 migrations。
- minimal 环境从 `uv.lock` 安装基础依赖，再以 `--no-deps` 安装 wheel；隔离探针证明 `agent_factory` 来自该环境，且 `gradio`、`openai` 不存在。
- Uvicorn 从工作区外目录以 `python -I` 启动，只绑定随机 loopback 端口；已安装 SDK 完成 readiness 和 Prototype 注册。
- 第一个进程完成 application shutdown 后，第二个进程使用同一 SQLite 启动；SDK 读取到同一 Published Prototype，数据库 migration history 恰好为 1-6。
- optional 环境独立安装 `demo,llm` lock 依赖及 `wheel[demo,llm]`，验证 Gradio、官方 OpenAI SDK 和 console entry point。
- 两次 Uvicorn 日志均扫描随机原始 Token；成功 run 自动安全清理，失败 run 留在 E 盘用于诊断，进程始终有 timeout 与 kill 兜底。
- 新增 [`本地 Alpha 部署说明`](../deployment/local-alpha.md)，冻结单机、单 Uvicorn、单 SQLite、loopback-only 拓扑及公网部署阻断项。

定向证据：

| 门禁 | 可复现命令 | 结果 |
| --- | --- | --- |
| 脚本单元测试 | `uv run pytest -q tests/unit/scripts/test_local_alpha_smoke.py` | 26 项通过，0.49 秒 |
| 隔离制品 smoke | `uv run python -m scripts.local_alpha_smoke --work-root E:\Agent-Factory\.tmp\local-alpha-smoke --uv-cache-dir E:\Agent-Factory\.tmp\uv-cache` | 通过；wheel `1.0.0a1`，migration 1-6，Prototype 重启恢复 |
| 进程与临时目录 | 完成后检查 Python command line 与 work root | 无 Uvicorn 残留；成功 run 已清理 |
| Ruff format/check | `uv run ruff format --check src tests scripts`；`uv run ruff check src tests scripts` | 151 个文件通过 |
| mypy strict | `uv run mypy src tests scripts` | 151 个 source file 无问题 |
| 契约快照 | `uv run python -m scripts.contract_snapshots --check` | 三份 SHA-256 与 M4.2 基线一致 |
| 全量 pytest | `uv run pytest -q --cov --cov-report=term-missing` | 403 项通过，75.09 秒 |
| Domain/Application/全项目 branch coverage | 三档 `coverage report --fail-under` | 96% / 94% / 92%，通过 90% / 85% / 80% 门槛 |
| sdist/wheel | `uv --cache-dir E:\Agent-Factory\.tmp\uv-cache build` | `agent_factory-1.0.0a1` 构建成功；001-006 migration 存在，`scripts`/`tests` 未进入 wheel |

首次真实运行发现 Windows `CTRL_BREAK_EVENT` 在 Uvicorn 完整 application shutdown 后返回平台退出码 3。脚本没有放宽全部非零返回码，只在 Windows、返回码 3 且日志同时包含 `Application shutdown complete.` 与 `Finished server process` 时接受。第二次运行完成全部业务阶段后，成功清理暴露 `sqlite3.Connection` 上下文只提交/回滚但不关闭连接；修正为显式 `closing()`，并对唯一 `run-*` 子目录实施路径校验和有限清理重试。最终脚本完整重跑通过。

依赖构建和安装可能访问 package index；安装后的应用、SDK 和 import 探针只使用 loopback，不调用真实模型。该结果证明本地制品可安装、启动和恢复，不证明公网安全、多进程 SQLite、高可用或 disaster recovery。M4.6 已把真实进程 smoke 纳入最终 CI，最终跨平台结果记录在下一节。

## 12. M4.6 退出候选与远程证据

M4.6 于 2026-07-25 完成以下本地实现与验证：

- `source_package_resources()` 从当前 `src/agent_factory/**/*.py` 动态推导 wheel 路径；新增生产模块却未进入 wheel 时，release step 会失败。001-006 migrations、metadata、extras 与 entry point 继续显式校验。
- GitHub Actions 从 16 个步骤收敛为 14 个步骤，删除重复的内联 49 项清单和 extras Bash 安装，统一调用跨平台 `scripts.local_alpha_smoke`。
- 安全回归、事务故障回归和全量 pytest 仍保持独立步骤；它们虽有执行重叠，但保留了安全与事务不变量的直接可见失败位置。
- CI job timeout 从 15 分钟调整为 20 分钟；release step 仍限制单命令 240 秒、启动 30 秒和关闭 15 秒，不使用总 timeout 放宽掩盖挂起。
- 本地按 CI 顺序重新执行静态检查、快照、定向测试、全量覆盖率和统一 release step，未修改生产 `src/`。

本地退出候选门禁：

| 门禁 | 结果 |
| --- | --- |
| Ruff format/check | 151 个文件通过 |
| mypy strict | 151 个 source file 无问题 |
| 契约快照 | 三份 SHA-256 与 M4.2 基线一致 |
| Alpha 安全回归 | 12 项通过，3.11 秒 |
| 事务故障回归 | 34 项通过，10.16 秒 |
| 全量 pytest | 409 项通过，93.65 秒 |
| Domain/Application/全项目 branch coverage | 96% / 94% / 92%，通过 90% / 85% / 80% 门槛 |
| 统一 release step | fresh sdist/wheel、全部源码资源、minimal/extras、migration 1-6、两次 Uvicorn、SDK 重启恢复与 Token 扫描全部通过 |
| CI workflow | PyYAML 可解析，共 14 个步骤，job timeout 20 分钟 |

首次完整推送后的提交 `3590775` 触发 [`CI #23`](https://github.com/1471436961/agent-factory/actions/runs/30147162529)，Ubuntu mypy 发现 `subprocess.CREATE_NEW_PROCESS_GROUP` 与 `signal.CTRL_BREAK_EVENT` 两个 Windows 专属属性不在 Linux typeshed 中。修复提交 `bf9d695` 通过运行时 `getattr()` 和整数类型校验读取平台常量，没有增加全局 ignore；随后 [`CI #24`](https://github.com/1471436961/agent-factory/actions/runs/30147589651) 的 type check、快照、安全、事务、全量测试和覆盖率均通过，但 release step 暴露 POSIX Uvicorn 完整关闭后返回 `-SIGTERM`。

Uvicorn `0.51.0` 在 lifespan shutdown 后恢复原 signal handler，并重新抛出捕获的终止信号。提交 `4a55d73` 只在 POSIX 返回 `-SIGTERM` 且日志同时包含 `Application shutdown complete.` 与 `Finished server process` 时接受该路径；Windows 仍只接受相同日志条件下的退出码 3，其他非零结果继续失败。该提交的 [`CI #25`](https://github.com/1471436961/agent-factory/actions/runs/30148036514) 全部通过，证明统一 release step 能在干净 Ubuntu runner 完成构建、隔离安装、真实 Uvicorn 重启和 SDK/SQLite 恢复。两次失败没有通过降低 mypy strict、删除断言、放宽任意非零退出码或跳过 release step 规避。

## 13. 阶段结论

M4.1-M4.6 已建立范围基线、契约快照、安全回归、事务故障、隔离制品和跨平台 CI 证据；最终候选通过 409 项本地测试、三档覆盖率、Windows release smoke 与 Ubuntu CI #25。项目 owner 于 2026-07-25 确认结束并封存 M4。

该结论只证明当前本地 Alpha 在声明的单机、单 Uvicorn、文件型 SQLite、loopback 和固定只读工具边界内具备可回归的工程证据；不证明 OIDC/TLS/WAF、多租户、PostgreSQL、多进程/分布式运行时、不可信代码沙箱、真实 LLM 质量或公网生产部署能力。
