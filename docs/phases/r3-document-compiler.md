# R3 DocumentCompiler

R3 replaces the R2 text-only compiler with one deep `DocumentCompiler` interface. The legacy
repository remains a pinned behavior reference; no production source was copied.

## Delivered boundary

- Formats: PDF, DOCX, PPTX, XLSX, TXT, Markdown, HTML and images.
- Normal form: schema-v2 blocks for heading, paragraph, list, table, image and code, including
  source order, page, bounding box, heading path, parser identity, warnings and media provenance.
- OCR: injectable port, Tesseract adapter, language-pack validation, timeout, pixel limits and
  scanned-PDF fallback. The stage uses a deterministic OCR test adapter because this workstation
  does not have the Tesseract executable installed; production fails with `ocr_unavailable`.
  Tesseract 5.5.0 plus its English language pack were invoked in an isolated Linux container; the
  first undersized-font sample correctly produced no recognized words, so CI now installs the real
  runtime and runs a large-font word-geometry test instead of treating adapter construction as proof.
- Chunk methods: General, Paper, Book, Manual, Laws, QA, Table, Resume and Picture. Chunk IDs are
  deterministic over version, method, method version, source block IDs and content.
- Routing/security: MIME + extension + signature agreement, OOXML member/path/expanded-size/ratio
  limits, image pixels, PDF pages, slide/worksheet/cell limits and stable parser error codes.
- Publication: parse metadata and blocks are staged in the same database transaction as chunks and
  candidate embeddings. Parse/chunk failures therefore cannot activate a partial index.

LangChain loaders were not selected for the compiler core: their generic `Document` output is not
sufficient to guarantee the required cross-format geometry, Office source order, archive gates and
stable errors. LangGraph continues to orchestrate load → compile → embed → stage → validate → publish.

## Evidence and known boundaries

Generated legal samples exercise real file libraries and a real PostgreSQL + pgvector ingestion and
query path. OCR coordinates and scanned-PDF fallback use an injected deterministic engine; the actual
Tesseract adapter is contract-tested and fails closed when the executable or language pack is missing.
Complex semantic page-layout models and full multimodal retrieval are not part of the legacy R3 scope.
CAP-35 is therefore only `parsing_foundation_implemented`; full work remains in R8.
