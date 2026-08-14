# R5 GroundedRag and Citation

R5 completes the fixed-RAG answer boundary for CAP-17, CAP-21 and CAP-27, and completes the
fixed-RAG portions of CAP-36 and CAP-37. The existing `/v1/rag/query` response remains compatible;
new evidence, cost and citation-location fields are additive. `/v1/rag/query/stream` adds ordered
SSE events.

## Implemented flow

1. `AuthorizedRetrieval` returns candidates that already passed tenant, KB, active-version and
   active-index checks.
2. `EvidencePackage` bounds context size and binds every item to its retrieval trace and exact
   versioned source.
3. Project-owned policy assigns exactly one of `sufficient`, `partial_evidence`, `no_evidence` or
   `conflicting_evidence`. Model output cannot change this status.
4. `no_evidence` and unresolved conflicts produce deterministic refusals. A model is never called
   for `no_evidence`; conflicting evidence is returned with its validated sources.
5. LangChain remains inside `LangChainModelRuntime`, where chat and token streaming are translated
   to provider-neutral contracts. The service owns timeout/retry policy, ordered model fallback,
   cancellation and input/output/cost ceilings.
6. Generated markers are resolved only against the EvidencePackage. Unknown or missing markers
   fail closed. Immediately before publication, referenced hits are revalidated against PostgreSQL;
   a document deleted or superseded during generation cannot become a Citation.
7. Citation schema v2 binds tenant, KB, Document, DocumentVersion, Chunk, page, bounding box, exact
   quote, source metadata and retrieval trace ID.
8. SSE emits monotonic `sequence` values and explicit `retrieval_started`, `evidence_evaluated`,
   `model_fallback`, `answer_delta`, `citations`, `completed`, `cancelled` or `error` events.

Fixed RAG remains a direct linear pipeline. LangGraph is not used on this request path because there
is no durable, branching workflow state. Evidence policy and Citation integrity are project-owned,
not delegated to LangChain.

## Failure semantics and evaluation truth

- Search failure remains `search_dependency_failed` and is never reported as no evidence.
- Exhausted chat providers return `model_dependency_failed`; timeout, budget and Citation failures
  have separate error codes.
- A stream can fall back only before emitting answer text. Failure after the first delta terminates
  as `stream_interrupted`, preventing duplicated mixed-provider output.
- Deterministic Fake tests cover contracts and orchestration. They are not treated as answer-quality
  evidence. Real-provider evaluation is explicitly `not_run` because no provider credentials are
  committed or assumed; the versioned synthetic dataset is ready for a configured evaluation run.
- Full lifecycle tombstones, outbox projection and reconciliation remain R6. Agent use of the same
  evidence boundary remains R7.
