---
document_id: RAG-GREENFIELD-IMPLEMENTATION-ROADMAP
version: "2.0.0"
status: accepted_for_implementation
last_updated_at: "2026-08-13"
---

# `rag` R0–R10 绿地实施路线图

## 1. 实施口径

本路线图从零实现新项目，不把旧仓库复制为起点，也不继承旧仓库或误建 `lang` 仓库的阶段状态。

- `R0–R10` 是新仓库自己的阶段编号。
- 每阶段同时交付 Implementation、测试、数据迁移/契约（如有）、文档和机器报告。
- 兼容目标以 R0 固定的旧仓库 commit、CAP-01～CAP-43 和公开行为为准。
- 允许内部类型、包结构、数据库表和执行流程不同；外部行为变化必须明确分类并批准。
- 每阶段必须先通过本阶段门禁，再开始下一阶段；不能用后续工作承诺替代当前验收。
- `experimental/off/deferred` 也是需要保留的行为，不得在无真实证据时标记为生产能力。

## 2. 总体阶段

| 阶段 | 目标 | 主要能力 | 结束时可运行状态 |
|---|---|---|---|
| R0 | 冻结兼容基线，建立全新工程 | CAP 台账、旧 commit、黑盒驱动、CI、依赖门禁 | 空业务骨架可测试，不宣称业务兼容 |
| R1 | 建立领域核心与基础 Adapter | 安全上下文、领域模型、ModelRuntime、配置、PostgreSQL 基础 | 新项目可启动，领域不依赖框架 |
| R2 | 建立最小端到端知识路径 | TXT/MD、General Chunk、Embedding、索引、固定 RAG | 第一条独立可运行 RAG 竖切 |
| R3 | 完成文档编译能力 | 多格式、OCR、统一 Block、九类 Chunk、媒体来源 | 文档输入兼容达到完整基线 |
| R4 | 完成授权混合检索 | Query 变换、BM25/KNN、RRF、Rerank、过滤、Trace | 统一检索核心可供 RAG/Tool 调用 |
| R5 | 完成证据问答 | 流式、no-evidence、Citation、模型治理 | 固定 RAG 完整兼容 |
| R6 | 完成可靠摄取和生命周期 | Outbox、版本、发布、回滚、删除、恢复、重建、对账 | 跨存储生命周期完整兼容 |
| R7 | 完成 AgentGraph | Tool、Checkpoint、HITL、Memory、预算、安全 SQL/HTTP | 单 Agent 能力完整兼容 |
| R8 | 完成高级知识能力实验实现 | enrich、GraphRAG、RAPTOR、多模态、时序 | 保持默认关闭并有确定性/真实增益门禁 |
| R9 | 完成生产工程能力 | 可观测、安全、部署、备份恢复、性能、供应链 | 候选版本满足自有云运行门禁 |
| R10 | 完成全能力兼容审计与发布 | CAP-01～CAP-43 总验收、数据迁移工具、演练 | 给出可发布/不可发布机器结论 |

## 3. R0：兼容基线与绿地工程

### 目标

建立“新项目没有旧代码，但能客观对照旧行为”的基础。R0 不实现业务能力。

### 工作

1. 固定旧仓库 URL 和完整 commit SHA，生成 `reference-lock.json`。
2. 将 CAP-01～CAP-43 转换为机器可读 `capabilities.yaml`，记录旧状态、输入、输出、不变量、
   错误、权限、性能证据和目标阶段。
3. 采集旧项目公开 OpenAPI、领域状态、黄金解析样本、检索结果、Citation、Trace 和 Agent 场景；
   敏感或受版权限制的数据只保存生成器和摘要 hash。
4. 建立 `LegacyDriver` 与 `NewDriver` 测试 Interface。Legacy Driver 在隔离进程/容器运行固定旧
   commit；生产代码不得导入它。
