"""
tests/test_regime_ml_splits.py

engine.regime_ml_splits.generate_walk_forward_folds()를 검증한다. expanding window로
test 구간을 겹침 없이 나누고, train_end는 embargo만큼 test_start 이전으로 당겨져야
한다(레이블이 미래 n_bars를 보는 데서 오는 누수를 막기 위함).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.regime_ml_splits import WalkForwardFold, generate_walk_forward_folds

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=400)


def test_folds_cover_full_range_without_gap_or_overlap():
    folds = generate_walk_forward_folds(START, END, n_folds=4, embargo=timedelta(days=3))

    assert len(folds) == 4
    assert folds[0].test_start == START
    assert folds[-1].test_end == END
    for i in range(len(folds) - 1):
        assert folds[i].test_end == folds[i + 1].test_start


def test_train_end_respects_embargo():
    embargo = timedelta(days=3)
    folds = generate_walk_forward_folds(START, END, n_folds=4, embargo=embargo)

    for fold in folds:
        assert fold.train_end == fold.test_start - embargo


def test_fold_index_is_sequential():
    folds = generate_walk_forward_folds(START, END, n_folds=3, embargo=timedelta(days=1))
    assert [f.fold_index for f in folds] == [0, 1, 2]


def test_raises_when_n_folds_less_than_one():
    with pytest.raises(ValueError, match="n_folds"):
        generate_walk_forward_folds(START, END, n_folds=0, embargo=timedelta(days=1))


def test_raises_when_end_before_start():
    with pytest.raises(ValueError, match="end"):
        generate_walk_forward_folds(END, START, n_folds=2, embargo=timedelta(days=1))


def test_fold_is_frozen_dataclass():
    fold = WalkForwardFold(0, START, START, END)
    with pytest.raises(Exception):
        fold.fold_index = 1
