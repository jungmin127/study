from __future__ import annotations

import pytest

from engine.regime_detector import CATEGORY_REFERENCE_SCORES, _softmax_categorize


def test_softmax_categorize_sums_to_one():
    probs = _softmax_categorize(0.0)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_softmax_categorize_returns_all_five_categories():
    probs = _softmax_categorize(1.0)
    assert set(probs.keys()) == set(CATEGORY_REFERENCE_SCORES.keys())


def test_softmax_categorize_extreme_positive_score_favors_surge_up():
    probs = _softmax_categorize(10.0)
    assert max(probs, key=probs.get) == "급상승"


def test_softmax_categorize_extreme_negative_score_favors_surge_down():
    probs = _softmax_categorize(-10.0)
    assert max(probs, key=probs.get) == "급하락"


def test_softmax_categorize_zero_score_favors_sideways():
    probs = _softmax_categorize(0.0)
    assert max(probs, key=probs.get) == "횡보"


def test_softmax_categorize_all_probabilities_nonnegative():
    probs = _softmax_categorize(-3.5)
    assert all(p >= 0.0 for p in probs.values())