5. 定义比较器：精确、集合、排序容差、数值容差、语义质量、状态机轨迹和安全负向结果。
6. 初始化 `pyproject.toml`、`src/rag_platform`、`tests`、`docs`、CI、pre-commit、Ruff、strict
   mypy、pytest、依赖漏洞和 secret scan。
7. 添加依赖门禁：禁止生产代码导入旧包；禁止 Domain 导入 LangChain、LangGraph、FastAPI、
   ORM 和基础设施 SDK；禁止未经登记的 copied-source header。
8. 建立 ADR、风险、复用登记和阶段报告模板。

### 交付物

- 兼容台账、旧 commit 锁、OpenAPI/黄金数据快照、黑盒驱动和比较器。
- 全新项目最小包和 CI；仓库历史中没有旧项目提交。
- `reports/r0/baseline.json`，明确所有业务 CAP 均为 `not_implemented_in_new_repo`。

### 出口门禁

- CAP-01～CAP-43 连续且每项至少一个可执行验收场景。
- 相同输入可重复运行旧 Driver；测试不依赖旧项目工作区的未提交状态。
- 新项目生产依赖图不含旧仓库。
- CI 在 Windows/Linux 的文本规范化与版本化数据原始字节校验均稳定。

## 4. R1：领域核心、ModelRuntime 与基础设施骨架

### 目标

在没有业务大图的情况下建立稳定领域语言、Deep Module Interface、可信安全上下文和可替换
Adapter。

### 工作

1. 定义 tenant、KnowledgeBase、Document、DocumentVersion、Block、Chunk、IndexVersion、
   RetrievalCandidate、Citation、Trace、Job、Operation、AgentRun 等领域类型和不变量。
2. 定义可信 `AuthorizationContext`；请求体、模型和 Tool 参数不能提供 tenant/角色/ACL。
3. 建立 `CorePolicies`：资源范围、可见性、错误分类、预算、敏感字段和 fail-closed 规则。
4. 建立 `ModelRuntime` Interface 及 Fake、LangChain Adapter；支持 Chat、Embedding、Reranker
   的结构化调用、超时、重试、Token/成本记录，但暂不宣称真实质量。
5. 建立 PostgreSQL Repository Interface、事务 Interface、对象、搜索、队列和 Clock/ID Adapter
   Interface；仅在确有 Fake 与真实 Adapter 两种实现时保留 Seam。
6. 创建 API、Worker 和 Maintainer 三个 composition root；建立健康检查和配置校验。
7. 创建首个 Alembic 迁移：tenant/KB/文档身份、模型注册元数据和审计基础表。
8. 建立 contract fake、架构依赖测试、迁移往返和跨 tenant 负向测试。

### 覆盖能力

CAP-36、CAP-37、CAP-41 的基础；其余阶段共享领域契约。

### 出口门禁

- 三个进程入口可独立启动并执行 `--check`。
- Domain 零框架依赖；LangChain 类型仅存在于 Adapter。
- Fake 与至少一个真实 PostgreSQL Adapter 通过同一 Interface 契约测试。
- tenant/actor/role 不可由非可信输入覆盖。

## 5. R2：最小独立 RAG 竖切

### 目标

先证明新架构能够独立完成最小知识闭环，再扩展格式和算法。

### 工作

1. 实现 KnowledgeBase 创建、TXT/MD 上传、对象存储、DocumentVersion 和 IngestionJob。
2. 实现 `DocumentCompiler` 最小路径：纯文本 Block、General Chunk、稳定 Chunk ID、来源位置。
3. 建立 `IngestionGraph` 最小流程：加载、编译、Embedding、写候选 generation、校验、发布。
4. 实现向量检索最小 Adapter，并强制 tenant/KB/活动版本过滤。
5. 实现 `GroundedRag` 非流式路径、结构化 `no_evidence`、基础 Citation 和 Retrieval Trace。
6. 建立 API：KB、上传、Job 查询、固定 RAG；错误 Schema 在后续阶段保持兼容。
7. 使用 Fake 模型完成确定性 E2E，并使用一个隔离真实后端组合验证。

