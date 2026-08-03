import pytest

from engine.sweep import DEFAULT_RISK_CONFIG
from scripts.grid_search import (
    build_condition_grid,
    compute_grid_results,
    compute_grid_results_parallel,
    dedup_top_results,
    _run_one_combo,
    _check_candle_warmup,
    _macd_required_bars,
    _watchdog_expired,
)
from tests.signal_fixtures import make_oscillating_df


def test_build_condition_grid_combo_counts():
    buy_conditions, sell_conditions = build_condition_grid()
    assert len(buy_conditions) == 138
    assert len(sell_conditions) == 150


def test_build_condition_grid_uses_k_period_for_stochastics():
    buy_conditions, sell_conditions = build_condition_grid()
    for indicator in ("STOCH_K", "STOCH_D"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert len(blocks) == 9
        assert all("k_period" in b["params"] for b in blocks)
        assert all("period" not in b["params"] for b in blocks)
        sell_blocks = [b for b in sell_conditions if b["indicator"] == indicator]
        assert all("k_period" in b["params"] for b in sell_blocks)


def test_build_condition_grid_uses_period_for_non_stochastic_oscillators():
    buy_conditions, _ = build_condition_grid()
    for indicator in ("RSI", "CCI", "WILLIAMS_R", "BB_PERCENT_B", "ATR_PCT"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert {b["params"]["period"] for b in blocks} == {10, 14, 20}


def test_build_condition_grid_rsi_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    rsi_buy = [b for b in buy_conditions if b["indicator"] == "RSI"]
    rsi_sell = [b for b in sell_conditions if b["indicator"] == "RSI"]
    assert {b["threshold"] for b in rsi_buy} == {20, 30, 40}
    assert all(b["operator"] == "<" for b in rsi_buy)
    assert {b["threshold"] for b in rsi_sell} == {60, 70, 80}
    assert all(b["operator"] == ">" for b in rsi_sell)


def test_build_condition_grid_sell_only_indicators():
    _, sell_conditions = build_condition_grid()
    stop_loss = [b for b in sell_conditions if b["indicator"] == "STOP_LOSS_PCT"]
    take_profit = [b for b in sell_conditions if b["indicator"] == "TAKE_PROFIT_PCT"]
    holding = [b for b in sell_conditions if b["indicator"] == "HOLDING_PERIOD_BARS"]
    assert len(stop_loss) == 4 and {b["threshold"] for b in stop_loss} == {-3, -5, -7, -10}
    assert all(b["operator"] == "<=" and b["params"] == {} for b in stop_loss)
    assert len(take_profit) == 4 and {b["threshold"] for b in take_profit} == {5, 10, 15, 20}
    assert all(b["operator"] == ">=" and b["params"] == {} for b in take_profit)
    assert len(holding) == 4 and {b["threshold"] for b in holding} == {5, 10, 20, 40}
    assert all(b["operator"] == ">=" and b["params"] == {} for b in holding)


def test_compute_grid_results_runs_every_combo():
    df = make_oscillating_df(n=200)
    buy_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        {"indicator": "CCI", "params": {"period": 20}, "operator": "<", "threshold": -100},
    ]
    sell_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 10},
    ]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)

    assert len(results) == 4
    for r in results:
        assert set(r.keys()) == {"return_pct", "buy_block", "sell_block", "trades", "final_value"}
        assert isinstance(r["trades"], list)
        assert isinstance(r["return_pct"], float)


def test_compute_grid_results_pairs_every_buy_with_every_sell():
    df = make_oscillating_df(n=200)
    buy_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
    ]
    sell_conditions = [
        {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        {"indicator": "RSI", "params": {"period": 20}, "operator": ">", "threshold": 80},
    ]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)

    assert len(results) == 2
    assert results[0]["buy_block"] == buy_conditions[0]
    assert {r["sell_block"]["threshold"] for r in results} == {70, 80}


_SAME_TRADES = [{"entryTime": "2026-06-01T00:00:00", "exitTime": "2026-06-02T00:00:00"}]
_OTHER_TRADES = [{"entryTime": "2026-06-05T00:00:00", "exitTime": "2026-06-06T00:00:00"}]


def _make_result(return_pct, buy_k_period, sell_period, trades):
    return {
        "return_pct": return_pct,
        "buy_block": {
            "indicator": "STOCH_D",
            "params": {"k_period": buy_k_period},
            "operator": "<",
            "threshold": 20,
        },
        "sell_block": {"indicator": "RSI", "params": {"period": sell_period}, "operator": ">", "threshold": 70},
        "trades": trades,
        "final_value": 1_000_000 * (1 + return_pct / 100),
    }


