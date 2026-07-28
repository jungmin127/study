"""
engine/runner.py

bt.Cerebro 설정 및 실행, 분석기 결과 추출.
backtesting_1/backend/app/engine/runner.py 포팅 버전.
"""
from __future__ import annotations

import backtrader as bt
import pandas as pd


AUX_MARKET_LINE_NAME: dict[str, str] = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}
_OPTIONAL_LINE_CANDIDATES: tuple[str, ...] = ("trade_value", "fear_greed_value", *AUX_MARKET_LINE_NAME.values())


def build_data_feed_class(extra_lines: tuple[str, ...]) -> type[bt.feeds.PandasData]:
    """주어진 이름들을 추가 라인으로 갖는 PandasData 서브클래스를 동적으로 만든다.

    보조 컬럼 조합(거래대금, BTC 종가, USDT 종가, ...)이 늘어나도 조합마다 클래스를 손으로
    나열하지 않아도 되게 하기 위한 헬퍼 — 조합 수는 2^n으로 늘어나지만 이 함수는 필요한
    조합만 그때그때 만든다."""
    if not extra_lines:
        return bt.feeds.PandasData
    return type(
        "DynamicPandasData",
        (bt.feeds.PandasData,),
        {"lines": extra_lines, "params": tuple((name, name) for name in extra_lines)},
    )


class FractionalPercentSizer(bt.Sizer):
    """소수점 수량을 지원하는 퍼센트 사이저 (암호화폐 소수점 거래용)."""
    params = (("percents", 100),)

    def _getsizing(self, comminfo, cash, data, isbuy):
        if isbuy:
            pct = self.params.percents / 100.0
            price = data.close[0]
            comm_rate = comminfo.p.commission if hasattr(comminfo.p, 'commission') else 0.001
            size = cash * pct / (price * (1.0 + comm_rate + 0.005))
        else:
            size = self.broker.getposition(data).size
        return size


class EquityAnalyzer(bt.Analyzer):
    """각 bar의 포트폴리오 가치를 기록."""

    def start(self) -> None:
        self.rets: list[dict] = []

    def next(self) -> None:
        dt = self.data.datetime.datetime(0)
        self.rets.append({
            "timestamp": dt.isoformat(),
            "value": round(self.strategy.broker.getvalue(), 4),
        })

    def get_analysis(self) -> list[dict]:
        return self.rets


class TradeLogger(bt.Analyzer):
    """완료된 거래(trade.isclosed)를 기록. 미청산 포지션도 추적."""

    def start(self) -> None:
        self.trades: list[dict] = []
        self._open: dict[int, dict] = {}

    def notify_trade(self, trade: bt.Trade) -> None:
        if trade.isopen:
            self._open[trade.ref] = {
                "entryTime": bt.num2date(trade.dtopen).isoformat(),
                "entryPrice": round(trade.price, 8),
                "size": abs(trade.size),
                "baropen": trade.baropen,
            }
            return

        if not trade.isclosed:
            return

        open_info = self._open.pop(trade.ref, None)
        size = open_info["size"] if open_info else 1

        entry_price = trade.price
        exit_price = entry_price + trade.pnl / size if size else entry_price
        return_rate = (trade.pnlcomm / (entry_price * size) * 100) if (entry_price and size) else 0.0

        self.trades.append({
            "entryTime": bt.num2date(trade.dtopen).isoformat(),
            "exitTime": bt.num2date(trade.dtclose).isoformat(),
            "entryPrice": round(entry_price, 8),
            "exitPrice": round(exit_price, 8),
            "returnRate": round(return_rate, 4),
            "holdingPeriod": int(trade.barclose - trade.baropen),
            "pnl": round(trade.pnlcomm, 4),
            "forceClosed": False,
            "size": round(size, 8),
        })

    def get_analysis(self) -> list[dict]:
        return self.trades

    def get_open_trades(self) -> list[dict]:
        return list(self._open.values())