### 覆盖能力

CAP-03、CAP-04、CAP-08、CAP-10、CAP-16、CAP-21～CAP-23、CAP-27、CAP-38 的最小子集。

### 出口门禁

- 新项目不启动旧项目即可完成“上传→摄取→查询→回答→Citation”。
- 跨 tenant、错误 KB、未发布版本和已删除状态全部零泄露。
- 同一文件重复投递不会产生不同稳定 Chunk 或重复可见版本。
- R2 所涉旧行为场景通过；未覆盖能力仍明确为未实现。

## 6. R3：完整 DocumentCompiler

### 目标

完成旧项目支持的文档输入、结构和 Chunk 行为，同时让所有 Parser 归一到一个深 Interface。

### 工作

1. 支持 PDF、DOCX、PPTX、XLSX、TXT、MD、HTML、图片；先评估 LangChain Loader，只有输出
   满足页码、bbox、顺序和资源限制才采用。
2. 实现 OCR Adapter、扫描 PDF fallback、语言包校验、像素/页数/时间/内存限制。
3. 完成 `ParsedDocument/ParsedBlock`：paragraph、heading、table、image、list、code，保留
   page、bbox、层级、source order、parser version、warning 和媒体引用。
4. 完成 General、Paper、Book、Manual、Laws、QA、Table、Resume、Picture 九类 Chunk；
   Audio/Email 仅在兼容基线确认属于当前能力后加入，不因旧规划描述自动扩展。
5. 实现 MIME/扩展/内容嗅探路由、压缩炸弹和恶意文档防御。
6. 为每种格式和策略建立 golden、property、fuzz、资源和错误兼容测试。

### 覆盖能力

CAP-01～CAP-04、CAP-35 的解析基础。

### 出口门禁

- 所有格式通过同一个 `DocumentCompiler` Interface；调用方不知道具体 Parser。
- 黄金样本的结构、顺序、来源定位和稳定 Chunk ID 达到 R0 兼容阈值。
- 单文件资源上限和失败错误稳定；不产生半发布索引。

## 7. R4：AuthorizedRetrieval

### 目标

建立固定 RAG 与 Agent Tool 唯一共享的自研检索核心。

### 工作

1. 实现 query normalization、对话独立问题、可选改写、跨语言和关键词变体；所有模型输出
   通过 Schema，失败回 canonical query。
2. 定义递归 Filter AST；分离用户过滤、模型推断过滤和不可放宽的系统硬过滤。
3. 实现 Elasticsearch/OpenSearch BM25 与 KNN Adapter；保存原始分数和通道排名。
4. 实现并行召回、chunk ID 去重、RRF、Reranker Adapter、阈值、TopK/TopN、每文档限额。
5. 实现有限空结果策略；只能放宽软变体，永不放宽 tenant、ACL、KB、活动版本和删除状态。
6. 最终返回前使用 PostgreSQL 权威状态防御删除/切版竞态。
7. 实现内容最小化 Retrieval Trace、TTL、角色读取和写失败非阻断指标。
8. 建立 Recall@K、MRR、NDCG、延迟、降级、注入和跨租户兼容评测。

### 覆盖能力

CAP-09～CAP-20、CAP-22。

### 出口门禁

- `GroundedRag` 和未来 `KnowledgeBaseTool` 只能调用同一个 `AuthorizedRetrieval` Interface。
- 混合检索相对单路基线不退化，并达到 R0 设定的兼容/质量阈值。
- 所有安全负向场景零泄露；依赖故障不能伪装为无结果。
- 每个候选可解释其 BM25/KNN/RRF/Rerank/最终状态。

## 8. R5：GroundedRag 与 Citation

### 目标

完成固定 RAG 的答案契约、证据判定、流式输出和引用完整性。

