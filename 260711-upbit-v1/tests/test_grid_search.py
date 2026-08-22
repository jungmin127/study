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


def test_check_candle_warmup_includes_base_group_macd_requirement():
    """체이닝 시 베이스 조건(base_sell_group)의 MACD 조합형 워밍업 요구량도 반영해야
    한다 — 새 풀만 보면 통과하지만 베이스를 포함하면 부족한 경우를 잡아야 한다
    (최종 리뷰 Critical #1 회귀 테스트)."""
    df = make_oscillating_df(n=35)  # 새 풀(RSI)만 보면 충분하지만 베이스(MACD 조합)는 43봉 필요
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    base_sell_group = {
        "type": "AND",
        "conditions": [
            {"indicator": "MACD_PPO_signal", "params": {"fast": 16, "slow": 32, "signal": 12}, "operator": ">", "threshold": 1},
        ],
    }
    with pytest.raises(SystemExit):
        _check_candle_warmup(df, buy_conditions, sell_conditions, base_sell_group=base_sell_group)


def test_check_candle_warmup_passes_when_base_group_requirement_met():
    df = make_oscillating_df(n=43)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    base_sell_group = {
        "type": "AND",
        "conditions": [
            {"indicator": "MACD_PPO_signal", "params": {"fast": 16, "slow": 32, "signal": 12}, "operator": ">", "threshold": 1},
        ],
    }
    _check_candle_warmup(df, buy_conditions, sell_conditions, base_sell_group=base_sell_group)


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


def test_build_condition_grid_default_pool_unchanged():
    buy_conditions, sell_conditions = build_condition_grid()
    assert len(buy_conditions) == 138
    assert len(sell_conditions) == 150


def test_build_condition_grid_with_trend_pool_only():
    buy_conditions, sell_conditions = build_condition_grid({"categories": ["추세"], "excluded_indicators": []})
    buy_indicators = {b["indicator"] for b in buy_conditions}
    assert buy_indicators == {"SMA_PCT", "EMA_PCT", "WMA_PCT", "MOMENTUM_PCT"}
    # 손익(SELL_ONLY)은 풀 선택과 무관하게 항상 매도 조건에 포함된다
    sell_indicators = {s["indicator"] for s in sell_conditions}
    assert {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS"} <= sell_indicators
    assert "RSI" not in buy_indicators


def test_build_condition_grid_excludes_individual_indicators():
    buy_conditions, _ = build_condition_grid({"categories": ["추세"], "excluded_indicators": ["MOMENTUM_PCT"]})
    buy_indicators = {b["indicator"] for b in buy_conditions}
    assert buy_indicators == {"SMA_PCT", "EMA_PCT", "WMA_PCT"}


def test_build_condition_grid_market_sentiment_pool_has_no_param_indicators():
    buy_conditions, _ = build_condition_grid({"categories": ["시장 심리"], "excluded_indicators": []})
    fear_greed_blocks = [b for b in buy_conditions if b["indicator"] == "FEAR_GREED_CMC"]
    assert fear_greed_blocks
    assert all(b["params"] == {} for b in fear_greed_blocks)


def test_build_condition_grid_excludes_btc_correlation_for_krw_btc_market():
    """KRW-BTC를 백테스트하면 BTC_CORRELATION은 대상 코인 종가와 KRW-BTC 종가를 비교하는
    자기상관이 되어 항상 정확히 1.0으로 퇴화한다(engine/indicators/market.py의
    RollingCorrelation(data.close, data.btc_close, ...) 참고) — 풀에서 제외해야 한다."""
    buy_conditions, sell_conditions = build_condition_grid(
        {"categories": ["시장 심리"], "excluded_indicators": []}, market="KRW-BTC"
    )
    indicators = {b["indicator"] for b in buy_conditions} | {s["indicator"] for s in sell_conditions}
    assert "BTC_CORRELATION" not in indicators
    assert "USDT_CORRELATION" in indicators  # 다른 마켓의 자기상관 지표는 영향받지 않는다


def test_build_condition_grid_excludes_usdt_correlation_for_krw_usdt_market():
    buy_conditions, sell_conditions = build_condition_grid(
        {"categories": ["시장 심리"], "excluded_indicators": []}, market="KRW-USDT"
    )
    indicators = {b["indicator"] for b in buy_conditions} | {s["indicator"] for s in sell_conditions}
    assert "USDT_CORRELATION" not in indicators
    assert "BTC_CORRELATION" in indicators


def test_build_condition_grid_keeps_correlation_indicators_for_other_markets():
    buy_conditions, _ = build_condition_grid(
        {"categories": ["시장 심리"], "excluded_indicators": []}, market="KRW-ETH"
    )
    indicators = {b["indicator"] for b in buy_conditions}
    assert {"BTC_CORRELATION", "USDT_CORRELATION"} <= indicators


def test_wrap_condition_without_base_matches_existing_shape():
    from scripts.grid_search import _wrap_condition

    block = {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}
    assert _wrap_condition(block, None, "AND") == {"type": "AND", "conditions": [block]}


def test_wrap_condition_with_base_nests_group():
    from scripts.grid_search import _wrap_condition

    base = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]}
    candidate = {"indicator": "SMA_PCT", "params": {"period": 14}, "operator": "<", "threshold": -1.0}
    assert _wrap_condition(candidate, base, "OR") == {"type": "OR", "conditions": [base, candidate]}


