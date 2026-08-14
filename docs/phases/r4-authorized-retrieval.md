# R4 AuthorizedRetrieval

R4 completes CAP-09 through CAP-20 and CAP-22 at the retrieval layer. `GroundedRag` and the
future governed knowledge tool both use the same framework-neutral `AuthorizedRetrieval` boundary.

## Implemented flow

1. NFKC normalization, control removal, whitespace collapse, language/keyword extraction.
2. Optional structured standalone-question rewrite and translation; malformed or failed model
   output falls back to the canonical query.
3. Recursive, allowlisted Filter AST with bounded depth and node count. Tenant, KB, active-version
   and deletion constraints are system-owned and cannot be supplied or relaxed by clients.
4. Parallel Elasticsearch BM25 and kNN recall. Each channel preserves its raw score and rank.
5. Chunk-ID deduplication and deterministic RRF (`k=60`), optional reranking, threshold, TopN and
   per-document quota.
6. Finite fallback: expanded hybrid, inferred-filter removal, BM25-only and vector-only. Only an
   inferred soft filter may be removed; user and system filters remain immutable.
7. Final PostgreSQL revalidation of tenant, KB, visibility, current document version and active
   index generation before any candidate leaves the retrieval boundary.
8. Content-free, role-scoped trace with query digests, stage events, channel/fusion/rerank/final
   ranking, fallback attempts and expiry. Trace persistence failure increments a metric and does
   not discard already-authorized evidence.

Online retrieval is a direct service pipeline. LangGraph remains responsible for recoverable
workflows such as ingestion; it is deliberately absent from the low-latency retrieval hot path.
LangChain remains behind model adapters only. Search, authorization, Filter AST, RRF and fallback
are project-owned behavior.

## Failure semantics and remaining work

- One failed recall channel may return results from the healthy channel and is recorded as degraded.
- If no evidence is available and a search dependency failed, the API returns
  `search_dependency_failed` (503), never `no_evidence`.
- Projection lifecycle coordination, tombstones and reconciliation remain R6 work. The PostgreSQL
  final-authority guard already prevents a stale projection from leaking inactive content.
- CAP-17 answer-level evidence classification and refusal, plus full Citation validation, remain R5.
- R9 adds production telemetry export and scheduled trace-expiry enforcement.