### 工作

1. 实现 evidence package、Prompt 组装、模型调用、流式事件和答案 Schema。
2. 实现 `sufficient/partial_evidence/no_evidence/conflicting_evidence` Policy；无证据时拒答，
   模型不得自行改变结果状态。
3. Citation 绑定 tenant、KB、DocumentVersion、Chunk、page、bbox、quote、source 与 trace ID。
4. 回答返回前验证 Citation 存在、授权、活动/保留版本状态和 quote 对齐。
5. 支持模型降级、超时、取消、Token/费用上限与流式中断；错误与 no-evidence 分离。
6. 建立忠实度、Citation precision/recall、拒答、流式序列和旧 API 兼容测试。

### 覆盖能力

CAP-17、CAP-21、CAP-27、CAP-36、CAP-37。

### 出口门禁

- 固定 RAG 全部 R0 黑盒场景通过或有批准的改进差异。
- Citation 不能引用未授权、未发布、已删除或不存在的内容。
- Fake 测试与真实模型评测分开报告；Fake 结果不能作为质量结论。

## 9. R6：可靠摄取与 LifecycleCoordinator

### 目标

完成文档从上传到更新、回滚、删除和索引重建的全生命周期一致性。

### 工作

1. 完成 IngestionJob/Task、事务 Outbox、tenant envelope、确定性 message ID、有限重试、
   transient/permanent/cancelled 分类、dead-letter、进度和批量聚合。
2. 实现不可变 DocumentVersion、候选 generation、完整性验证、alias/读写路由切换和
   PostgreSQL CAS/fencing 激活；新版本失败不影响旧活动版本。
3. 实现 update、reparse、rollback 和 superseded 保留窗口。
4. 实现 tombstone：删除意图提交后立即不可检索；保留期 restore；到期后幂等物理 purge。
5. 实现全量/KB 索引重建、验证、原子切换、旧 generation 保留与回滚。
6. 实现 reconciliation：stale operation、未投递 Outbox、孤儿对象/投影、过期 purge；默认
   dry-run，只自动修复可证明安全的情况。
7. 建立 Worker kill、重复消息、网络失败、部分写入、并发更新、取消竞态和迁移往返测试。

### 覆盖能力

CAP-23～CAP-26、CAP-38，以及所有检索/Citation 的版本与删除防御。

### 出口门禁

- 任意单点故障后旧活动版本继续服务，且对账能定位未完成副作用。
- 任一业务命令重复执行均为幂等或明确冲突。
- 删除意图提交后没有搜索、Citation、缓存或 Agent 路径可以恢复可见性。
- 四基础设施集成、并发和故障注入测试通过。

## 10. R7：AgentGraph 与受治理 Tool

### 目标

使用 LangGraph 实现单 Agent 的可恢复编排，并将安全、授权和预算保留在自研 Policy 中。

### 工作

1. 定义版本化 Agent State，只保存恢复所需的最小数据和业务 ID。
2. 实现简单问题直达 `GroundedRag`，复杂问题进入 planner/tool/evidence 循环；检索最多三轮，
   所有循环、模型、Tool、Token、时间和费用均有服务端预算。
3. 用 LangChain Tool 包装 `AuthorizedRetrieval`，实现 `KnowledgeBaseTool`；模型不能传 tenant/ACL。
4. 实现显式 Tool Registry、Schema、风险等级、授权、超时、重试、输出限额、脱敏和审计。
5. 实现只读 SQL Tool：AST allowlist、单语句、tenant 条件注入与数据库权限双保险。
6. 实现 HTTP Tool：固定 base URL/path/method、禁止重定向、DNS/IP/SSRF 防护和密钥注入。
7. 使用 LangGraph PostgreSQL Checkpoint；实现 thread/run tenant 绑定、State 版本迁移、失败恢复。
8. 使用 interrupt/resume 实现 HITL；独立业务审批表记录 TTL、actor、Tool/version/参数摘要，恢复时
   重新检查授权、Policy、资源和预算，副作用幂等。
