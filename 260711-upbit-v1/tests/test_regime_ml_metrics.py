"""
tests/test_regime_ml_metrics.py

engine.regime_ml_metrics.compute_classification_metrics()를 검증한다.
"""
from __future__ import annotations

from engine.regime_ml_labels import CATEGORY_LABELS
from engine.regime_ml_metrics import compute_classification_metrics


def test_compute_classification_metrics_returns_none_values_for_empty_input():
    result = compute_classification_metrics([], [])

    assert result["n"] == 0
    assert result["macro_f1"] is None
    assert result["weighted_kappa"] is None
    assert result["confusion"] == {p: {a: 0 for a in CATEGORY_LABELS} for p in CATEGORY_LABELS}
    assert all(
        result["class_precision_recall"][label] == {"precision": None, "recall": None}
        for label in CATEGORY_LABELS
    )


def test_compute_classification_metrics_perfect_predictions_score_maximally():
    y_true = ["하락", "횡보", "상승", "하락", "횡보", "상승"]
    y_pred = list(y_true)

    result = compute_classification_metrics(y_true, y_pred)

    assert result["n"] == 6
    assert result["macro_f1"] == 1.0
    assert result["weighted_kappa"] == 1.0
    for label in CATEGORY_LABELS:
        assert result["confusion"][label][label] == y_true.count(label)
        assert result["class_precision_recall"][label] == {"precision": 1.0, "recall": 1.0}


def test_compute_classification_metrics_confusion_matrix_is_row_predicted_col_actual():
    # 상승을 2번 예측했는데 실제로는 1번만 맞음(1번은 실제 횡보) -> precision(상승)=0.5
    y_true = ["상승", "횡보", "하락"]
    y_pred = ["상승", "상승", "하락"]

    result = compute_classification_metrics(y_true, y_pred)

    assert result["confusion"]["상승"] == {"하락": 0, "횡보": 1, "상승": 1}
    assert result["class_precision_recall"]["상승"]["precision"] == 0.5
    assert result["class_precision_recall"]["상승"]["recall"] == 1.0


def test_compute_classification_metrics_worst_case_kappa_and_f1_are_low():
    # 실제와 정반대로만 예측(하락<->상승 뒤바꿈) -> 우연보다도 못한 성능
    y_true = ["하락", "하락", "상승", "상승"]
    y_pred = ["상승", "상승", "하락", "하락"]

    result = compute_classification_metrics(y_true, y_pred)

    assert result["weighted_kappa"] == -1.0
    assert result["macro_f1"] == 0.0
