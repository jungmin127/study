import statistics

import backtrader as bt
import pandas as pd
import pytest

from engine.condition_tree import get_indicator_value
from engine.indicators import INDICATOR_FACTORY
from engine.indicators import price_levels
from tests.signal_fixtures import make_oscillating_df
from engine.runner import build_data_feed_class, run_backtest


class _ProbeStrategy(bt.Strategy):
    params = (("indicator", "RSI"), ("indicator_params", {}))

    def __init__(self):
        create_fn = INDICATOR_FACTORY[self.p.indicator]
        self.probe = create_fn(self.data, **self.p.indicator_params)
        self.seen_values: list[float] = []

    def next(self):
        self.seen_values.append(get_indicator_value(self.p.indicator, self.probe))


def _run_probe(indicator: str, params: dict) -> list[float]:
    df = make_oscillating_df()
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


_NEEDS_EXTRA_LINE = {"MARKET_TREND", "BTC_CORRELATION", "USDT_CORRELATION", "FEAR_GREED_CMC", "KOREA_PREMIUM"}  # btc_close/usdt_close 데이터 라인이 필요 — test_market_trend_matches_manual_close_minus_sma_of_btc_close_line 등 참고
_NEEDS_TRADE_VALUE_LINE = {"TRADE_VALUE", "TRADE_VALUE_SMA"}  # trade_value 라인이 필요 — test_trade_value_* 참고


def test_all_registered_indicators_produce_values():
    for name in INDICATOR_FACTORY:
        if name in _NEEDS_EXTRA_LINE or name in _NEEDS_TRADE_VALUE_LINE:
            continue
        values = _run_probe(name, {})
        assert len(values) > 0, f"{name} 지표가 값을 하나도 생성하지 못함"


def test_sma_matches_manual_average():
    values = _run_probe("SMA", {"period": 5})
    df = make_oscillating_df()
    manual = df["close"].rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def _run_probe_with_aux(indicator: str, params: dict, aux_line: str, aux_series) -> list[float]:
    df = make_oscillating_df()
    df[aux_line] = aux_series
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)
    cerebro = bt.Cerebro()
    cerebro.adddata(
        build_data_feed_class((aux_line,))(
            dataname=df_bt, open="open", high="high", low="low", close="close",
            volume="volume", openinterest=-1, **{aux_line: aux_line},
        )
    )
    cerebro.addstrategy(_ProbeStrategy, indicator=indicator, indicator_params=params)
    results = cerebro.run()
    return results[0].seen_values


def test_market_trend_matches_manual_close_minus_sma_of_btc_close_line():
    df = make_oscillating_df()
    btc_close = df["close"] * 2 + 1000  # 대상 마켓과 스케일이 다른 별도 시세임을 검증하기 위해 배율을 둠
    values = _run_probe_with_aux("MARKET_TREND", {"period": 5}, "btc_close", btc_close)
    manual = (btc_close - btc_close.rolling(5).mean()).iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def test_momentum_pct_matches_manual_pct_change_over_period():
    values = _run_probe("MOMENTUM_PCT", {"period": 5})
    df = make_oscillating_df()
    manual = (df["close"].iloc[-1] - df["close"].iloc[-6]) / df["close"].iloc[-6] * 100
    assert abs(values[-1] - manual) < 1e-6


def test_trade_value_matches_raw_trade_value_column():
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    values = _run_probe_with_aux("TRADE_VALUE", {}, "trade_value", trade_value)
    assert abs(values[-1] - trade_value.iloc[-1]) < 1e-6


def test_trade_value_sma_matches_manual_average_of_trade_value():
    df = make_oscillating_df()
    trade_value = df["close"] * df["volume"]
    values = _run_probe_with_aux("TRADE_VALUE_SMA", {"period": 5}, "trade_value", trade_value)
    manual = trade_value.rolling(5).mean().iloc[-1]
    assert abs(values[-1] - manual) < 1e-6


def _run_vpvr_probe(
    highs: list[float], lows: list[float], volumes: list[float], period: int, num_bins: int
) -> list[tuple[float, float, float]]:
    """명시적 high/low/volume 시퀀스로 VPVR bin 분배를 손으로 검증할 수 있게 만드는 전용 하네스.
    NUM_BINS을 테스트 전용 값으로 좁혀 손 계산이 가능하게 하고, 매 봉의 (poc, vah, val) 이력을
    리스트로 반환한다."""
    n = len(highs)
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx,
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": volumes,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _VpvrProbeStrategy(bt.Strategy):
        def __init__(self):
            self.probe = price_levels.VolumeProfile(self.data, period=period)
            self.seen_values: list[tuple[float, float, float]] = []

        def next(self):
            self.seen_values.append((
                float(self.probe.lines.poc[0]),
                float(self.probe.lines.vah[0]),
                float(self.probe.lines.val[0]),
            ))

    original_num_bins = price_levels.NUM_BINS
    price_levels.NUM_BINS = num_bins
    try:
        cerebro = bt.Cerebro()
        cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
        cerebro.addstrategy(_VpvrProbeStrategy)
        results = cerebro.run()
        return results[0].seen_values
    finally:
        price_levels.NUM_BINS = original_num_bins