9. Memory 默认关闭；显式 consent 后只保存允许的稳定事实，Checkpoint/Trace/Memory 分离。

### 覆盖能力

CAP-28～CAP-32。其中 CAP-32 多 Agent 保持 deferred，除非本阶段评测证明单 Agent 无法满足明确需求。

### 出口门禁

- 进程重启、节点异常、重复 resume 不导致重复副作用。
- Direct RAG 与 KB Tool 在相同范围和输入下得到等价检索结果。
- 未注册 Tool、越权、SQL 写入、任意 URL、过期审批和跨 tenant resume 全部失败关闭。
- Agent 评测达到成功率、证据、Citation、预算和安全阈值；多 Agent 不被虚假标记为完成。

## 11. R8：AdvancedKnowledgeBuilder

### 目标

重新实现旧项目高级知识能力，但严格保持实验属性，只有真实增益证据才能默认启用。

### 工作

1. Chunk 关键词、问题和摘要：结构化输出、去重、忠实度、成本和失败降级。
2. 确定性 TOC、父子 Chunk 与层级来源；实现受预算和收敛限制的 RAPTOR。
3. GraphRAG：实体、关系、社区、provenance、版本绑定、幂等构建、取消与恢复。
4. 多模态：图片/图表/音频派生物、Vision/ASR Adapter、跨模态来源与 Citation；不自动扩展视频。
5. 时序 RAG：UTC 事件、乱序/缺失、窗口、趋势、相似历史、文本融合和时间 Citation。
6. 使用 `AdvancedBuildGraph` 处理分支、长时构建和恢复；算法与数据模型仍位于 Module。
7. 建立确定性协议测试、真实 Provider/数据对照评测、成本上限、版本/删除和 tenant 安全测试。

### 覆盖能力

CAP-05～CAP-07、CAP-33～CAP-35、CAP-43。

### 出口门禁

- 每项能力有可重复构建、来源链、取消、重建和清理测试。
- 未证明相对普通检索/RAG 有真实增益的能力继续 `experimental_off/no_go`。
- CAP-35 仅对已接入且真实验证的媒体类型声明支持。

## 12. R9：生产工程、安全与可观测

### 目标

让已兼容的业务能力具备可部署、可观察、可恢复和可运营的工程条件。

### 工作

1. 统一 request/job/run/retrieval/model/tool correlation；JSON 日志、OpenTelemetry、Prometheus
   指标、Dashboard 和告警；禁止完整原文、Prompt、密钥和高风险 Tool 输出进入 Trace。
2. 完成认证 Adapter、限流、CORS/TLS、secret provider、依赖锁、SBOM、镜像签名、漏洞扫描、
   non-root 镜像和默认拒绝网络策略。
3. API、Worker、Maintainer 同一制品不同入口；独立健康检查、扩缩容和优雅停止。
4. Compose 作为自有云基线；Kubernetes/Helm 只有明确部署需求时再实现，不伪装为已验证。
5. 数据库、对象、搜索 generation 和配置的备份/恢复手册与自动校验；定义 RPO/RTO 目标。
6. 建立容量、并发、长时 Worker、网络分区、磁盘/队列积压和恢复演练。
7. 完成评测 CI：检索、答案、Citation、Agent、安全、性能和高级能力门禁。

### 覆盖能力

CAP-39～CAP-42，以及所有能力的运行期门禁。

### 出口门禁

- 全新环境可一次性迁移并启动；API/Worker 可独立伸缩和退出。
- 备份恢复后以内容 hash、业务计数、索引 generation 和抽样查询校验一致性。
- 安全负向、供应链、性能和故障演练无阻断问题。
- 未实际运营验证的月度 SLO、企业 IdP、复杂 RBAC、ARM64/Kubernetes 明确保持限制。

