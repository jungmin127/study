"""
engine/regime_ml_metrics.py

이진 장세 분류(CATEGORY_LABELS)의 성능 지표를 계산한다. sklearn 표준 함수를 얇게
감싸 팀 관례 스키마(dict, confusion은 행=예측/열=실제)로 변환한다 —
scripts/train_regime_ml.py가 fold별/마켓별로 반복 호출한다. 이전(5단계 시절)
피어슨 상관계수 기반 평가는 확률벡터의 기댓값과 연속값(실현수익률)을 비교하는
방식이었는데, Triple Barrier 이후 정답 자체가 범주형이라 더 이상 성립하지 않아
표준 분류지표로 교체했다(2026-08-29 문제 재정의). 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md
"""
from __future__ import annotations

import math

from sklearn.metrics import (
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

from engine.regime_ml_labels import CATEGORY_LABELS


def compute_classification_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    """y_true/y_pred: CATEGORY_LABELS 값으로 이뤄진 같은 길이의 리스트. 표본이
    없으면 모든 값이 None/0인 빈 결과를 반환한다(계산 불가와 "성능이 0"을
    구분하기 위해 숫자가 아니라 None으로 표시)."""
    if not y_true:
        return {
            "n": 0,
            "macro_f1": None,
            "weighted_kappa": None,
            "confusion": {p: {a: 0 for a in CATEGORY_LABELS} for p in CATEGORY_LABELS},
            "class_precision_recall": {
                c: {"precision": None, "recall": None} for c in CATEGORY_LABELS
            },
        }

    macro_f1 = float(f1_score(y_true, y_pred, labels=CATEGORY_LABELS, average="macro", zero_division=0))
    weighted_kappa = float(cohen_kappa_score(y_true, y_pred, labels=CATEGORY_LABELS, weights="linear"))
    # y_true/y_pred가 둘 다 단일 클래스로만 이뤄지면 cohen_kappa_score 내부에서
    # 0/0이 발생해 NaN이 나온다 — 이 함수는 "계산 불가"를 이미 None으로 표현하는
    # 관례(위 n==0 분기 등)를 따르므로, NaN도 0.0이 아니라 None으로 맞춘다.
    if math.isnan(weighted_kappa):
        weighted_kappa = None

    matrix = confusion_matrix(y_true, y_pred, labels=CATEGORY_LABELS)
    confusion = {
        predicted: {
            actual: int(matrix[actual_idx, predicted_idx])
            for actual_idx, actual in enumerate(CATEGORY_LABELS)
        }
        for predicted_idx, predicted in enumerate(CATEGORY_LABELS)
    }

    precision, recall, _, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=CATEGORY_LABELS, average=None, zero_division=0
    )
    class_precision_recall = {
        label: {"precision": float(p), "recall": float(r)}
        for label, p, r in zip(CATEGORY_LABELS, precision, recall)
    }

    return {
        "n": len(y_true),
        "macro_f1": macro_f1,
        "weighted_kappa": weighted_kappa,
        "confusion": confusion,
        "class_precision_recall": class_precision_recall,
    }
