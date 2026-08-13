---
document_id: RAG-R0-COMPATIBILITY-BASELINE
status: completed
last_updated_at: "2026-08-13"
reference_commit: "ce74c58e664275ff239bcbb8c841771bc4649557"
---

# R0：兼容基线与绿地工程

## 1. 阶段目标

冻结旧项目能力与行为参考，建立全新的 Python 工程、兼容 Harness、质量门禁和机器报告。
R0 不实现任何业务 RAG 能力，也不把旧项目完成状态继承到新项目。

## 2. 任务状态

| 任务 | 内容 | 状态 |
|---|---|---|
| R0-T01 | 固定旧仓库 URL、commit、tree、OpenAPI、迁移和测试证据 | 已完成 |
| R0-T02 | 建立 CAP-01～CAP-43 机器台账与契约 Profile | 已完成 |
| R0-T03 | 为每项 CAP 建立可执行 R0 基线场景 | 已完成 |
| R0-T04 | 建立隔离 LegacyDriver 与 R0 NewDriver | 已完成 |
| R0-T05 | 建立 exact/structured/set/ranking/numeric/semantic/state/security 比较器 | 已完成 |
| R0-T06 | 初始化 `rag-platform` 工程、质量配置与 CI | 已完成 |
| R0-T07 | 建立依赖方向、旧运行时依赖和复用登记门禁 | 已完成 |
| R0-T08 | 生成机器报告并执行 R0 出口审查 | 已完成 |

## 3. 固定参考

| 项目 | 值 |
|---|---|
| 旧仓库 | `https://github.com/haonanhu02-jpg/ragflow-agent` |
| commit | `ce74c58e664275ff239bcbb8c841771bc4649557` |
| Git tree | `862b341cb6ad06796eea555c9e16007256a30695` |
| OpenAPI | 22 paths；SHA-256 `3b8a5db4ae03c889cc87c4c535e3aaaf1bc5801bfec1e437a3ce95219eb2e459` |
| Alembic head | `20260801_0006` |
| 旧测试清单 | 108 个 `test_*.py` 文件 |

完整锁见 [`baselines/r0/reference-lock.json`](../../baselines/r0/reference-lock.json)。旧参考必须从该
commit 的 detached checkout 或隔离容器运行，并要求工作树干净。用户日常旧仓库工作区即使存在
未提交改动，也不会影响基线。

公开 OpenAPI 已捕获到 [`baselines/r0/openapi.json`](../../baselines/r0/openapi.json)；黄金解析、
检索/答案/Citation/Agent 评测和历史机器报告保留在旧仓库，仅在
[`baselines/r0/evidence-manifest.json`](../../baselines/r0/evidence-manifest.json) 固定 Git 对象
hash，避免把旧项目测试树整体复制到新仓库。

## 4. 能力事实

- CAP-01～CAP-43 共 43 项，连续且无重复。
- 旧参考：35 项 `implemented`、6 项 `experimental_off`、1 项 `mixed`、1 项 `deferred`。
- 新项目：43 项全部为 `not_implemented_in_new_repo`。
- 43 项均有一个可执行的 R0 evidence probe；它从固定 commit 读取并哈希唯一能力矩阵行。
- 目标阶段负责将 evidence probe 深化为具体黑盒输入/输出场景，R10 执行全量最终对照。

机器台账见 [`baselines/r0/capabilities.yaml`](../../baselines/r0/capabilities.yaml)，场景见
[`baselines/r0/scenarios.jsonl`](../../baselines/r0/scenarios.jsonl)。

## 5. 兼容 Harness

`Driver` Interface 只交换版本化 JSON，不暴露旧或新项目内部类型：

```text
DriverRequest(capability_id, scenario_id, payload)
  -> isolated LegacyDriver or NewDriver
  -> DriverResult(status, output, error)
  -> capability comparator
  -> equivalent / regression / not_comparable
```

R0 的 NewDriver 必须返回 `not_implemented_in_new_repo`，比较器必须将其分类为
`not_comparable`，不能因为 Harness 本身工作正常就宣称业务兼容。

已实现比较器：

- `exact`：状态、错误、确定性 ID 和精确结果。
- `structured`：旧必需结构存在，允许新项目增加字段。
- `set`：无序候选或派生物集合。
- `ranking`：TopK 重叠与排序阈值。
- `numeric`：分数、延迟、Token 和费用容差。
- `semantic`：真实评测器得分达到旧基线阈值。
- `state_sequence`：摄取、生命周期和 Agent 状态轨迹。
- `security_negative`：旧系统拒绝的请求，新系统必须同样拒绝或更严格。

## 6. 绿地门禁

CI 和本地脚本强制：

1. `src/rag_platform` 禁止导入 `ragflow_agent`。
2. Domain 禁止导入 LangChain、LangGraph、FastAPI、Pydantic、ORM 和基础设施 SDK。
3. R0 阶段生产代码也禁止导入 LangChain/LangGraph；框架 Adapter 从 R1 开始引入。
4. 生产源码出现 copied/adapted/derived marker 时，目标文件必须在复用登记中。
5. 文本基线统一使用 UTF-8/LF 规范化 SHA；未来版本化数据集继续使用原始字节 hash。
6. 新仓库不包含旧目录、submodule、runtime fallback 或 legacy 路由。

## 7. 阶段出口

| 检查 | 结果 |
|---|---|
| `uv lock --check` | 通过；43 个包可重复解析 |
| Ruff | 通过 |
| strict mypy | 通过；26 个源文件无问题 |
| Pytest/coverage | 通过；57 passed、0 skipped；总覆盖率 97% |
| Architecture/greenfield gate | 通过；无旧运行时或框架越层依赖 |
| R0 baseline | 通过；43 项能力、43 个场景 |
| Dependency/secret scan | 通过；无已知依赖漏洞、无密钥命中 |
| 43 个旧参考探针两次重复执行 | 通过；固定 detached checkout 结果一致 |

机器报告见 [`reports/r0/baseline.json`](../../reports/r0/baseline.json)。旧参考能力状态只是证据上下文；
新仓库 43 项业务能力仍全部为 `not_implemented_in_new_repo`，R1 尚未开始。
