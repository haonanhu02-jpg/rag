---
document_id: RAG-R2-MINIMUM-RAG
status: completed
last_updated_at: "2026-08-13"
---

# R2：最小独立 RAG 竖切

## 交付

- KB 创建；UTF-8 TXT/Markdown 上传；tenant 隔离文件对象存储；幂等 Job/DocumentVersion。
- 纯文本 Block、General Chunk、稳定 Chunk ID、字符和行号来源。
- LangGraph `IngestionGraph`：load → compile → embed → stage → validate → publish。
- PostgreSQL + pgvector 候选 generation；发布时原子切换；向量检索硬过滤 tenant、KB、活动
  generation 和活动 DocumentVersion。
- 非流式 `GroundedRag`、结构化 `no_evidence`、版本绑定 Citation、基础 Retrieval Trace。
- 旧公开路径兼容：KB、上传、Job、固定 RAG、Trace；worker 支持一次执行一个 pending Job。

## 验收

- Fake 模型 + 真实 PostgreSQL/pgvector：上传 → 摄取 → 查询 → 回答 → Citation。
- 跨 tenant、错误 KB、未发布版本、deleted 版本均不泄露。
- 同幂等键或相同文件重复投递复用 Job/Version，稳定 Chunk 不重复。
- 旧系统 R2 行为依据固定到 `baselines/r2/legacy-behavior-evidence.json`；未复制旧代码。

## 明确留到后续

R2 只取得十项能力的最小子集证据。完整多格式和九类 Chunk 在 R3；混合检索和完整 ACL/Trace
在 R4；完整 GroundedRag/Citation/流式在 R5；队列、Outbox、重试取消和生命周期在 R6。
