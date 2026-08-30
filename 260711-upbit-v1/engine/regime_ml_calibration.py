"""
engine/regime_ml_calibration.py

장세 판별 ML의 "하락" 클래스 예측 확률에 대한 threshold 선택과 확률 보정(isotonic
calibration) 순수 함수. scripts/train_regime_ml.py가 walk-forward pooled
out-of-fold 확률로 이 함수들을 호출해 모델 sidecar에 decision_threshold/
calibration_breakpoints를 저장하고, backend/regime_ml_service.py가 서빙 시
저장된 breakpoints로 apply_calibration()을 재현한다(sklearn 의존 없이 np.interp
기반이라 서빙 경로가 가볍다). 설계 문서:
docs/superpowers/specs/2026-08-31-regime-ml-performance-improvement-design.md
"""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression

_DOWN_LABEL = "하락"


def compute_precision_recall_table(
    y_true: list[str], proba_down: list[float], thresholds: list[float]
) -> list[dict]:
    """threshold별로 "proba_down >= threshold면 하락으로 예측"했을 때의
    precision/recall/예측된 하락 표본 수를 계산한다. 어떤 threshold에서도 "하락"으로
    예측된 표본이 없으면(0으로 나눗셈) precision을 0.0으로 취급한다(정의 불가가
    아니라 "경고를 전혀 안 냄"이므로 0이 자연스러운 값)."""
    y_true_arr = np.array(y_true)
    proba_arr = np.array(proba_down)
    n_actual_down = int((y_true_arr == _DOWN_LABEL).sum())

    table: list[dict] = []
    for threshold in thresholds:
        predicted_down = proba_arr >= threshold
        n_predicted_down = int(predicted_down.sum())
        if n_predicted_down == 0:
            precision = 0.0
        else:
            true_positive = int(((y_true_arr == _DOWN_LABEL) & predicted_down).sum())
            precision = true_positive / n_predicted_down
        if n_actual_down == 0:
            recall = 0.0
        else:
            true_positive = int(((y_true_arr == _DOWN_LABEL) & predicted_down).sum())
            recall = true_positive / n_actual_down
        table.append({
            "threshold": threshold,
            "precision": precision,
            "recall": recall,
            "n_predicted_down": n_predicted_down,
        })
    return table


def select_threshold_for_target_precision(table: list[dict], target_precision: float) -> float:
    """precision >= target_precision인 행 중 threshold가 가장 낮은(=recall이 가장
    높은) 것을 고른다. 목표를 만족하는 threshold가 없으면 precision이 가장 높은
    threshold로 폴백한다(그마저도 여러 개면 threshold가 가장 낮은 쪽)."""
    meeting = [row for row in table if row["precision"] >= target_precision]
    if meeting:
        return min(meeting, key=lambda row: row["threshold"])["threshold"]
    best = max(table, key=lambda row: (row["precision"], -row["threshold"]))
    return best["threshold"]


def fit_isotonic_breakpoints(y_true: list[str], proba_down: list[float]) -> list[list[float]]:
    """pooled out-of-fold (proba_down, 실제 "하락" 여부) 쌍에 IsotonicRegression을
    적합해 정렬된 [x, y] 브레이크포인트 리스트를 반환한다. sklearn의
    IsotonicRegression 객체를 그대로 저장하지 않는 이유: 서빙 경로
    (backend/regime_ml_service.py)가 sklearn 없이 np.interp만으로 재현 가능하게
    하기 위함(모델 파일과 같은 텍스트 기반 sidecar json에 담기도 더 쉬움)."""
    y_binary = [1.0 if label == _DOWN_LABEL else 0.0 for label in y_true]
    regressor = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    regressor.fit(proba_down, y_binary)
    xs = regressor.X_thresholds_
    ys = regressor.y_thresholds_
    return [[float(x), float(y)] for x, y in zip(xs, ys)]


def apply_calibration(breakpoints: list[list[float]], proba: float) -> float:
    """breakpoints 기준 선형보간으로 보정된 확률을 계산한다. breakpoints가 비어있으면
    (구형 모델 등) 원래 확률을 그대로 반환한다(항등 보정)."""
    if not breakpoints:
        return proba
    xs = [pt[0] for pt in breakpoints]
    ys = [pt[1] for pt in breakpoints]
    return float(np.interp(proba, xs, ys))
