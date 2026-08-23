"""
engine/regime_detector.py

실시간 장세 판별 — 규칙기반 EWMA 위험조정 모멘텀 스코어로 매 봉 인과적으로
5개 장세 카테고리의 확률벡터를 산출한다. 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md
"""
from __future__ import annotations

import math

TEMPERATURE = 1.0

CATEGORY_REFERENCE_SCORES: dict[str, float] = {
    "급하락": -2.0,
    "완만하락": -0.7,
    "횡보": 0.0,
    "완만상승": 0.7,
    "급상승": 2.0,
}


def _softmax_categorize(score: float, temperature: float = TEMPERATURE) -> dict[str, float]:
    """score와 각 카테고리 대표값의 거리에 softmax를 적용해 확률벡터를 만든다.
    합계는 항상 1.0."""
    labels = list(CATEGORY_REFERENCE_SCORES.keys())
    neg_distances = [
        -abs(score - CATEGORY_REFERENCE_SCORES[label]) / temperature for label in labels
    ]
    max_val = max(neg_distances)
    exp_vals = [math.exp(v - max_val) for v in neg_distances]
    total = sum(exp_vals)
    return {label: exp_val / total for label, exp_val in zip(labels, exp_vals)}
