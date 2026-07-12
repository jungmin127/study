"""
engine/sweep.py

코인×봉타입×신호(개별/혼합) 조합을 전부 스윕 실행하고 결과를 sweep_history에 기록한다.
캐시(run_backtest_cached)가 중복 실행을 막아주므로, 이미 실행한 조합을 다시 스윕해도
backtrader가 재실행되지 않는다 — sweep_history에만 새 행이 append된다.
"""
from __future__ import annotations

from datetime import datetime

from engine.cache import run_backtest_cached, save_sweep_result
from engine.strategies import SignalStrategy
from upbit_data_service import get_candles

DEFAULT_RISK_CONFIG = {
    "initial_capital": 10_000_000,
    "commission_rate": 0.0005,
    "position_sizing": "percent",
    "position_size": 100,
}


def run_sweep(
    markets: list[str],
    timeframes: list[str],
    signal_sets: list[tuple[str, list, bool]],
    start: datetime,
    end: datetime,
    risk_config: dict | None = None,
) -> None:
    """
    markets x timeframes x signal_sets 전 조합을 백테스트하고 sweep_history에 기록한다.

    Args:
        signal_sets: (표시용 이름, signals.Signal 리스트, 혼합 전략 여부) 튜플 리스트.
                     혼합 전략이면 signals 리스트에 신호를 2개 이상 넣고 세 번째 값을 True로.
        risk_config: 생략 시 DEFAULT_RISK_CONFIG 사용.
    """
    risk_config = risk_config or DEFAULT_RISK_CONFIG

    for market in markets:
        for timeframe in timeframes:
            df = get_candles(market, timeframe, start, end)
            for signal_set_name, signals, is_combined in signal_sets:
                try:
                    result = run_backtest_cached(
                        df=df,
                        strategy_cls=SignalStrategy,
                        risk_config=risk_config,
                        market=market,
                        timeframe=timeframe,
                        start=start,
                        end=end,
                        strategy_params={"signals": signals},
                    )
                except Exception as exc:
                    print(f"[run_sweep] 건너뜀 {signal_set_name}/{market}/{timeframe}: {exc}")
                    continue

                return_rate = (
                    (result["final_value"] - risk_config["initial_capital"])
                    / risk_config["initial_capital"]
                    * 100
                )
                save_sweep_result(
                    run_id=result["run_id"],
                    signal_set_name=signal_set_name,
                    is_combined=is_combined,
                    market=market,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    return_rate=return_rate,
                    sharpe=result["sharpe"],
                    max_drawdown=result["max_drawdown"],
                )


__all__ = ["run_sweep", "DEFAULT_RISK_CONFIG"]
