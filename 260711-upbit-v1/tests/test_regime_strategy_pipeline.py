from datetime import datetime, timezone

import pytest

from scripts.regime_strategy_pipeline import adjust_window, augment_with_tp_sl, min_trades_for_days, run_grid_for_window, select_target_segments, top_candidates
from scripts.regime_strategy_pipeline import pick_final_strategy
from scripts.regime_strategy_pipeline import save_and_map
from scripts.regime_strategy_pipeline import (
    _ensure_supported_market, parse_args, print_summary_table, run_pipeline,
)


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
    assert len(result["conditions"]) == 3
    assert result["conditions"][0] == base_sell
    assert result["conditions"][1] == {
        "indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5,
    }
    assert result["conditions"][2] == {
        "indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 8,
    }


def test_augment_with_tp_sl_adds_trailing_stop_step_when_given():
    base_sell = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
    }

    result = augment_with_tp_sl(base_sell, stop_loss_pct=-5, take_profit_pct=8, trailing_stop_step_pct=3)

    assert len(result["conditions"]) == 4
    assert result["conditions"][3] == {
        "indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 3,
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


def test_pick_final_strategy_tries_all_candidates_and_picks_highest_return(monkeypatch):
    # 그리드서치 철학: 순서상 먼저 통과하는 후보가 아니라, 조건을 만족하는 후보 전체
    # 중 최종 수익률이 가장 높은 것을 채택한다 — 그래서 raw_return_pct가 더 낮은
    # 두 번째 후보(8.0)가 증강 후 실제로는 더 높은 수익률(final_value 1_200_000)을
    # 내면 그쪽이 채택돼야 한다.
    call_count = {"n": 0}

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        call_count["n"] += 1
        final_value = 1_100_000 if call_count["n"] == 1 else 1_200_000
        return {"trades": [{"pnl": 1.0}] * 5, "final_value": final_value}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4), _candidate(8.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert call_count["n"] == 2  # 두 후보 모두 시도해야 한다(선착순 아님)
    assert final["raw_return_pct"] == 8.0
    assert final["raw_trade_count"] == 4
    assert final["return_pct"] == 20.0
    assert final["sell_conditions"]["type"] == "OR"
    assert final["trailing_stop_step_pct"] is None


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


def test_pick_final_strategy_grid_searches_trailing_stop_step_pcts_and_picks_best(monkeypatch):
    # step별로 다른 final_value를 리턴하도록 해서, 후보 1개 x step 3개(2,3,5) 조합
    # 전체를 다 시도한 뒤 그중 최고 수익률(step=3일 때)을 채택하는지 확인한다.
    seen_steps = []

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        sell = strategy_params["sell_conditions"]
        trailing_cond = next(c for c in sell["conditions"] if c.get("indicator") == "TRAILING_STOP_STEP_PCT")
        step = trailing_cond["threshold"]
        seen_steps.append(step)
        final_value = {2: 1_050_000, 3: 1_300_000, 5: 1_100_000}[step]
        return {"trades": [{"pnl": 1.0}] * 5, "final_value": final_value}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(
        None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8,
        trailing_stop_step_pcts=[2, 3, 5],
    )

    assert sorted(seen_steps) == [2, 3, 5]
    assert final["trailing_stop_step_pct"] == 3
    assert final["return_pct"] == 30.0


def test_save_and_map_builds_title_and_maps_to_library(monkeypatch):
    captured = {}

    def fake_run_backtest_cached(**kwargs):
        captured["cached_kwargs"] = kwargs
        return {"run_id": "abc123"}

    def fake_upsert(market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json):
        captured["upsert_args"] = {
            "market": market, "regime": regime, "source_run_id": source_run_id,
            "timeframe": timeframe,
        }

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest_cached", fake_run_backtest_cached)
    monkeypatch.setattr("trading.db.upsert_regime_strategy_mapping", fake_upsert)

    final = {
        "buy_conditions": {"type": "AND", "conditions": []},
        "sell_conditions": {"type": "OR", "conditions": []},
        "return_pct": 12.5, "trades": [{}] * 5,
        "raw_return_pct": 10.0, "raw_trade_count": 4,
    }
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 20, tzinfo=timezone.utc)
    risk_config = {"initial_capital": 1_000_000}

    run_id = save_and_map(
        "KRW-ETH", "상승", start, end, final,
        df=None, risk_config=risk_config, stop_loss_pct=-5, take_profit_pct=8,
    )

    assert run_id == "abc123"
    assert captured["cached_kwargs"]["title"] == "[상승] KRW-ETH 2026-06-01~2026-06-20 그리드+TP8%/SL5%"
    assert "10.00%(4건)" in captured["cached_kwargs"]["description"]
    assert "12.50%(5건)" in captured["cached_kwargs"]["description"]
    assert captured["upsert_args"]["market"] == "KRW-ETH"
    assert captured["upsert_args"]["regime"] == "상승"
    assert captured["upsert_args"]["source_run_id"] == "abc123"