def test_wrap_condition_sell_only_block_forces_or_regardless_of_combinator():
    """손절/익절/보유기간(SELL_ONLY) 조건은 combinator로 AND를 요청해도 항상 OR로 묶여야
    한다 — AND로 묶이면 청산 안전장치가 사실상 무력화된다(최종 리뷰 Important #2)."""
    from scripts.grid_search import _wrap_condition

    base = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]}
    stop_loss_block = {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5}
    assert _wrap_condition(stop_loss_block, base, "AND") == {"type": "OR", "conditions": [base, stop_loss_block]}


def test_wrap_condition_non_sell_only_block_respects_requested_combinator():
    from scripts.grid_search import _wrap_condition

    base = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]}
    candidate = {"indicator": "SMA_PCT", "params": {"period": 14}, "operator": "<", "threshold": -1.0}
    assert _wrap_condition(candidate, base, "AND") == {"type": "AND", "conditions": [base, candidate]}


def test_run_one_combo_uses_base_group_when_provided():
    from engine.sweep import DEFAULT_RISK_CONFIG
    from scripts.grid_search import _run_one_combo
    from tests.signal_fixtures import make_oscillating_df

    df = make_oscillating_df()
    buy_block = {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 90}  # 거의 항상 참
    sell_block = {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 10}  # 거의 항상 참
    base_buy = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 0}]}  # 항상 거짓
    result = _run_one_combo(
        df, DEFAULT_RISK_CONFIG, buy_block, sell_block,
        base_buy_group=base_buy, base_sell_group=None, combinator="AND",
    )
    # base_buy가 항상 거짓인 AND 결합이므로, 매수 조건 자체가 성립할 수 없어 거래가 0건이어야 한다
    assert result["trades"] == []


def test_main_parses_categories_and_exclude_indicators_args(monkeypatch, capsys):
    import sys
    from scripts import grid_search

    captured_pool = {}

    def fake_build_condition_grid(pool=None, market=None):
        captured_pool["pool"] = pool
        return [], []

    monkeypatch.setattr(grid_search, "build_condition_grid", fake_build_condition_grid)
    monkeypatch.setattr(
        sys, "argv",
        [
            "grid_search.py", "--market", "KRW-ETH", "--timeframe", "minutes60",
            "--capital", "1000000", "--start", "2026-01-01", "--end", "2026-01-02",
            "--categories", "오실레이터,추세", "--exclude-indicators", "MOMENTUM_PCT",
        ],
    )
    try:
        grid_search.main()
    except SystemExit:
        pass  # 빈 조합이라 캔들 조회 이후 어딘가에서 중단돼도 괜찮다 — 파싱 결과만 검증
    assert captured_pool["pool"] == {
        "categories": ["오실레이터", "추세"],
        "excluded_indicators": ["MOMENTUM_PCT"],
    }


