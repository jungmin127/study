"""
engine/regime_ml_splits.py

ML 장세 판별기 학습을 위한 워크포워드(walk-forward) 검증 fold 경계를 만든다. 무작위
shuffle 대신 시간순으로 test 구간을 나누고, train은 각 fold의 test 시작 이전 전체
데이터로 정의한다(expanding window) — 금융 시계열의 미래정보 누수를 막기 위함. 설계
문서: docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_end: datetime
    test_start: datetime
    test_end: datetime


def generate_walk_forward_folds(
    start: datetime, end: datetime, n_folds: int, embargo: timedelta
) -> list[WalkForwardFold]:
    """[start, end]를 n_folds개의 동일 너비 test 구간으로 순서대로 나눈다. 각 fold의
    train_end는 test_start - embargo다 — 레이블이 미래 n_bars를 내다보므로, embargo가
    n_bars에 해당하는 기간(bar_duration * n_bars)만큼은 돼야 train/test 사이 레이블
    누수가 없다. 초반 fold는 train_end가 start보다 이전일 수 있다(훈련 표본 부족) —
    호출자가 최소 표본 수를 별도로 검증해야 한다."""
    if n_folds < 1:
        raise ValueError("n_folds는 1 이상이어야 합니다")
    if end <= start:
        raise ValueError("end는 start보다 이후여야 합니다")

    fold_width = (end - start) / n_folds
    folds: list[WalkForwardFold] = []
    for i in range(n_folds):
        test_start = start + fold_width * i
        test_end = end if i == n_folds - 1 else start + fold_width * (i + 1)
        train_end = test_start - embargo
        folds.append(WalkForwardFold(i, train_end, test_start, test_end))
    return folds
