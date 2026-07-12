from engine.runner import run_backtest
from engine.strategies import SignalStrategy
from signals import BollingerBandSignal, MacdCrossSignal, RsiZoneSignal, SmaCrossSignal, SIGNAL_REGISTRY
from tests.signal_fixtures import make_oscillating_df

RISK_CONFIG = {"initial_capital": 10000, "commission_rate": 0.001, "position_sizing": "percent", "position_size": 100}


def test_macd_cross_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [MacdCrossSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_rsi_zone_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [RsiZoneSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_sma_cross_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [SmaCrossSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_bollinger_band_signal_trades_on_oscillating_data():
    df = make_oscillating_df()
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [BollingerBandSignal()]})

    assert len(result["trades"]) >= 1
    assert any(t["forceClosed"] is False for t in result["trades"])


def test_signal_registry_contains_all_four_signals():
    assert set(SIGNAL_REGISTRY.keys()) == {"macd_cross", "rsi_zone", "sma_cross", "bollinger_band"}


def test_signal_registry_extensible_without_touching_other_modules(monkeypatch):
    """새 신호를 레지스트리에 등록하면 (engine/sweep.py 등 다른 모듈 수정 없이)
    바로 SignalStrategy에서 사용할 수 있어야 한다 — 확장성 요구사항 검증."""

    class DummyAlwaysBuySignal:
        def setup(self, strategy) -> None:
            pass

        def should_buy(self, strategy) -> bool:
            return True

        def should_sell(self, strategy) -> bool:
            return False

    monkeypatch.setitem(SIGNAL_REGISTRY, "dummy_always_buy", DummyAlwaysBuySignal())

    assert "dummy_always_buy" in SIGNAL_REGISTRY
    df = make_oscillating_df(n=20)
    signal = SIGNAL_REGISTRY["dummy_always_buy"]
    result = run_backtest(df, SignalStrategy, RISK_CONFIG, strategy_params={"signals": [signal]})
    # DummyAlwaysBuySignal은 항상 매수이므로 첫 bar에 바로 진입해야 함
    assert len(result["trades"]) == 1
