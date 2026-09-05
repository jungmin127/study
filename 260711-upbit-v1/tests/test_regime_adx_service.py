"""
tests/test_regime_adx_service.py

backend.regime_adx_service.compute_adx_regime_history()/
compute_adx_regime_overview()를 검증한다. ADX 계산 수학 자체(compute_adx_di,
classify_regime)는 tests/test_regime_adx.py가 이미 검증하므로, 여기서는
compute_adx_di를 monkeypatch로 고정해 이 모듈이 새로 하는 일(봉별 bars
배열 조립, 연속 구간 묶기, 최소 지속봉수 필터링, 오버뷰 순회)만 검증한다.
"""
from __future__ import annotations

import pandas as pd

import backend.regime_adx_service as regime_adx_service
from backend.regime_adx_service import _same_label, compute_adx_regime_history, compute_adx_regime_overview


def _make_df(n: int) -> pd.DataFrame:
    times = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "candle_time": times,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
    })


def _make_adx_di(n: int, adx: list[float], plus_di: list[float], minus_di: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})[:n]


def test_same_label_treats_both_none_as_equal():
    assert _same_label(None, None) is True


def test_same_label_treats_label_and_none_as_different():
    assert _same_label("상승", None) is False
    assert _same_label(None, "상승") is False


def test_same_label_compares_equal_and_different_strings():
    assert _same_label("상승", "상승") is True
    assert _same_label("상승", "하락") is False


def test_compute_adx_regime_history_bars_carry_per_bar_label(monkeypatch):
    df = _make_df(4)
    adx_di = _make_adx_di(4, [30, 30, 30, float("nan")], [40, 40, 10, float("nan")], [10, 10, 40, float("nan")])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert [b["label"] for b in result["bars"]] == ["상승", "상승", "하락", None]
    assert result["market"] == "KRW-BTC"
    assert result["timeframe"] == "minutes60"
    assert len(result["bars"]) == 4
    assert result["bars"][0]["open"] == 100.0
    assert result["bars"][0]["time"] == df["candle_time"].iloc[0].isoformat()


def test_compute_adx_regime_history_merges_consecutive_same_label_into_one_segment(monkeypatch):
    df = _make_df(6)
    adx_di = _make_adx_di(
        6,
        [30, 30, 30, 30, 30, 30],
        [40, 40, 40, 10, 10, 10],
        [10, 10, 10, 40, 40, 40],
    )
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 2)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 2
    first, second = result["segments"]
    assert first["label"] == "상승"
    assert first["bar_count"] == 3
    assert first["start"] == df["candle_time"].iloc[0].isoformat()
    assert first["end"] == df["candle_time"].iloc[2].isoformat()
    assert second["label"] == "하락"
    assert second["bar_count"] == 3
    assert second["start"] == df["candle_time"].iloc[3].isoformat()
    assert second["end"] == df["candle_time"].iloc[5].isoformat()


def test_compute_adx_regime_history_excludes_runs_shorter_than_min_bars(monkeypatch):
    df = _make_df(5)
    adx_di = _make_adx_di(5, [10, 30, 30, 30, 30], [40, 40, 40, 40, 40], [10, 10, 10, 10, 10])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 2)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "상승"
    assert result["segments"][0]["bar_count"] == 4


def test_compute_adx_regime_history_excludes_none_runs_from_segments(monkeypatch):
    df = _make_df(5)
    adx_di = _make_adx_di(
        5,
        [30, 30, float("nan"), float("nan"), float("nan")],
        [40, 40, float("nan"), float("nan"), float("nan")],
        [10, 10, float("nan"), float("nan"), float("nan")],
    )
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 1)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "상승"
    assert result["segments"][0]["bar_count"] == 2


def test_compute_adx_regime_overview_returns_one_entry_per_major_market(monkeypatch):
    df = _make_df(30)
    adx_di = pd.DataFrame({
        "adx": [30.0] * 30, "plus_di": [40.0] * 30, "minus_di": [10.0] * 30,
    })
    monkeypatch.setattr(regime_adx_service, "MAJOR_MARKETS", ["KRW-BTC", "KRW-ETH"])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_overview("minutes60")

    assert [r["market"] for r in result] == ["KRW-BTC", "KRW-ETH"]
    assert all(r["label"] == "상승" for r in result)
    assert all(r["adx"] == 30.0 for r in result)


def test_compute_adx_regime_overview_reports_none_label_when_last_bar_is_warmup(monkeypatch):
    df = _make_df(5)
    adx_di = pd.DataFrame({
        "adx": [float("nan")] * 5, "plus_di": [float("nan")] * 5, "minus_di": [float("nan")] * 5,
    })
    monkeypatch.setattr(regime_adx_service, "MAJOR_MARKETS", ["KRW-BTC"])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_overview("minutes60")

    assert result[0]["label"] is None
    assert result[0]["adx"] is None