def test_main_includes_base_conditions_in_aux_data_detection(monkeypatch):
    """체이닝 시 베이스 조건에 등장하는 지표(예: 시장심리 카테고리)가 새 풀에 없어도,
    _fetch_backtest_dataframe에 넘기는 감지용 트리에 베이스 조건이 포함돼야 그 지표가
    필요로 하는 보조 데이터(btc_close 등)가 함께 병합된다(최종 리뷰 Critical #1 회귀 테스트)."""
    import sys
    from scripts import grid_search
    from engine.condition_tree import collect_blocks

    base_sell = {
        "type": "AND",
        "conditions": [{"indicator": "BTC_CORRELATION", "params": {"period": 20}, "operator": ">", "threshold": 0.7}],
    }
    base_buy = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
    }
    monkeypatch.setattr(
        grid_search, "get_run_config",
        lambda run_id: {"buy_conditions": base_buy, "sell_conditions": base_sell},
    )
    monkeypatch.setattr(
        grid_search, "build_condition_grid",
        lambda pool=None, market=None: (
            [{"indicator": "SMA_PCT", "params": {"period": 20}, "operator": "<", "threshold": -1.0}],
            [{"indicator": "EMA_PCT", "params": {"period": 20}, "operator": ">", "threshold": 1.0}],
        ),
    )
    captured = {}

    class _StopEarly(Exception):
        pass

    def fake_fetch(market, timeframe, start_dt, end_dt, buy_group, sell_group):
        captured["buy_group"] = buy_group
        captured["sell_group"] = sell_group
        raise _StopEarly()

    monkeypatch.setattr(grid_search, "_fetch_backtest_dataframe", fake_fetch)
    monkeypatch.setattr(
        sys, "argv",
        [
            "grid_search.py", "--market", "KRW-ETH", "--timeframe", "minutes60",
            "--capital", "1000000", "--start", "2026-01-01", "--end", "2026-01-02",
            "--categories", "추세", "--base-run-id", "base-abc", "--combinator", "AND",
        ],
    )
    with pytest.raises(_StopEarly):
        grid_search.main()

    sell_indicators = {b["indicator"] for b in collect_blocks(captured["sell_group"])}
    assert "BTC_CORRELATION" in sell_indicators
    buy_indicators = {b["indicator"] for b in collect_blocks(captured["buy_group"])}
    assert "RSI" in buy_indicators


def test_main_raises_when_base_run_id_not_found(monkeypatch):
    import sys
    from scripts import grid_search

    monkeypatch.setattr(grid_search, "get_run_config", lambda run_id: None)
    monkeypatch.setattr(
        sys, "argv",
        [
            "grid_search.py", "--market", "KRW-ETH", "--timeframe", "minutes60",
            "--capital", "1000000", "--start", "2026-01-01", "--end", "2026-01-02",
            "--base-run-id", "nonexistent-id-xyz", "--combinator", "AND",
        ],
    )
    with pytest.raises(SystemExit):
        grid_search.main()


def test_main_raises_when_base_run_id_has_no_conditions(monkeypatch):
    """get_run_config()가 None이 아닌 dict를 반환하더라도(예: ConditionTreeStrategy가
    아닌 SignalStrategy 결과), buy_conditions/sell_conditions가 None이면 체이닝이
    불가능하므로 명확한 SystemExit으로 실패해야 한다 — 조용히 일반 그리드서치로
    진행되면 안 된다."""
    import sys
    from scripts import grid_search

    monkeypatch.setattr(
        grid_search, "get_run_config",
        lambda run_id: {"buy_conditions": None, "sell_conditions": None},
    )
    monkeypatch.setattr(
        sys, "argv",
        [
            "grid_search.py", "--market", "KRW-ETH", "--timeframe", "minutes60",
            "--capital", "1000000", "--start", "2026-01-01", "--end", "2026-01-02",
            "--base-run-id", "signal-strategy-run-id", "--combinator", "AND",
        ],
    )
    with pytest.raises(SystemExit):
        grid_search.main()