def test_vpvr_produces_nan_during_warmup_before_enough_bars():
    # period=3인데 봉이 2개뿐이라 롤링 윈도우가 아직 안 찼음 — 세 라인 모두 NaN이어야 함.
    highs = [10, 10]
    lows = [0, 0]
    volumes = [100, 100]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    assert all(v != v for poc, vah, val in values for v in (poc, vah, val)), (
        "워밍업 중(봉이 period개 미만)엔 poc/vah/val 전부 NaN이어야 함(v != v는 NaN 체크)"
    )


def test_vpvr_matches_hand_traced_bin_distribution():
    # period=3, num_bins=4로 손 계산 가능한 3봉 시퀀스.
    # window_high=10.0(2번째 봉), window_low=0.0(1번째 봉) → bin_width=2.5.
    # bin0=[0,2.5], bin1=[2.5,5.0], bin2=[5.0,7.5], bin3=[7.5,10.0].
    # 1번째 봉(h=2.5,l=0,v=100)은 bin0에만 전량 겹침 → bin0 += 100.
    # 2번째 봉(h=10,l=7.5,v=10)은 bin3에만 전량 겹침 → bin3 += 10.
    # 3번째 봉(h=5,l=2.5,v=5)은 bin1에만 전량 겹침 → bin1 += 5.
    # 최종 bin_volumes = [100, 5, 0, 10], 합계 115.
    # POC = bin0(최댓값) 중간값 = 0 + 0.5*2.5 = 1.25.
    # Value Area: bin0 하나만으로 100/115 ≈ 87% > 70% 목표 도달 → 확장 없음.
    # VAH = bin0 윗값 = 2.5, VAL = bin0 아랫값 = 0.0.
    highs = [2.5, 10.0, 5.0]
    lows = [0.0, 7.5, 2.5]
    volumes = [100, 10, 5]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    poc, vah, val = values[-1]
    assert poc == pytest.approx(1.25)
    assert vah == pytest.approx(2.5)
    assert val == pytest.approx(0.0)


def test_vpvr_handles_completely_flat_window_without_dividing_by_zero():
    # 윈도우 안 모든 봉이 h==l==100(무변동) — window_high==window_low라 bin 분할이 무의미.
    # ZeroDivisionError 없이 poc=vah=val=100.0으로 처리되어야 함.
    highs = [100.0, 100.0, 100.0]
    lows = [100.0, 100.0, 100.0]
    volumes = [10, 10, 10]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    poc, vah, val = values[-1]
    assert poc == pytest.approx(100.0)
    assert vah == pytest.approx(100.0)
    assert val == pytest.approx(100.0)


def test_vpvr_handles_doji_bar_within_a_non_flat_window():
    # 윈도우 전체는 평평하지 않지만(window_high=10 != window_low=0), 그 안의 한 봉(2번째)만
    # h==l==5(도지)인 경우 — 그 봉 처리에서 크래시(ZeroDivisionError) 없이 값이 나와야 함.
    # 정확한 값 대신 불변식(VAL <= POC <= VAH, 윈도우 범위 안)만 검증한다.
    highs = [10, 5, 10]
    lows = [0, 5, 0]
    volumes = [50, 30, 50]
    values = _run_vpvr_probe(highs, lows, volumes, period=3, num_bins=4)
    poc, vah, val = values[-1]
    assert 0.0 <= val <= poc <= vah <= 10.0


def test_vpvr_default_settings_keep_value_area_ordering_and_stays_within_window_range():
    # 기본 설정(NUM_BINS=24, period=50)의 실제 오실레이팅 데이터로, 워밍업 이후 모든 봉에서
    # VAL <= POC <= VAH이고 셋 다 그 시점 롤링 윈도우([window_low, window_high]) 범위 안에
    # 있는지 스모크 검증한다(정확한 값이 아니라 불변식 검증).
    poc_values = _run_probe("VPVR_POC", {})
    vah_values = _run_probe("VPVR_VAH", {})
    val_values = _run_probe("VPVR_VAL", {})
    df = make_oscillating_df()
    period = 50
    checked_any = False
    for i in range(len(poc_values)):
        poc, vah, val = poc_values[i], vah_values[i], val_values[i]
        if poc != poc:  # 워밍업 구간(NaN)은 건너뜀
            continue
        checked_any = True
        window_start = max(0, i - period + 1)
        window_high = df["high"].iloc[window_start:i + 1].max()
        window_low = df["low"].iloc[window_start:i + 1].min()
        assert val <= poc <= vah, f"bar {i}: VAL<=POC<=VAH 불변식 깨짐 ({val}, {poc}, {vah})"
        assert window_low - 1e-6 <= val, f"bar {i}: VAL이 윈도우 최저가보다 낮음"
        assert vah <= window_high + 1e-6, f"bar {i}: VAH가 윈도우 최고가보다 높음"
    assert checked_any, "워밍업 이후 값이 하나도 없음"