def test_dedup_keeps_shortest_period_among_identical_trade_sequences():
    results = [
        _make_result(5.0, buy_k_period=20, sell_period=20, trades=_SAME_TRADES),
        _make_result(5.0, buy_k_period=10, sell_period=14, trades=_SAME_TRADES),
        _make_result(5.0, buy_k_period=14, sell_period=14, trades=_SAME_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 1
    assert deduped[0]["buy_block"]["params"]["k_period"] == 10
    assert deduped[0]["sell_block"]["params"]["period"] == 14


def test_dedup_excludes_zero_trade_results():
    results = [_make_result(0.0, 10, 10, trades=[])]
    assert dedup_top_results(results, top_n=20) == []


def test_dedup_sorts_desc_and_caps_top_n():
    results = [
        _make_result(1.0, 10, 10, trades=_SAME_TRADES),
        _make_result(9.0, 10, 10, trades=_OTHER_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=1)
    assert len(deduped) == 1
    assert deduped[0]["return_pct"] == 9.0


def test_dedup_leaves_distinct_trade_sequences_untouched():
    results = [
        _make_result(3.0, 10, 10, trades=_SAME_TRADES),
        _make_result(7.0, 10, 10, trades=_OTHER_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 2
    assert [r["return_pct"] for r in deduped] == [7.0, 3.0]


def test_dedup_reports_dup_count_for_group_size():
    results = [
        _make_result(5.0, buy_k_period=20, sell_period=20, trades=_SAME_TRADES),
        _make_result(5.0, buy_k_period=10, sell_period=14, trades=_SAME_TRADES),
        _make_result(5.0, buy_k_period=14, sell_period=14, trades=_SAME_TRADES),
        _make_result(9.0, buy_k_period=10, sell_period=10, trades=_OTHER_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 2
    # 동일 거래 시퀀스를 만든 조합 3개 중 대표
    same_group = next(r for r in deduped if r["return_pct"] == 5.0)
    assert same_group["dup_count"] == 3
    # 서로 다른 거래 시퀀스 1개짜리 그룹
    other_group = next(r for r in deduped if r["return_pct"] == 9.0)
    assert other_group["dup_count"] == 1


def test_build_condition_grid_bb_percent_b_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    bb_buy = [b for b in buy_conditions if b["indicator"] == "BB_PERCENT_B"]
    bb_sell = [b for b in sell_conditions if b["indicator"] == "BB_PERCENT_B"]
    assert len(bb_buy) == 9
    assert {b["threshold"] for b in bb_buy} == {0.0, 0.1, 0.2}
    assert all(b["operator"] == "<" for b in bb_buy)
    assert len(bb_sell) == 9
    assert {b["threshold"] for b in bb_sell} == {0.8, 0.9, 1.0}
    assert all(b["operator"] == ">" for b in bb_sell)


def test_build_condition_grid_macd_ppo_param_grid_and_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    for indicator in ("MACD_PPO", "MACD_PPO_signal"):
        buy_blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        sell_blocks = [b for b in sell_conditions if b["indicator"] == indicator]
        assert len(buy_blocks) == 24
        assert len(sell_blocks) == 24
        param_combos = {tuple(sorted(b["params"].items())) for b in buy_blocks}
        assert len(param_combos) == 8
        assert {b["threshold"] for b in buy_blocks} == {-3, -2, -1}
        assert all(b["operator"] == "<" for b in buy_blocks)
        assert {b["threshold"] for b in sell_blocks} == {1, 2, 3}
        assert all(b["operator"] == ">" for b in sell_blocks)


def test_build_condition_grid_atr_pct_is_bidirectional():
    buy_conditions, sell_conditions = build_condition_grid()
    for conditions in (buy_conditions, sell_conditions):
        atr_blocks = [b for b in conditions if b["indicator"] == "ATR_PCT"]
        assert len(atr_blocks) == 36
        assert {b["operator"] for b in atr_blocks} == {"<", ">"}
        assert {b["threshold"] for b in atr_blocks} == {0.5, 1, 2, 3, 5, 8}
        assert {b["params"]["period"] for b in atr_blocks} == {10, 14, 20}


_MACD_SAME_TRADES = [{"entryTime": "2026-06-01T00:00:00", "exitTime": "2026-06-02T00:00:00"}]


def _make_macd_result(return_pct, fast, slow, signal, trades):
    return {
        "return_pct": return_pct,
        "buy_block": {
            "indicator": "MACD_PPO",
            "params": {"fast": fast, "slow": slow, "signal": signal},
            "operator": "<",
            "threshold": -2,
        },
        "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        "trades": trades,
        "final_value": 1_000_000 * (1 + return_pct / 100),
    }


def test_dedup_keeps_smallest_fast_slow_signal_sum_for_macd_style_params():
    results = [
        _make_macd_result(5.0, fast=16, slow=32, signal=12, trades=_MACD_SAME_TRADES),
        _make_macd_result(5.0, fast=12, slow=26, signal=9, trades=_MACD_SAME_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 1
    assert deduped[0]["buy_block"]["params"] == {"fast": 12, "slow": 26, "signal": 9}


def test_run_one_combo_returns_expected_shape():
    df = make_oscillating_df(n=200)
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}
    buy_block = {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}
    sell_block = {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}

    result = _run_one_combo(df, risk_config, buy_block, sell_block)

    assert set(result.keys()) == {"return_pct", "buy_block", "sell_block", "trades", "final_value"}
    assert result["buy_block"] == buy_block
    assert result["sell_block"] == sell_block
    assert isinstance(result["trades"], list)
    assert isinstance(result["return_pct"], float)


def test_check_candle_warmup_raises_when_insufficient():
    df = make_oscillating_df(n=10)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    with pytest.raises(SystemExit):
        _check_candle_warmup(df, buy_conditions, sell_conditions)


def test_check_candle_warmup_passes_when_sufficient():
    df = make_oscillating_df(n=200)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    _check_candle_warmup(df, buy_conditions, sell_conditions)


def test_macd_required_bars_matches_verified_formula():
    assert _macd_required_bars({"fast": 16, "slow": 32, "signal": 12}) == 43
    assert _macd_required_bars({"fast": 12, "slow": 26, "signal": 9}) == 34


def test_check_candle_warmup_catches_macd_composite_requirement():
    df = make_oscillating_df(n=35)  # passes max_required_period's max(32) but not the true 43
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [
        {"indicator": "MACD_PPO_signal", "params": {"fast": 16, "slow": 32, "signal": 12}, "operator": ">", "threshold": 1},
    ]
    with pytest.raises(SystemExit):
        _check_candle_warmup(df, buy_conditions, sell_conditions)


def test_check_candle_warmup_passes_when_macd_requirement_met():
    df = make_oscillating_df(n=43)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [
        {"indicator": "MACD_PPO_signal", "params": {"fast": 16, "slow": 32, "signal": 12}, "operator": ">", "threshold": 1},
    ]
    _check_candle_warmup(df, buy_conditions, sell_conditions)


def test_watchdog_expired_when_timeout_exceeded():
    assert _watchdog_expired(last_progress_time=0.0, now=301.0, timeout_sec=300) is True


def test_watchdog_not_expired_within_timeout():
    assert _watchdog_expired(last_progress_time=0.0, now=299.0, timeout_sec=300) is False


def test_watchdog_not_expired_exactly_at_timeout():
    assert _watchdog_expired(last_progress_time=0.0, now=300.0, timeout_sec=300) is False


def test_compute_grid_results_parallel_raises_on_watchdog_timeout():
    # The polling loop's very first `ar.ready()` check reads a 0 elapsed-time diff (it
    # happens microseconds after `last_progress` is set), so with watchdog_timeout=0 it
    # never trips on a trivially fast combo before the loop's mandatory 1s poll sleep lets
    # that combo finish first and mask the timeout entirely. The watchdog only fires
    # deterministically if the combo is STILL unfinished after that first 1s sleep, i.e.
    # combo compute time must clear the 1s sleep with real margin.
    #
    # n=8000 (~1.02s in-process, measured on this machine) sat right on that 1s boundary —
    # multiprocessing overhead (worker spawn, df pickling, IPC) usually pushed total task
    # time past 1s, but not reliably: a fast run could finish within the first sleep(1)
    # window, making the test flaky (fails ~1 in a few runs, both isolated and under full
    # suite). n=20000 (~2.54s in-process, more once multiprocessing overhead is added)
    # gives a 1.5s+ margin over the 1s sleep cadence, so it reliably survives into a second
    # or third poll iteration regardless of run-to-run timing variance.
    df = make_oscillating_df(n=20000)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}

    with pytest.raises(RuntimeError, match="워커 응답 없음"):
        compute_grid_results_parallel(
            df, buy_conditions, sell_conditions, risk_config,
            processes=1, watchdog_timeout=0,
        )
