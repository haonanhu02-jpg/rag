"""Hand-checkable answer and citation metrics used by R5 quality gates."""

from __future__ import annotations

import re


def citation_precision_recall(
    predicted: frozenset[str], expected: frozenset[str]
) -> tuple[float, float]:
    if not predicted and not expected:
        return 1.0, 1.0
    overlap = len(predicted & expected)
    precision = overlap / len(predicted) if predicted else 0.0
    recall = overlap / len(expected) if expected else 0.0
    return precision, recall


def lexical_faithfulness(answer: str, evidence: tuple[str, ...]) -> float:
    """Deterministic smoke metric; real-model quality remains a separately labelled evaluation."""

    answer_terms = _terms(re.sub(r"\[\d+]", "", answer))
    if not answer_terms:
        return 1.0
    evidence_terms = _terms(" ".join(evidence))
    return len(answer_terms & evidence_terms) / len(answer_terms)


def refusal_accuracy(expected_refusal: bool, status: str) -> float:
    refused = status in {"no_evidence", "conflicting_evidence"}
    return 1.0 if refused is expected_refusal else 0.0


def _terms(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[\w\u3400-\u9fff]+", value.casefold()))
