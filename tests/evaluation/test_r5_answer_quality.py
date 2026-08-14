from rag_platform.modules.grounded_rag.evaluation import (
    citation_precision_recall,
    lexical_faithfulness,
    refusal_accuracy,
)


def test_deterministic_answer_metrics_are_hand_checkable() -> None:
    precision, recall = citation_precision_recall(
        frozenset({"chunk-a", "chunk-b"}), frozenset({"chunk-a", "chunk-c"})
    )
    assert precision == 0.5
    assert recall == 0.5
    assert lexical_faithfulness("relay reset [1]", ("relay reset procedure",)) == 1.0
    assert refusal_accuracy(True, "no_evidence") == 1.0
    assert refusal_accuracy(True, "answered") == 0.0
