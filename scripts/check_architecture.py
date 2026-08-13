"""Fail when the greenfield dependency and source-reuse rules are violated."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

FORBIDDEN_PRODUCTION_MODULES = {
    "ragflow_agent",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langgraph",
}
FORBIDDEN_DOMAIN_MODULES = FORBIDDEN_PRODUCTION_MODULES | {
    "fastapi",
    "sqlalchemy",
    "pydantic",
    "pydantic_settings",
    "redis",
    "elasticsearch",
    "boto3",
    "arq",
}
LEGACY_MARKERS = (
    "src/ragflow_agent/",
    "ragflow_agent.",
    "from ragflow_agent",
    "import ragflow_agent",
)
COPY_MARKERS = ("copied from", "adapted from", "derived from")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _top_level(module: str) -> str:
    return module.split(".", maxsplit=1)[0]


def _registered_sources(root: Path) -> set[str]:
    register_path = root / "docs/reuse-register.yaml"
    text = register_path.read_text(encoding="utf-8")
    try:
        import yaml

        raw = yaml.safe_load(text)
        entries = raw.get("entries", []) if isinstance(raw, dict) else []
        return {
            str(entry["destination_path"]).replace("\\", "/")
            for entry in entries
            if isinstance(entry, dict) and "destination_path" in entry
        }
    except ImportError:
        return set()


def check_architecture(root: Path) -> list[str]:
    source_root = root / "src/rag_platform"
    registered_sources = _registered_sources(root)
    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        imports = {_top_level(module) for module in _imports(path)}
        forbidden = (
            FORBIDDEN_DOMAIN_MODULES
            if "domain" in path.parts
            else FORBIDDEN_PRODUCTION_MODULES
        )
        unexpected = sorted(imports & forbidden)
        if unexpected:
            violations.append(f"{relative}: forbidden imports {unexpected}")
        text = path.read_text(encoding="utf-8").lower()
        if any(marker in text for marker in LEGACY_MARKERS):
            violations.append(f"{relative}: legacy source/runtime marker")
        if any(marker in text for marker in COPY_MARKERS) and relative not in registered_sources:
            violations.append(f"{relative}: copied-source marker without reuse register entry")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    violations = check_architecture(root)
    if args.json:
        print(json.dumps({"violations": violations}, ensure_ascii=False, indent=2))
    elif violations:
        print("\n".join(violations))
    else:
        print("Architecture and greenfield source gates passed.")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
