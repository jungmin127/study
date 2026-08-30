"""
tests/test_regime_fact_service.py

backend.regime_fact_service.compute_fact_regime_segments()를 검증한다. 라벨링
수학 자체(compute_triple_barrier_labels)는 tests/test_regime_ml_labels.py가 이미
검증하므로, 여기서는 compute_triple_barrier_labels를 monkeypatch로 고정해 이
함수가 새로 하는 일(봉별 bars 배열 조립, 연속 구간 묶기, 최소 지속봉수 필터링)만
검증한다.
"""
from __future__ import annotations

import pandas as pd

import backend.regime_fact_service as regime_fact_service
from backend.regime_fact_service import _same_label, compute_fact_regime_segments


def _make_df(n: int) -> pd.DataFrame:
    times = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "candle_time": times,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
    })


def test_same_label_treats_both_nan_as_equal():
    assert _same_label(float("nan"), float("nan")) is True


def test_same_label_treats_label_and_nan_as_different():
    assert _same_label("하락", float("nan")) is False
    assert _same_label(float("nan"), "하락") is False


def test_same_label_compares_equal_and_different_strings():
    assert _same_label("하락", "하락") is True
    assert _same_label("하락", "하락아님") is False


def test_compute_fact_regime_segments_bars_carry_per_bar_label(monkeypatch):
    df = _make_df(4)
    labels = pd.Series(["하락", "하락", "하락아님", float("nan")], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert [b["label"] for b in result["bars"]] == ["하락", "하락", "하락아님", None]
    assert result["market"] == "KRW-BTC"
    assert result["timeframe"] == "minutes60"
    assert len(result["bars"]) == 4
    assert result["bars"][0]["open"] == 100.0
    assert result["bars"][0]["time"] == df["candle_time"].iloc[0].isoformat()


def test_compute_fact_regime_segments_merges_consecutive_same_label_into_one_segment(monkeypatch):
    df = _make_df(6)
    labels = pd.Series(["하락", "하락", "하락", "하락아님", "하락아님", "하락아님"], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)
    monkeypatch.setattr(regime_fact_service, "MIN_SEGMENT_BARS", 2)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 2
    first, second = result["segments"]
    assert first["label"] == "하락"
    assert first["bar_count"] == 3
    assert first["start"] == df["candle_time"].iloc[0].isoformat()
    assert first["end"] == df["candle_time"].iloc[2].isoformat()
    assert second["label"] == "하락아님"
    assert second["bar_count"] == 3
    assert second["start"] == df["candle_time"].iloc[3].isoformat()
    assert second["end"] == df["candle_time"].iloc[5].isoformat()


def test_compute_fact_regime_segments_excludes_runs_shorter_than_min_bars(monkeypatch):
    df = _make_df(5)
    labels = pd.Series(["하락", "하락아님", "하락아님", "하락아님", "하락아님"], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)
    monkeypatch.setattr(regime_fact_service, "MIN_SEGMENT_BARS", 2)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "하락아님"
    assert result["segments"][0]["bar_count"] == 4


def test_compute_fact_regime_segments_excludes_nan_runs_from_segments(monkeypatch):
    df = _make_df(5)
    labels = pd.Series(["하락", "하락", float("nan"), float("nan"), float("nan")], index=df.index)
    monkeypatch.setattr(regime_fact_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_fact_service, "compute_triple_barrier_labels", lambda *a, **k: labels)
    monkeypatch.setattr(regime_fact_service, "MIN_SEGMENT_BARS", 1)

    result = compute_fact_regime_segments("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "하락"
    assert result["segments"][0]["bar_count"] == 2
