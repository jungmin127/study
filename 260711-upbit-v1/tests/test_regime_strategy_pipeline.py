from datetime import datetime, timezone

from scripts.regime_strategy_pipeline import adjust_window, augment_with_tp_sl, min_trades_for_days, pick_final_strategy, run_grid_for_window, select_target_segments, top_candidates


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


def test_run_grid_for_window_uses_all_six_categories_and_given_capital(monkeypatch):
    calls = {}

    def fake_build_condition_grid(pool, market=None):
        calls["pool"] = pool
        calls["market"] = market
        return (
            [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
            [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
        )

    fake_df = object()

    def fake_fetch(market, timeframe, start, end, buy_group, sell_group):
        calls["fetch_args"] = (market, timeframe, start, end)
        return fake_df

    def fake_check_warmup(df, buy_conditions, sell_conditions):
        calls["warmup_checked"] = True

    def fake_compute_parallel(df, buy_conditions, sell_conditions, risk_config):
        calls["risk_config"] = risk_config
        return [{"return_pct": 1.0, "trades": []}]

    monkeypatch.setattr("scripts.regime_strategy_pipeline.build_condition_grid", fake_build_condition_grid)
    monkeypatch.setattr("scripts.regime_strategy_pipeline._fetch_backtest_dataframe", fake_fetch)
    monkeypatch.setattr("scripts.regime_strategy_pipeline._check_candle_warmup", fake_check_warmup)
    monkeypatch.setattr("scripts.regime_strategy_pipeline.compute_grid_results_parallel", fake_compute_parallel)

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 20, tzinfo=timezone.utc)

    grid = run_grid_for_window("KRW-ETH", start, end, capital=10_000_000)

    assert calls["pool"]["categories"] == ["오실레이터", "추세", "가격대", "거래량", "거래대금", "시장 심리"]
    assert calls["market"] == "KRW-ETH"
    assert calls["warmup_checked"] is True
    assert calls["risk_config"]["initial_capital"] == 10_000_000
    assert grid["df"] is fake_df
    assert grid["results"] == [{"return_pct": 1.0, "trades": []}]


def _candidate(return_pct: float, n_trades: int) -> dict:
    return {
        "buy_block": {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        "return_pct": return_pct,
        "trades": [{"entryTime": f"t{i}", "exitTime": f"t{i}x"} for i in range(n_trades)],
    }


def test_pick_final_strategy_accepts_first_passing_candidate(monkeypatch):
    call_count = {"n": 0}

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        call_count["n"] += 1
        return {"trades": [{"pnl": 1.0}] * 5, "final_value": 1_100_000}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4), _candidate(8.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert call_count["n"] == 1
    assert final["raw_return_pct"] == 10.0
    assert final["raw_trade_count"] == 4
    assert final["return_pct"] == 10.0
    assert final["sell_conditions"]["type"] == "OR"


def test_pick_final_strategy_falls_through_when_first_fails_trade_count(monkeypatch):
    responses = [
        {"trades": [{"pnl": 1.0}], "final_value": 1_010_000},
        {"trades": [{"pnl": 1.0}] * 5, "final_value": 1_080_000},
    ]

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        return responses.pop(0)

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4), _candidate(8.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert final["raw_return_pct"] == 8.0


def test_pick_final_strategy_returns_none_when_all_candidates_fail(monkeypatch):
    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        return {"trades": [{"pnl": 1.0}], "final_value": 1_010_000}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert final is None