def test_save_and_map_adds_trailing_stop_suffix_when_present(monkeypatch):
    captured = {}

    def fake_run_backtest_cached(**kwargs):
        captured["cached_kwargs"] = kwargs
        return {"run_id": "abc123"}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest_cached", fake_run_backtest_cached)
    monkeypatch.setattr("trading.db.upsert_regime_strategy_mapping", lambda *a, **k: None)

    final = {
        "buy_conditions": {"type": "AND", "conditions": []},
        "sell_conditions": {"type": "OR", "conditions": []},
        "return_pct": 12.5, "trades": [{}] * 5,
        "raw_return_pct": 10.0, "raw_trade_count": 4,
        "trailing_stop_step_pct": 3,
    }
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 20, tzinfo=timezone.utc)
    risk_config = {"initial_capital": 1_000_000}

    save_and_map(
        "KRW-ETH", "상승", start, end, final,
        df=None, risk_config=risk_config, stop_loss_pct=-5, take_profit_pct=8,
    )

    assert captured["cached_kwargs"]["title"] == "[상승] KRW-ETH 2026-06-01~2026-06-20 그리드+TP8%/SL5%/TS3%"


def test_ensure_supported_market_rejects_unknown_market():
    with pytest.raises(SystemExit):
        _ensure_supported_market("KRW-NOTREAL")


def test_ensure_supported_market_accepts_major_market():
    _ensure_supported_market("KRW-ETH")


def test_parse_args_defaults():
    args = parse_args(["--market", "KRW-ETH", "--history-start", "2026-01-01"])

    assert args.market == "KRW-ETH"
    assert args.history_start == "2026-01-01"
    assert args.capital == 10_000_000
    assert args.min_days == 10
    assert args.stop_loss_pct == -5.0
    assert args.take_profit_pct == 8.0
    assert args.candidate_pool == 20
    assert args.trailing_stop_step_pcts is None


def test_parse_args_parses_trailing_stop_step_pcts_list():
    args = parse_args([
        "--market", "KRW-ETH", "--history-start", "2026-01-01",
        "--trailing-stop-step-pcts", "2,3,4,5",
    ])

    assert args.trailing_stop_step_pcts == [2.0, 3.0, 4.0, 5.0]


def test_parse_args_rejects_non_positive_trailing_stop_step_pct():
    with pytest.raises(SystemExit):
        parse_args([
            "--market", "KRW-ETH", "--history-start", "2026-01-01",
            "--trailing-stop-step-pcts", "2,0,5",
        ])