## 13. R10：全能力兼容审计与发布

### 目标

对新项目做最终能力、数据、安全和运行审查，生成可机器判断的发布结论。

### 工作

1. 在隔离环境运行全部 CAP-01～CAP-43 旧 Driver 与 New Driver，生成逐场景 diff。
2. 将差异分类为：`equivalent`、`approved_improvement`、`preserved_limitation`、
   `regression`、`not_comparable`；后两类默认阻断。
3. 核验 OpenAPI/SDK、错误、状态机、Citation、Trace、权限和数据保留兼容。
4. 构建旧数据导出/新数据导入工具；不让新运行时读取旧数据库 Schema。迁移支持 dry-run、
   校验、幂等重放和回滚到旧系统，但不在应用代码中保留双运行分支。
5. 演练“冻结旧写入→最终导出→导入→重建投影→验收→切换入口”；旧系统保持只读回滚窗口。
6. 生成 SBOM、镜像 digest、迁移 head、数据集版本、模型版本、评测阈值和已知限制清单。
7. 发布 `reports/r10/release-report.json`，只有零未批准 regression 才允许 `go`。

### 覆盖能力

CAP-01～CAP-43 全量。

### 出口门禁

- 每项能力有最终状态和可定位证据；不得用“总体测试通过”代替逐 CAP 验收。
- 已实现的旧能力零未批准回归；实验和 deferred 状态被如实保留。
- 数据迁移演练通过并可回滚；新项目生产运行不依赖旧项目。
- 发布结论由机器报告给出，人工只能批准有记录的兼容差异，不能绕过安全门禁。

## 14. 能力到阶段映射

| 能力 | 主阶段 | 最终复验 |
|---|---|---|
| CAP-01～CAP-04 文档解析、结构、Chunk | R3 | R10 |
| CAP-05～CAP-07 enrich、摘要、TOC | R8 | R10 |
| CAP-08 Embedding/索引 | R2，R6 完善 | R10 |
| CAP-09～CAP-20 查询与检索 | R4 | R10 |
| CAP-21 Citation | R2 基础，R5 完整 | R10 |
| CAP-22 Retrieval Trace | R2 基础，R4 完整 | R9、R10 |
| CAP-23～CAP-26 摄取与生命周期 | R2 基础，R6 完整 | R10 |
| CAP-27 固定 RAG | R2 基础，R5 完整 | R10 |
| CAP-28～CAP-31 Agent/Tool/恢复/HITL | R7 | R10 |
| CAP-32 多 Agent | R7 评估，默认 deferred | R10 |
| CAP-33～CAP-35 高级知识与多模态 | R8，默认实验 | R10 |
| CAP-36 模型注册与调用 | R1 基础，R5/R7 完善 | R9、R10 |
| CAP-37 FastAPI | R1 基础，逐阶段扩展 | R10 |
| CAP-38 Worker | R2 基础，R6 完整 | R9、R10 |
| CAP-39～CAP-42 评测、观测、安全、部署 | 全程基础，R9 完整 | R10 |
| CAP-43 时序 RAG | R8，默认实验 | R10 |

## 15. 每阶段统一完成定义

任何阶段只有同时满足以下条件才可标记 `completed`：

1. 生产 Implementation、数据库迁移和配置已提交，默认值安全。
2. Interface contract、单元、集成、兼容、安全和所需 E2E 测试通过。
3. 使用真实基础设施的能力已在隔离环境验证；使用 Fake 的结果单独标识。
4. OpenAPI、领域状态、数据保留、错误、权限和回滚文档已更新。
5. 机器报告记录 commit、依赖锁、迁移 head、数据集、通过/跳过/失败和已知限制。
6. 跳过测试逐项解释，不能把 skip 算作验证通过。
7. 该阶段覆盖 CAP 的状态已更新；没有证据的能力不标记完成。
