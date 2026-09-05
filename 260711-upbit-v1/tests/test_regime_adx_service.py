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
    assert first["in_progress"] is False
    assert second["in_progress"] is True


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
    assert result["segments"][0]["in_progress"] is True


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
    # trailing run은 None(워밍업/미분류)이라 in_progress 대상이 아니고, 이
    # 구간은 trailing이 아니므로 in_progress=False.
    assert result["segments"][0]["in_progress"] is False


def test_compute_adx_regime_history_includes_run_exactly_at_min_segment_bars(monkeypatch):
    """MIN_SEGMENT_BARS 경계값(>=) 핀 테스트 — 정확히 임계치와 같은 길이의
    구간도 포함돼야 한다(오늘은 그보다 길거나 짧은 구간만 테스트되고 있었음)."""
    df = _make_df(3)
    adx_di = _make_adx_di(3, [30, 30, 30], [40, 40, 40], [10, 10, 10])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 3)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "상승"
    assert result["segments"][0]["bar_count"] == 3
    assert result["segments"][0]["in_progress"] is True


def test_compute_adx_regime_history_surfaces_short_trailing_run_as_in_progress(monkeypatch):
    """가장 최근 구간(마지막 봉을 포함)이 MIN_SEGMENT_BARS 미만이어도, 현재
    장세를 표에서 항상 보여주기 위해 in_progress=True로 포함돼야 한다.
    trailing이 아닌 다른 짧은 구간(첫 3봉 상승)은 여전히 제외된다."""
    df = _make_df(5)
    adx_di = _make_adx_di(
        5,
        [30, 30, 30, 30, 30],
        [40, 40, 40, 10, 10],
        [10, 10, 10, 40, 40],
    )
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)
    monkeypatch.setattr(regime_adx_service, "MIN_SEGMENT_BARS", 24)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["segments"]) == 1
    assert result["segments"][0]["label"] == "하락"
    assert result["segments"][0]["bar_count"] == 2
    assert result["segments"][0]["in_progress"] is True


def test_compute_adx_regime_history_handles_empty_candles(monkeypatch):
    """조회 기간에 캔들이 전혀 없는 마켓(상장폐지/거래정지 등)도 예외 없이
    빈 bars/segments를 반환해야 한다."""
    empty_df = pd.DataFrame(columns=["candle_time", "open", "high", "low", "close"])
    empty_adx_di = pd.DataFrame(columns=["adx", "plus_di", "minus_di"])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: empty_df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: empty_adx_di)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert result == {"market": "KRW-BTC", "timeframe": "minutes60", "bars": [], "segments": []}


def test_compute_adx_regime_history_handles_single_bar_market(monkeypatch):
    """1개 봉만 있는(워밍업조차 못 채운) 마켓 — 그 한 봉의 라벨은 None이고
    segments는 비어 있어야 한다."""
    df = _make_df(1)
    adx_di = _make_adx_di(1, [float("nan")], [float("nan")], [float("nan")])
    monkeypatch.setattr(regime_adx_service, "get_candles", lambda *a, **k: df)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", lambda *a, **k: adx_di)

    result = compute_adx_regime_history("KRW-BTC", "minutes60")

    assert len(result["bars"]) == 1
    assert result["bars"][0]["label"] is None
    assert result["segments"] == []


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


def test_compute_adx_regime_overview_handles_empty_candles_for_one_market(monkeypatch):
    """상장폐지/거래정지 등으로 조회 기간에 캔들이 하나도 없는 마켓이 섞여
    있어도, 그 마켓만 미분류로 내려가고 나머지 오버뷰 전체가 500으로 죽지
    않아야 한다."""
    empty_df = pd.DataFrame(columns=["candle_time", "open", "high", "low", "close"])
    healthy_df = _make_df(30)
    healthy_adx_di = pd.DataFrame({
        "adx": [30.0] * 30, "plus_di": [40.0] * 30, "minus_di": [10.0] * 30,
    })

    def fake_get_candles(market, *a, **k):
        return empty_df if market == "KRW-DELISTED" else healthy_df

    def fake_compute_adx_di(df, *a, **k):
        return pd.DataFrame(columns=["adx", "plus_di", "minus_di"]) if df.empty else healthy_adx_di

    monkeypatch.setattr(regime_adx_service, "MAJOR_MARKETS", ["KRW-DELISTED", "KRW-BTC"])
    monkeypatch.setattr(regime_adx_service, "get_candles", fake_get_candles)
    monkeypatch.setattr(regime_adx_service, "compute_adx_di", fake_compute_adx_di)

    result = compute_adx_regime_overview("minutes60")

    assert result[0] == {
        "market": "KRW-DELISTED", "label": None,
        "adx": None, "plus_di": None, "minus_di": None,
    }
    assert result[1]["market"] == "KRW-BTC"
    assert result[1]["label"] == "상승"