def _build_forced_close_trade(
    entry_time: str,
    entry_price: float,
    size: float,
    baropen: int,
    last_close: float,
    last_dt: str,
    total_bars: int,
    commission_rate: float,
) -> dict:
    """백테스트 종료 시점까지 매도 조건을 만족하지 못한 포지션을, 리포팅을 위해
    마지막 봉 종가로 강제 청산 처리한 거래 기록을 만든다. 진입/청산 양쪽 수수료를
    모두 차감해야 정상 청산 거래(trade.pnlcomm)와 계산 방식이 일치한다."""
    pnl_gross = (last_close - entry_price) * size
    entry_commission = entry_price * size * commission_rate
    exit_commission = last_close * size * commission_rate
    pnlcomm = pnl_gross - entry_commission - exit_commission
    return_rate = (pnlcomm / (entry_price * size) * 100) if (entry_price and size) else 0.0
    holding_period = max(total_bars - 1 - baropen, 0)

    return {
        "entryTime": entry_time,
        "exitTime": last_dt,
        "entryPrice": round(entry_price, 8),
        "exitPrice": round(last_close, 8),
        "returnRate": round(return_rate, 4),
        "holdingPeriod": holding_period,
        "pnl": round(pnlcomm, 4),
        "forceClosed": True,
        "size": round(size, 8),
    }


def run_backtest(
    df: pd.DataFrame,
    strategy_cls: type[bt.Strategy],
    risk_config: dict,
    strategy_params: dict | None = None,
) -> dict:
    """
    백테스트를 실행하고 결과를 반환.

    Args:
        df: OHLCV DataFrame (컬럼: candle_time, open, high, low, close, volume)
        strategy_cls: bt.Strategy 서브클래스
        risk_config: {initial_capital, commission_rate, position_sizing,
                       position_size, stop_loss, take_profit, trailing_stop}
        strategy_params: 전략 파라미터 (addstrategy에 키워드 인수로 전달)

    Returns:
        {equity_curve, trades, final_value, sharpe, max_drawdown}
    """
    if strategy_params is None:
        strategy_params = {}

    df_bt = df.copy()
    df_bt = df_bt.set_index("candle_time")

    if df_bt.index.tz is not None:
        df_bt.index = df_bt.index.tz_localize(None)

    extra_lines = tuple(name for name in _OPTIONAL_LINE_CANDIDATES if name in df_bt.columns)
    feed_kwargs = {
        "dataname": df_bt, "open": "open", "high": "high", "low": "low", "close": "close",
        "volume": "volume", "openinterest": -1,
    }
    feed_kwargs.update({name: name for name in extra_lines})
    data_feed = build_data_feed_class(extra_lines)(**feed_kwargs)

    cerebro = bt.Cerebro()
    cerebro.adddata(data_feed)

    initial_capital: float = float(risk_config.get("initial_capital", 10000))
    commission_rate: float = float(risk_config.get("commission_rate", 0.001))
    position_size: float = float(risk_config.get("position_size", 100))

    cerebro.broker.setcash(initial_capital)
    cerebro.broker.setcommission(commission=commission_rate)
    cerebro.addsizer(FractionalPercentSizer, percents=min(position_size, 100))

    cerebro.addstrategy(strategy_cls, **strategy_params)

    cerebro.addanalyzer(EquityAnalyzer, _name="equity")
    cerebro.addanalyzer(TradeLogger, _name="trades")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

    results = cerebro.run()
    strategy = results[0]

    equity_curve: list[dict] = strategy.analyzers.equity.get_analysis()
    trades: list[dict] = strategy.analyzers.trades.get_analysis()
    final_value: float = round(cerebro.broker.getvalue(), 4)

    sharpe_analysis = strategy.analyzers.sharpe.get_analysis()
    sharpe_ratio = sharpe_analysis.get("sharperatio")

    drawdown_analysis = strategy.analyzers.drawdown.get_analysis()
    max_drawdown_pct = drawdown_analysis.get("max", {}).get("drawdown")

    open_trades = strategy.analyzers.trades.get_open_trades()
    if open_trades:
        last_close = float(df_bt["close"].iloc[-1])
        last_dt = df_bt.index[-1].isoformat()
        total_bars = len(df_bt)

        for ot in open_trades:
            trades.append(_build_forced_close_trade(
                entry_time=ot["entryTime"],
                entry_price=ot["entryPrice"],
                size=ot["size"],
                baropen=ot["baropen"],
                last_close=last_close,
                last_dt=last_dt,
                total_bars=total_bars,
                commission_rate=commission_rate,
            ))

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "final_value": final_value,
        "sharpe": sharpe_ratio,
        "max_drawdown": max_drawdown_pct,
    }


__all__ = [
    "run_backtest",
    "build_data_feed_class",
    "AUX_MARKET_LINE_NAME",
]
