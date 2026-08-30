"""
tests/test_regime_ml_calibration.py

engine.regime_ml_calibration의 threshold/확률보정 순수 함수를 검증한다.
"""
from __future__ import annotations

import pytest

from engine.regime_ml_calibration import (
    apply_calibration,
    compute_precision_recall_table,
    fit_isotonic_breakpoints,
    select_threshold_for_target_precision,
)

_LABELS = ["하락", "하락아님"]


def test_compute_precision_recall_table_matches_hand_computed_values():
    # threshold=0.5: proba>=0.5인 4개 중 실제 "하락"은 3개(precision=0.75),
    # 전체 "하락" 5개 중 3개 잡음(recall=0.6).
    y_true = ["하락", "하락", "하락", "하락", "하락", "하락아님", "하락아님", "하락아님"]
    proba_down = [0.9, 0.8, 0.7, 0.3, 0.2, 0.6, 0.4, 0.1]

    table = compute_precision_recall_table(y_true, proba_down, thresholds=[0.5])

    assert len(table) == 1
    row = table[0]
    assert row["threshold"] == 0.5
    assert row["precision"] == 0.75
    assert row["recall"] == 0.6
    assert row["n_predicted_down"] == 4


def test_compute_precision_recall_table_covers_all_requested_thresholds_in_order():
    y_true = ["하락", "하락아님"]
    proba_down = [0.9, 0.1]

    table = compute_precision_recall_table(y_true, proba_down, thresholds=[0.3, 0.6, 0.9])

    assert [row["threshold"] for row in table] == [0.3, 0.6, 0.9]


def test_compute_precision_recall_table_zero_predicted_down_gives_zero_precision_not_error():
    y_true = ["하락", "하락아님"]
    proba_down = [0.1, 0.05]  # 어떤 threshold에서도 "하락"으로 예측되는 표본 없음

    table = compute_precision_recall_table(y_true, proba_down, thresholds=[0.9])

    assert table[0]["precision"] == 0.0
    assert table[0]["recall"] == 0.0
    assert table[0]["n_predicted_down"] == 0


def test_select_threshold_for_target_precision_picks_lowest_threshold_meeting_target():
    table = [
        {"threshold": 0.3, "precision": 0.40, "recall": 0.90, "n_predicted_down": 10},
        {"threshold": 0.5, "precision": 0.55, "recall": 0.70, "n_predicted_down": 6},
        {"threshold": 0.7, "precision": 0.80, "recall": 0.30, "n_predicted_down": 3},
    ]

    threshold = select_threshold_for_target_precision(table, target_precision=0.55)

    assert threshold == 0.5  # 0.55 이상인 것 중 recall이 가장 높은(=threshold가 가장 낮은) 것


def test_select_threshold_for_target_precision_falls_back_to_highest_precision_when_unreachable():
    table = [
        {"threshold": 0.3, "precision": 0.20, "recall": 0.95, "n_predicted_down": 10},
        {"threshold": 0.5, "precision": 0.35, "recall": 0.50, "n_predicted_down": 6},
    ]

    threshold = select_threshold_for_target_precision(table, target_precision=0.55)

    assert threshold == 0.5  # 목표 미달성 -> precision이 가장 높은 threshold로 폴백


def test_fit_isotonic_breakpoints_returns_monotonic_nondecreasing_y():
    y_true = ["하락아님"] * 20 + ["하락"] * 20
    # proba_down이 커질수록 실제 "하락" 비율도 커지는 단조 증가 패턴(사전 정렬)
    proba_down = [i / 39 for i in range(40)]

    breakpoints = fit_isotonic_breakpoints(y_true, proba_down)

    ys = [pt[1] for pt in breakpoints]
    assert ys == sorted(ys)
    assert all(0.0 <= y <= 1.0 for y in ys)


def test_apply_calibration_interpolates_between_breakpoints():
    breakpoints = [[0.0, 0.1], [0.5, 0.5], [1.0, 0.9]]

    assert apply_calibration(breakpoints, 0.25) == pytest.approx(0.3)  # 0.0~0.5 구간 선형보간
    assert apply_calibration(breakpoints, 0.5) == pytest.approx(0.5)


def test_apply_calibration_clips_outside_breakpoint_range():
    breakpoints = [[0.2, 0.1], [0.8, 0.9]]

    assert apply_calibration(breakpoints, 0.0) == 0.1  # 왼쪽 밖 -> 첫 y로 clip
    assert apply_calibration(breakpoints, 1.0) == 0.9  # 오른쪽 밖 -> 마지막 y로 clip
