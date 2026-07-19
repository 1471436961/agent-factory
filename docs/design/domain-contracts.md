# M1.1 Domain Contracts Design Note

## 1. 解决的问题

M1 需要把 Agent 定义、原型来源、知识版本和导出规格保存为可重放快照。M1.1 只建立领域契约和纯校验逻辑，不读写数据库，不创建 HTTP 路由，也不调用 LLM。

完成条件是：相同输入得到相同 canonical JSON 和 checksum，领域对象创建后无法通过公开容器修改内容，非法输入在进入 controller 或 repository 之前失败。

## 2. 依赖方向

```text
application/commands.py
          |
          v
domain/models.py -----> domain/enums.py
       |  |                    |
       |  +--------------------+
       v
domain/common.py <----- domain/validation.py

domain/errors.py -----> domain/common.py
```

- `domain` 不导入 FastAPI、SQLite、SQLAlchemy 或模型 SDK。
- `application.commands` 可以引用 domain model，domain 不反向引用 application。
- repository 与 controller 在 M1.2/M1.3 实现，只消费本阶段建立的契约。

## 3. 不可变 JSON

Pydantic `frozen=True` 是浅层冻结。`FrozenJsonObject` 在输入边界递归执行：

- mapping 转为只读 mapping；
- list/tuple 转为 tuple；
- `str`/`int`/`float`/`bool`/`None` 保留；
- `NaN`、正负无穷和其他 Python 对象被拒绝。

Pydantic 序列化器将冻结 mapping/tuple 还原为标准 JSON object/array。这使内存对象不可原地修改，同时保持 SQLite JSON 快照和 REST 输出的兼容性。

## 4. Canonical JSON 与 checksum

canonical JSON 固定使用：

- UTF-8；
- `ensure_ascii=False`；
- `sort_keys=True`；
- `separators=(",", ":")`；
- `allow_nan=False`。

`sha256_model()` 先执行 `model_dump(mode="json")`，再对 canonical bytes 计算 SHA-256。`checksum_knowledge_content()` 对字符串使用 UTF-8 原字节，对 JSON object 使用上述 canonical 规则。

## 5. 校验边界

M1.1 负责：

- 字段格式、长度、集合唯一性和模型内跨字段不变量；
- SemVer 解析与区间自洽性；
- JSON Schema Draft 2020-12 结构合法性；
- canonical JSON 与 checksum 纯函数。

M1.1 不负责：

- 原型/知识版本唯一性，这需要 repository 和数据库约束；
- 知识选择与某个实例所有槽位的组合校验，这由 M1.3 policy/controller 执行；
- 原型状态迁移、幂等重放和 revision compare-and-swap，这些需要 M1.2/M1.3 事务边界；
- 领域错误到 HTTP status 的映射，这属于 M1.4。

## 6. 验证方法

- 单元测试验证冻结容器无法原地修改，但 `model_dump(mode="json")` 仍生成普通 JSON 容器。
- 固定输入的 canonical bytes 和 checksum 使用确切断言，不只断言长度。
- Pydantic 模型同时覆盖有效样例、边界值、跨字段失败和 extra field 拒绝。
- M1.1 验收执行 Ruff format/lint、mypy strict、pytest 分支覆盖率和 wheel/sdist 构建。
