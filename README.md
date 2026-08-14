# rag

`rag` 是对 [`haonanhu02-jpg/ragflow-agent`](https://github.com/haonanhu02-jpg/ragflow-agent)
进行绿地重构的新项目。

## 不可变约束

- 旧仓库只作为能力、行为和兼容性参考，不作为新仓库的代码基底。
- 新仓库具有独立 Git 历史、独立包名和独立目录结构。
- 禁止整体复制旧仓库、保留旧目录后逐步替换，或在生产代码中依赖旧项目。
- 旧代码只有通过逐文件复用评审后才能进入新仓库，并必须登记来源、理由、改造和测试。
- 每项现有能力都必须通过兼容测试重新取得证据；旧项目的“已完成”状态不会自动继承。
- LangChain/LangGraph 只承担其擅长的框架职责，领域语义、安全、数据一致性和检索质量仍由本项目负责。

## 当前状态

R0–R4 已完成。新项目可独立处理 PDF、DOCX、PPTX、XLSX、TXT、Markdown、HTML 与图片，
统一保留 Block 结构、页码、坐标、顺序、解析告警和媒体来源，并提供九类稳定 Chunk Method、
Embedding、pgvector 索引、Elasticsearch BM25/kNN 混合召回、RRF、Rerank、递归元数据过滤、
PostgreSQL 最终授权校验、有限空结果回退、非流式回答、Citation 和完整 Retrieval Trace。
CAP-35 当前只完成多模态解析基础；完整多模态 RAG 仍在 R8。

## 文档

- [目标架构](docs/architecture.md)
- [R0–R10 实施路线图](docs/implementation-roadmap.md)
- [兼容测试与代码复用规则](docs/compatibility-and-reuse.md)
- [R0 执行记录](docs/phases/r0-compatibility-baseline.md)
- [R1 执行记录](docs/phases/r1-domain-foundation.md)
- [R2 执行记录](docs/phases/r2-minimum-rag.md)
- [R3 执行记录](docs/phases/r3-document-compiler.md)
- [R4 执行记录](docs/phases/r4-authorized-retrieval.md)
- [风险登记](docs/risks.md)
- [ADR-001：采用绿地仓库](docs/adr/ADR-001-greenfield-repository.md)
- [ADR-002：LangChain/LangGraph 职责边界](docs/adr/ADR-002-framework-responsibilities.md)

## 仓库关系

| 仓库 | 角色 | 允许的关系 |
|---|---|---|
| `haonanhu02-jpg/ragflow-agent` | 旧项目、行为参考、兼容性 oracle | R0 固定 commit；测试只通过公开 Interface 或独立测试驱动调用 |
| `haonanhu02-jpg/rag` | 全新实现 | 不依赖旧包；只接收通过复用评审的少量代码 |
| `haonanhu02-jpg/lang` | 先前误建的渐进迁移仓库 | 不作为本项目实现来源或阶段状态依据 |