def test_run_pipeline_isolates_label_failures(monkeypatch):
    segments = {
        "하락": None,
        "횡보": {
            "start": "2026-02-01T00:00:00+00:00", "end": "2026-02-10T00:00:00+00:00",
            "label": "횡보", "bar_count": 240, "in_progress": False,
        },
        "상승": {
            "start": "2026-03-01T00:00:00+00:00", "end": "2026-03-15T00:00:00+00:00",
            "label": "상승", "bar_count": 360, "in_progress": True,
        },
    }
    monkeypatch.setattr("scripts.regime_strategy_pipeline.select_target_segments", lambda market, history_start: segments)

    def fake_run_grid_for_window(market, start, end, capital):
        # adjust_window가 짧은 세그먼트(횡보, 9일<min_days10)의 start를 1월로
        # 당기므로 start가 아니라 항상 고정인 end로 라벨을 구분한다.
        if end.month == 2:
            raise SystemExit("워밍업 부족")
        return {
            "df": None, "risk_config": {"initial_capital": capital},
            "results": [{"return_pct": 5.0, "trades": [{}, {}, {}]}],
        }

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_grid_for_window", fake_run_grid_for_window)
    monkeypatch.setattr(
        "scripts.regime_strategy_pipeline.top_candidates",
        lambda results, min_trades, pool: [{
            "return_pct": 5.0, "trades": [{}, {}, {}],
            "buy_block": {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
            "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        }],
    )
    monkeypatch.setattr(
        "scripts.regime_strategy_pipeline.pick_final_strategy",
        lambda df, candidates, risk_config, min_trades, sl, tp, trailing_stop_step_pcts=None: {
            "buy_conditions": {}, "sell_conditions": {}, "return_pct": 6.0, "trades": [{}, {}, {}],
            "raw_return_pct": 5.0, "raw_trade_count": 3, "trailing_stop_step_pct": None,
        },
    )
    monkeypatch.setattr("scripts.regime_strategy_pipeline.save_and_map", lambda *a, **k: "run-xyz")

    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    summary = run_pipeline("KRW-ETH", history_start, 10_000_000, 10, -5.0, 8.0, 20)

    by_regime = {row["regime"]: row for row in summary}
    assert by_regime["하락"]["status"] == "skipped"
    assert by_regime["하락"]["reason"] == "탐지된 구간 없음"
    assert by_regime["횡보"]["status"] == "failed"
    assert "워밍업 부족" in by_regime["횡보"]["reason"]
    assert by_regime["횡보"]["segment_bar_count"] == 240
    assert by_regime["횡보"]["segment_in_progress"] is False
    assert by_regime["상승"]["status"] == "mapped"
    assert by_regime["상승"]["run_id"] == "run-xyz"
    assert by_regime["상승"]["segment_bar_count"] == 360
    assert by_regime["상승"]["segment_in_progress"] is True


def test_run_pipeline_rejects_non_negative_stop_loss_before_segment_detection(monkeypatch):
    def _fail_if_called(market, history_start):
        raise AssertionError("stop_loss_pct 부호 검증 전에 select_target_segments가 호출되면 안 된다")

    monkeypatch.setattr("scripts.regime_strategy_pipeline.select_target_segments", _fail_if_called)
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(SystemExit) as exc_info:
        run_pipeline("KRW-ETH", history_start, 10_000_000, 10, 5.0, 8.0, 20)

    assert "stop_loss_pct" in str(exc_info.value)


def test_run_pipeline_rejects_non_positive_take_profit_before_segment_detection(monkeypatch):
    def _fail_if_called(market, history_start):
        raise AssertionError("take_profit_pct 부호 검증 전에 select_target_segments가 호출되면 안 된다")

    monkeypatch.setattr("scripts.regime_strategy_pipeline.select_target_segments", _fail_if_called)
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(SystemExit) as exc_info:
        run_pipeline("KRW-ETH", history_start, 10_000_000, 10, -5.0, -8.0, 20)

    assert "take_profit_pct" in str(exc_info.value)


def test_print_summary_table_smoke(capsys):
    summary = [
        {"regime": "하락", "status": "skipped", "reason": "탐지된 구간 없음"},
        {
            "regime": "상승", "status": "mapped", "run_id": "abc",
            "period": "2026-03-01~2026-03-15", "return_pct": 12.34, "trade_count": 5,
        },
    ]

    print_summary_table(summary)

    captured = capsys.readouterr()
    assert "하락" in captured.out
    assert "상승" in captured.out
    assert "12.34" in captured.out


def test_print_summary_table_warns_on_in_progress_or_short_segment(capsys):
    summary = [
        {
            # in_progress=True인 경우 — MIN_SEGMENT_BARS 이상이어도 경고해야 한다.
            "regime": "상승", "status": "mapped", "run_id": "abc",
            "period": "2026-03-01~2026-03-15", "return_pct": 12.34, "trade_count": 5,
            "segment_bar_count": 999, "segment_in_progress": True,
        },
        {
            # bar_count가 MIN_SEGMENT_BARS 미만인 경우(진행중 아니어도) 경고해야 한다.
            "regime": "횡보", "status": "mapped", "run_id": "def",
            "period": "2026-02-01~2026-02-05", "return_pct": 1.0, "trade_count": 3,
            "segment_bar_count": 10, "segment_in_progress": False,
        },
        {
            # 충분히 길고 진행중도 아니면 경고 없이 조용해야 한다.
            "regime": "하락", "status": "mapped", "run_id": "ghi",
            "period": "2026-01-01~2026-01-20", "return_pct": 3.0, "trade_count": 4,
            "segment_bar_count": 480, "segment_in_progress": False,
        },
    ]

    print_summary_table(summary)

    captured = capsys.readouterr()
    lines = {line.split()[0]: line for line in captured.out.splitlines() if line}
    assert "[!]" in lines["상승"]
    assert "[!]" in lines["횡보"]
    assert "[!]" not in lines["하락"]
