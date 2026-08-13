# ADR-002：LangChain 与 LangGraph 的职责边界

- 状态：Accepted
- 日期：2026-08-13

## 背景

项目希望最大化利用 LangChain/LangGraph，但旧项目能力还包括权限、生命周期、跨存储一致性、
混合检索质量、Citation、可靠任务和安全治理。这些不能仅靠框架积木保证。

## 决策

LangChain 位于出站 Adapter，负责模型、Embedding、Prompt、结构化输出、Tool 以及可满足领域
契约的 Loader/Splitter/Retriever 集成。LangChain 类型不穿透领域和公开 Schema。

LangGraph 仅用于有真实编排需求的三个图：

- `IngestionGraph`：长时、多分支、可恢复的文档编译编排。
- `AgentGraph`：循环、Tool、Checkpoint、interrupt/resume 和流式事件。
- `AdvancedBuildGraph`：高级知识构建的长时分支和恢复。

固定检索、固定 RAG 算法和生命周期事务状态机不为了统一而建图。

以下保持自研：领域模型和 Policy、tenant/ACL、稳定文档/Chunk 身份、混合检索融合、证据与
Citation、版本/删除/Outbox/CAS/reconciliation、Tool 安全、评测和部署治理。

## 后果

- 可以升级或更换框架 Adapter，而不改领域 Interface。
- Graph State 不成为业务事实源，Checkpoint 不替代业务数据库。
- 需要维护自研 RAG 核心，但这是保留完整能力和安全语义的必要成本。
