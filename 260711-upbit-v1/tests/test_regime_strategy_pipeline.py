from datetime import datetime, timezone

from scripts.regime_strategy_pipeline import adjust_window, augment_with_tp_sl, min_trades_for_days, select_target_segments, top_candidates


def test_min_trades_for_days_boundary():
    assert min_trades_for_days(1) == 3
    assert min_trades_for_days(5) == 3
    assert min_trades_for_days(15) == 3
    assert min_trades_for_days(20) == 4


def test_adjust_window_keeps_already_long_enough_segment():
    seg = {"start": "2026-06-01T00:00:00+00:00", "end": "2026-06-20T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, tzinfo=timezone.utc)


def test_adjust_window_widens_short_segment_by_pulling_start_back():
    seg = {"start": "2026-06-15T00:00:00+00:00", "end": "2026-06-20T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, tzinfo=timezone.utc)


def test_adjust_window_clamps_to_history_start():
    seg = {"start": "2026-01-05T00:00:00+00:00", "end": "2026-01-08T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == history_start
    assert end == datetime(2026, 1, 8, tzinfo=timezone.utc)


def test_select_target_segments_picks_latest_per_label_and_filters_by_history_start(monkeypatch):
    fake_history = {
        "segments": [
            {"start": "2026-02-01T00:00:00+00:00", "end": "2026-02-10T00:00:00+00:00", "label": "상승", "bar_count": 240},
            {"start": "2026-03-01T00:00:00+00:00", "end": "2026-03-15T00:00:00+00:00", "label": "상승", "bar_count": 360},
            {"start": "2025-12-01T00:00:00+00:00", "end": "2025-12-20T00:00:00+00:00", "label": "하락", "bar_count": 480},
        ]
    }
    monkeypatch.setattr(
        "scripts.regime_strategy_pipeline.compute_adx_regime_history",
        lambda market, timeframe: fake_history,
    )

    result = select_target_segments("KRW-ETH", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result["상승"]["start"] == "2026-03-01T00:00:00+00:00"  # 두 개 중 더 최근 것
    assert result["하락"] is None  # history_start 이전이라 제외
    assert result["횡보"] is None  # 세그먼트 자체가 없음


def test_augment_with_tp_sl_preserves_base_and_adds_or_blocks():
    base_sell = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
    }

    result = augment_with_tp_sl(base_sell, stop_loss_pct=-5, take_profit_pct=8)

    assert result["type"] == "OR"
    assert result["conditions"][0] == base_sell
    assert result["conditions"][1] == {
        "indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5,
    }
    assert result["conditions"][2] == {
        "indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 8,
    }


def _grid_result(return_pct: float, n_trades: int) -> dict:
    return {
        "return_pct": return_pct,
        "buy_block": {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        "trades": [{"entryTime": f"t{i}", "exitTime": f"t{i}x"} for i in range(n_trades)],
        "final_value": 1_000_000 * (1 + return_pct / 100),
    }


def test_top_candidates_filters_by_min_trades_then_sorts_by_return():
    results = [_grid_result(10.0, 2), _grid_result(5.0, 5), _grid_result(20.0, 1)]

    candidates = top_candidates(results, min_trades=3, pool_size=10)

    assert len(candidates) == 1
    assert candidates[0]["return_pct"] == 5.0
