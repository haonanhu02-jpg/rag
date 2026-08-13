from __future__ import annotations

from pathlib import Path

from scripts.check_architecture import check_architecture


def test_production_source_has_no_legacy_or_framework_dependency() -> None:
    assert check_architecture(Path.cwd()) == []


def test_domain_framework_import_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src/rag_platform/domain"
    source.mkdir(parents=True)
    (source / "bad.py").write_text("import langgraph\n", encoding="utf-8")
    register = tmp_path / "docs"
    register.mkdir()
    (register / "reuse-register.yaml").write_text("entries: []\n", encoding="utf-8")

    violations = check_architecture(tmp_path)

    assert violations == ["src/rag_platform/domain/bad.py: forbidden imports ['langgraph']"]


def test_unregistered_copied_source_marker_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src/rag_platform/modules"
    source.mkdir(parents=True)
    (source / "copied.py").write_text("# Copied from old repository\n", encoding="utf-8")
    register = tmp_path / "docs"
    register.mkdir()
    (register / "reuse-register.yaml").write_text("entries: []\n", encoding="utf-8")

    violations = check_architecture(tmp_path)

    assert violations == [
        "src/rag_platform/modules/copied.py: copied-source marker without reuse register entry"
    ]