def test_fib_382_matches_manual_swing_calculation():
    values = _run_probe("FIB_382", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    manual = hh - (hh - ll) * 0.382
    assert abs(values[-1] - manual) < 1e-6


def test_fib_618_matches_manual_swing_calculation():
    values = _run_probe("FIB_618", {"period": 5})
    df = make_oscillating_df()
    hh = df["high"].rolling(5).max().iloc[-1]
    ll = df["low"].rolling(5).min().iloc[-1]
    manual = hh - (hh - ll) * 0.618
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_p_matches_manual_prev_bar_average():
    values = _run_probe("PIVOT_P", {})
    df = make_oscillating_df()
    manual = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_r1_matches_manual_formula():
    values = _run_probe("PIVOT_R1", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    manual = pivot * 2 - df["low"].iloc[-2]
    assert abs(values[-1] - manual) < 1e-6


def test_pivot_s1_matches_manual_formula():
    values = _run_probe("PIVOT_S1", {})
    df = make_oscillating_df()
    pivot = (df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 3.0
    manual = pivot * 2 - df["high"].iloc[-2]
    assert abs(values[-1] - manual) < 1e-6


def test_btc_correlation_matches_manual_pearson_of_pct_returns():
    df = make_oscillating_df()
    btc_df = make_oscillating_df(base=50000.0, amplitude=3000.0, period=45, ripple_period=9)
    values = _run_probe_with_aux("BTC_CORRELATION", {"period": 10}, "btc_close", btc_df["close"])

    coin_roc = df["close"].pct_change() * 100
    btc_roc = btc_df["close"].pct_change() * 100
    manual = statistics.correlation(coin_roc.iloc[-10:].tolist(), btc_roc.iloc[-10:].tolist())
    assert abs(values[-1] - manual) < 1e-6


def test_usdt_correlation_matches_manual_pearson_of_pct_returns():
    df = make_oscillating_df()
    usdt_df = make_oscillating_df(base=1300.0, amplitude=40.0, period=30, ripple_period=4)
    values = _run_probe_with_aux("USDT_CORRELATION", {"period": 10}, "usdt_close", usdt_df["close"])

    coin_roc = df["close"].pct_change() * 100
    usdt_roc = usdt_df["close"].pct_change() * 100
    manual = statistics.correlation(coin_roc.iloc[-10:].tolist(), usdt_roc.iloc[-10:].tolist())
    assert abs(values[-1] - manual) < 1e-6


def test_usdt_correlation_returns_zero_when_aux_series_is_constant():
    # KRW-USDT 등 페그/스테이블코인 마켓이 완전히 flat(무변동)한 구간에서는
    # statistics.correlation()이 StatisticsError("at least one of the inputs
    # is constant")를 던진다. 크래시 대신 "상관 신호 없음"으로 0.0을 반환해야 한다.
    usdt_df = make_oscillating_df(base=1300.0, amplitude=40.0, period=30, ripple_period=4)
    usdt_df["close"] = 1300.0
    values = _run_probe_with_aux("USDT_CORRELATION", {"period": 10}, "usdt_close", usdt_df["close"])
    assert values[-1] == 0.0


def test_obv_matches_manual_cumulative_volume_by_close_direction():
    values = _run_probe("OBV", {})
    df = make_oscillating_df()
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()
    manual = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            manual.append(manual[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            manual.append(manual[-1] - volumes[i])
        else:
            manual.append(manual[-1])
    assert values[-1] == manual[-1]


def test_fear_greed_cmc_matches_raw_fear_greed_value_column():
    df = make_oscillating_df()
    fear_greed = pd.Series([30.0 + (i % 50) for i in range(len(df))])
    values = _run_probe_with_aux("FEAR_GREED_CMC", {}, "fear_greed_value", fear_greed)
    assert abs(values[-1] - fear_greed.iloc[-1]) < 1e-6


def test_korea_premium_matches_raw_korea_premium_value_column():
    df = make_oscillating_df()
    korea_premium = pd.Series([3.0 + (i % 5) * 0.1 for i in range(len(df))])
    values = _run_probe_with_aux("KOREA_PREMIUM", {}, "korea_premium_value", korea_premium)
    assert abs(values[-1] - korea_premium.iloc[-1]) < 1e-6


def _run_vpin_probe(volumes: list[float], closes: list[float], period: int) -> list[float]:
    """명시적인 volume/close 시퀀스로 VPIN 버킷 경계를 손으로 검증할 수 있게 만드는 전용 하네스.
    make_oscillating_df 기반 _run_probe/_run_probe_with_aux와 달리, 매 봉의 vpin 값 전체 이력을
    리스트로 반환한다(버킷이 몇 번째 봉에서 완성되는지까지 검증해야 하므로 마지막 값만으론 부족)."""
    n = len(volumes)
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({
        "candle_time": idx,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes,
    })
    df_bt = df.set_index("candle_time")
    df_bt.index = df_bt.index.tz_localize(None)

    class _VpinProbeStrategy(bt.Strategy):
        def __init__(self):
            self.probe = INDICATOR_FACTORY["VPIN"](self.data, period=period)
            self.seen_values: list[float] = []

        def next(self):
            self.seen_values.append(float(self.probe[0]))

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=df_bt, openinterest=-1))
    cerebro.addstrategy(_VpinProbeStrategy)
    results = cerebro.run()
    return results[0].seen_values


def test_vpin_produces_nan_during_warmup_before_enough_buckets():
    # period=2: 최근 2봉 평균 거래량이 버킷 목표치. 처음 두 완성 버킷(2번째·4번째 봉)까지는
    # 불균형 비율이 1개(또는 0개)뿐이라 아직 평균 낼 period(2)개가 안 모여 NaN이어야 한다.
    volumes = [10, 10, 2, 2]
    closes = [100, 100, 100, 100]
    values = _run_vpin_probe(volumes, closes, period=2)
    assert all(v != v for v in values), "버킷이 period개 미만일 때는 전부 NaN이어야 함(v != v는 NaN 체크)"


def test_vpin_matches_hand_traced_bucket_sequence():
    # 손으로 추적 가능한 8봉 시퀀스(period=2). 실제 알고리즘을 파이썬으로 직접 재현해 검증한
    # 기대값이며(스펙 문서의 알고리즘과 동일), 봉별 기대 결과:
    #   1~4봉: NaN(워밍업, 완성 버킷이 아직 1개뿐이거나 없음)
    #   5봉: 0.0 (두 번째 유효 불균형 비율까지 모여 평균 0.0)
    #   6봉: 0.0 (거래량 1 < 목표 1.5라 버킷 미완성 → 5봉 값을 그대로 이어붙임, forward-fill)
    #   7봉: 0.0 (버킷은 새로 완성되지만 가격 변화가 0이라 우연히 같은 값)
    #   8봉: 아래에서 statistics로 직접 계산한 기대값(가격이 5 상승하는 버킷)
    volumes = [10, 10, 2, 2, 2, 1, 1, 10]
    closes = [100, 100, 100, 100, 100, 100, 100, 105]
    values = _run_vpin_probe(volumes, closes, period=2)

    assert all(v != v for v in values[:4])
    assert values[4] == pytest.approx(0.0)
    assert values[5] == values[4], "버킷 미완성 봉(6번째)은 직전 값을 그대로 이어붙여야 함(forward-fill)"
    assert values[6] == pytest.approx(0.0)

    sigma = statistics.stdev([0.0, 5.0])
    z = 5.0 / sigma
    buy_ratio = statistics.NormalDist().cdf(z)
    imbalance_bucket_8 = abs(2 * buy_ratio - 1)
    expected_bar8 = imbalance_bucket_8 / 2  # 직전 불균형(0.0)과 평균
    assert values[7] == pytest.approx(expected_bar8)


def test_vpin_handles_zero_price_variance_without_crashing():
    # 가격이 한 번도 안 바뀌면 버킷 가격변화 표준편차가 0 — ZeroDivisionError 없이 z=0(매수/매도
    # 50:50, 불균형 0)으로 처리되어야 한다.
    volumes = [10] * 10
    closes = [100.0] * 10
    values = _run_vpin_probe(volumes, closes, period=2)
    non_nan = [v for v in values if v == v]
    assert non_nan, "버킷이 완성되는 구간이 있어야 함"
    assert all(v == pytest.approx(0.0) for v in non_nan)


def test_vpin_registered_in_factory_and_runs_on_default_oscillating_data():
    # 다른 지표들과 동일한 스모크 테스트 경로(_run_probe, make_oscillating_df)로도 크래시 없이
    # 값을 내야 한다 — VPIN은 aux 라인이 필요 없으므로 _NEEDS_EXTRA_LINE에 추가하지 않는다.
    values = _run_probe("VPIN", {})
    assert len(values) > 0
