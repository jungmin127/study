"""
tests/test_regime_service.py

backend.regime_service.evaluate_market()의 반환 스키마와 기본 동작을 검증한다.
get_candles는 네트워크 호출을 하므로 monkeypatch로 합성 데이터로 대체한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

import backend.regime_service as regime_service
from backend.regime_service import evaluate_market
from engine.regime_detector import CATEGORY_REFERENCE_SCORES

_CANDLE_COLUMNS = ["candle_time", "open", "high", "low", "close", "volume", "trade_value"]


def _make_candle_df(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({
        "candle_time": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes), "trade_value": [1.0] * len(closes),
    })


def test_evaluate_market_returns_empty_result_when_no_candles(monkeypatch):
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: pd.DataFrame(columns=_CANDLE_COLUMNS))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert result["candles"] == []
    assert result["correlation"] is None
    assert sum(sum(row.values()) for row in result["confusion"].values()) == 0
    assert sum(result["actual_totals"].values()) == 0


def test_evaluate_market_returns_one_candle_entry_per_input_row(monkeypatch):
    closes = [100.0 * (1.01**i) for i in range(40)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc),
    )

    assert len(result["candles"]) == 40
    assert result["candles"][0]["predicted_category"] is None  # 워밍업 미달(half_life_bars=1.0*5=5봉)
    assert result["candles"][-1]["predicted_category"] in CATEGORY_REFERENCE_SCORES
    assert result["candles"][-1]["close"] == pytest.approx(closes[-1])
    assert result["candles"][0]["time"].endswith("+00:00")


def test_evaluate_market_uptrend_confusion_favors_up_categories(monkeypatch):
    closes = [100.0 * (1.01**i) for i in range(40)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc),
    )

    up_labels = ("완만상승", "급상승")
    total_up_predictions = sum(sum(result["confusion"][label].values()) for label in up_labels)
    total_predictions = sum(sum(row.values()) for row in result["confusion"].values())
    assert total_predictions > 0
    assert total_up_predictions == total_predictions  # 순수 상승추세이므로 하락계열 예측은 0건이어야 함


def test_evaluate_market_raises_value_error_when_candle_count_exceeds_max(monkeypatch):
    closes = [100.0 + i for i in range(regime_service.MAX_CANDLES + 1)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    with pytest.raises(ValueError, match="너무 많습니다"):
        evaluate_market(
            "KRW-BTC", "days",
            datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_evaluate_market_current_prediction_matches_last_candle(monkeypatch):
    closes = [100.0 * (1.01**i) for i in range(40)]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 10, tzinfo=timezone.utc),
    )

    assert result["current_prediction"] is not None
    assert result["current_prediction"]["time"] == result["candles"][-1]["time"]
    assert result["current_prediction"]["predicted_category"] == result["candles"][-1]["predicted_category"]
    assert result["current_prediction"]["probs"] is not None
    assert sum(result["current_prediction"]["probs"].values()) == pytest.approx(1.0, abs=1e-9)


def test_evaluate_market_current_prediction_is_none_when_warmup_not_reached(monkeypatch):
    closes = [100.0, 101.0, 102.0]
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: _make_candle_df(closes))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 4, tzinfo=timezone.utc),
    )

    assert result["current_prediction"] is not None
    assert result["current_prediction"]["predicted_category"] is None
    assert result["current_prediction"]["probs"] is None


def test_evaluate_market_current_prediction_is_none_when_no_candles(monkeypatch):
    monkeypatch.setattr(regime_service, "get_candles", lambda *a, **k: pd.DataFrame(columns=_CANDLE_COLUMNS))

    result = evaluate_market(
        "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert result["current_prediction"] is None
