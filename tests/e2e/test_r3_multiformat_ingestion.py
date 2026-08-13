from __future__ import annotations

import os
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.fakes.r3_documents import StaticOcr, samples

from rag_platform.bootstrap.api import create_app
from rag_platform.bootstrap.r2_runtime import R2Runtime
from rag_platform.bootstrap.settings import Settings

TENANT = "00000000-0000-0000-0000-000000000001"
ACTOR = "00000000-0000-0000-0000-000000000011"


@pytest.fixture
def r3_client(tmp_path: Path) -> Iterator[tuple[TestClient, R2Runtime]]:
    database_url = os.environ.get("RAG_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("RAG_TEST_DATABASE_URL is required for the real R3 vertical slice")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    runtime_settings = Settings(
        "test", database_url, "INFO", str(tmp_path / "objects"), 10 * 1024 * 1024
    )
    runtime = R2Runtime(runtime_settings, ocr=StaticOcr())
    with TestClient(create_app(settings=runtime_settings, runtime=runtime)) as client:
        yield client, runtime


def test_all_eight_formats_compile_publish_and_keep_structured_provenance(
    r3_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = r3_client
    headers = {"x-tenant-id": TENANT, "x-actor-id": ACTOR, "x-roles": "owner"}
    kb = client.post("/v1/knowledge-bases", headers=headers, json={"name": "R3"}).json()["id"]
    for ordinal, sample in enumerate(samples()):
        response = client.post(
            f"/v1/knowledge-bases/{kb}/documents",
            headers={**headers, "Idempotency-Key": f"r3-{ordinal}"},
            files={
                "file": (sample.name, sample.content, sample.media_type),
                "chunk_method": (None, "manual" if sample.name != "alarms.xlsx" else "table"),
            },
        )
        assert response.status_code == 202, (sample.name, response.text)
        runtime.ingestion.run(runtime.repository.next_pending_job())  # type: ignore[arg-type]

    with runtime.engine.connect() as connection:
        observed = (
            connection.execute(
                text(
                    "SELECT count(DISTINCT document_version_id) AS versions, "
                    "count(*) FILTER (WHERE parser_name <> 'plain-text') AS structured_blocks, "
                    "count(*) FILTER (WHERE page_number IS NOT NULL) AS located_blocks "
                    "FROM document_blocks"
                )
            )
            .mappings()
            .one()
        )
        methods = set(
            connection.execute(
                text("SELECT DISTINCT chunk_method FROM document_versions")
            ).scalars()
        )
    assert observed["versions"] == 8
    assert observed["structured_blocks"] > 8
    assert observed["located_blocks"] > 0
    assert methods == {"manual", "table"}

    answer = client.post(
        "/v1/rag/query",
        headers=headers,
        json={"question": "Alarm recovery", "knowledge_base_ids": [kb], "top_n": 2},
    )
    assert answer.status_code == 200
    assert answer.json()["citations"]


def test_parser_resource_failure_marks_job_and_never_publishes_partial_index(
    r3_client: tuple[TestClient, R2Runtime],
) -> None:
    client, runtime = r3_client
    headers = {"x-tenant-id": TENANT, "x-actor-id": ACTOR, "x-roles": "owner"}
    kb = client.post("/v1/knowledge-bases", headers=headers, json={"name": "Failure"}).json()[
        "id"
    ]
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "A" * 2_000_000)
    upload = client.post(
        f"/v1/knowledge-bases/{kb}/documents",
        headers={**headers, "Idempotency-Key": "zip-bomb"},
        files={
            "file": (
                "unsafe.docx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == 202
    with pytest.raises(Exception, match="compression ratio"):
        runtime.ingestion.run(runtime.repository.next_pending_job())  # type: ignore[arg-type]
    job = client.get(f"/v1/ingestion-jobs/{upload.json()['job_id']}", headers=headers).json()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "parser_resource_limit"
    with runtime.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM index_versions")) == 0
