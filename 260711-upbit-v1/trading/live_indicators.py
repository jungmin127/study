"""
trading/live_indicators.py

라이브 트레이딩용 지표 계산 — pandas 기반. engine/indicators/*.py(backtrader 기반,
백테스트 전용)와 값이 일치하도록 골든테스트로 검증한다(스펙 결정 1). INDICATOR_FACTORY
39개(A그룹 33개 + B그룹 6개) 전부를 다룬다(스펙 결정 2).

대부분의 create_* 함수는 순수 계산이다 — bt.feeds.PandasData 대신 필요한 컬럼(OHLCV,
일부는 trade_value/btc_close/usdt_close/fear_greed_value/korea_premium_value/
funding_rate_value)을 가진 pandas.DataFrame을 받아 같은 이름의 pandas.Series(워밍업
구간 NaN)를 반환하며 I/O를 하지 않는다. 예외는 fetch_live_*() 3개(FEAR_GREED_CMC/
FUNDING_RATE의 원시값과, KOREA_PREMIUM의 두 입력 중 binance_close를 실제로 조회) —
이 셋만 외부 API를 호출하고, 지연·실패 시 오래된 값을 forward-fill하지 않고 None을
반환한다(스펙 결정 8). 단 KOREA_PREMIUM은 compute_korea_premium_value()에 binance_close
외에 usdt_close(KRW-USDT aux 마켓 종가)도 필요한데, 이를 실시간으로 공급할 수단은 이
브랜치에 아직 없다(aux-market 웹소켓 피드는 향후 서브플랜에서 구축 예정) — 따라서
KOREA_PREMIUM은 원시값의 절반(binance_close)만 조회 가능할 뿐, 아직 엔드투엔드로
라이브 계산되지 않는다.
LIVE_INDICATOR_FACTORY 레지스트리는 engine.indicators.INDICATOR_FACTORY와 같은 패턴이며
항목 수도 동일하다(39개).
"""
from __future__ import annotations

import statistics
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from external_data_service import get_fear_greed_cmc
from binance_data_service import (
    BinanceSymbolNotFoundError,
    binance_symbol,
    get_binance_close,
    get_binance_funding_rate,
    timeframe_duration,
)


def create_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    return df["close"].rolling(period).mean()


def create_ema(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    return df["close"].ewm(span=period, adjust=False, min_periods=period).mean()


def create_wma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    weights = np.arange(1, period + 1)
    return df["close"].rolling(period).apply(
        lambda window: np.dot(window, weights) / weights.sum(), raw=True
    )


def create_rsi(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def create_macd_line(df: pd.DataFrame, **params) -> pd.Series:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    return ema_fast - ema_slow


def create_macd_signal(df: pd.DataFrame, **params) -> pd.Series:
    signal = int(params.get("signal", 9))
    macd_line = create_macd_line(df, **params)
    return macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()


def create_macd_ppo(df: pd.DataFrame, **params) -> pd.Series:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    return (ema_fast - ema_slow) / ema_slow * 100


def create_macd_ppo_signal(df: pd.DataFrame, **params) -> pd.Series:
    signal = int(params.get("signal", 9))
    ppo = create_macd_ppo(df, **params)
    return ppo.ewm(span=signal, adjust=False, min_periods=signal).mean()


def create_stoch_k(df: pd.DataFrame, **params) -> pd.Series:
    k_period = int(params.get("k_period", 14))
    d_period = int(params.get("d_period", 3))
    lowest_low = df["low"].rolling(k_period).min()
    highest_high = df["high"].rolling(k_period).max()
    fast_k = 100 * (df["close"] - lowest_low) / (highest_high - lowest_low)
    # backtrader의 Stochastic(StochasticFast가 아님)은 %K 자체가 이미 period_dfast로
    # 스무딩된 값을 노출한다 — fast_k를 그대로 쓰면 안 됨.
    return fast_k.rolling(d_period).mean()


def create_stoch_d(df: pd.DataFrame, **params) -> pd.Series:
    slow_k = create_stoch_k(df, **params)
    # period_dslow는 backtrader 기본값 3으로 고정(이 프로젝트의 STOCH 팩토리가
    # 파라미터화하지 않음, engine/indicators/momentum.py 참고).
    return slow_k.rolling(3).mean()


def create_cci(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    tp = (df["high"] + df["low"] + df["close"]) / 3
    tp_mean = tp.rolling(period).mean()
    # backtrader의 MeanDev는 각 시점의 |tp-tpmean|을 먼저 전체 시계열로 만든 뒤 그
    # 절대편차 시계열을 다시 이동평균한다 — 각 윈도우 내부에서 자기 평균을 새로 구해
    # 편차를 재는 것과 다르다(둘은 값이 다르다, 반드시 이 순서를 지킬 것).
    mean_dev = (tp - tp_mean).abs().rolling(period).mean()
    return (tp - tp_mean) / (0.015 * mean_dev)


def create_williams_r(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return -100 * (hh - df["close"]) / (hh - ll)


def create_momentum_pct(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 5))
    close = df["close"]
    return (close - close.shift(period)) / close.shift(period) * 100


def create_atr(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 14))
    prev_close = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def create_atr_pct(df: pd.DataFrame, **params) -> pd.Series:
    atr = create_atr(df, **params)
    return atr / df["close"] * 100


def create_bb_middle(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["close"].rolling(period).mean()


def create_bb_upper(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    mid = create_bb_middle(df, **params)
    std = df["close"].rolling(period).std(ddof=0)
    return mid + 2 * std


def create_bb_lower(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    mid = create_bb_middle(df, **params)
    std = df["close"].rolling(period).std(ddof=0)
    return mid - 2 * std


def create_bb_percent_b(df: pd.DataFrame, **params) -> pd.Series:
    upper = create_bb_upper(df, **params)
    lower = create_bb_lower(df, **params)
    return (df["close"] - lower) / (upper - lower)


def create_obv(df: pd.DataFrame, **params) -> pd.Series:
    direction = np.sign(df["close"].diff())
    return (direction * df["volume"]).fillna(0).cumsum()


def create_volume_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["volume"].rolling(period).mean()


def create_trade_value(df: pd.DataFrame, **params) -> pd.Series:
    return df["trade_value"]


def create_trade_value_sma(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return df["trade_value"].rolling(period).mean()


def create_vpin(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    recent_volumes: deque = deque(maxlen=period)
    bucket_cum_volume = 0.0
    last_bucket_close: float | None = None
    bucket_deltas: deque = deque(maxlen=period)
    bucket_imbalance_ratios: deque = deque(maxlen=period)

    out: list[float] = []
    prev_vpin = float("nan")
    for close, volume in zip(closes, volumes):
        recent_volumes.append(volume)
        bucket_cum_volume += volume
        target = statistics.mean(recent_volumes) if len(recent_volumes) == period else None
        if target is not None and bucket_cum_volume >= target:
            bucket_close = close
            bucket_volume = bucket_cum_volume
            if last_bucket_close is not None:
                delta = bucket_close - last_bucket_close
                bucket_deltas.append(delta)
                sigma = statistics.stdev(bucket_deltas) if len(bucket_deltas) >= 2 else 0.0
                z = delta / sigma if sigma > 0 else 0.0
                buy_ratio = statistics.NormalDist().cdf(z)
                buy_volume = bucket_volume * buy_ratio
                sell_volume = bucket_volume - buy_volume
                imbalance_ratio = abs(buy_volume - sell_volume) / bucket_volume if bucket_volume else 0.0
                bucket_imbalance_ratios.append(imbalance_ratio)
            last_bucket_close = bucket_close
            bucket_cum_volume = 0.0
        val = statistics.mean(bucket_imbalance_ratios) if len(bucket_imbalance_ratios) == period else prev_vpin
        out.append(val)
        prev_vpin = val
    return pd.Series(out, index=df.index)


def create_fib_382(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return hh - (hh - ll) * 0.382


def create_fib_500(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return hh - (hh - ll) * 0.5


def create_fib_618(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return hh - (hh - ll) * 0.618


def create_fib_382_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_382(df, **params)
    return (df["close"] - level) / level * 100


def create_fib_500_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_500(df, **params)
    return (df["close"] - level) / level * 100


def create_fib_618_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_fib_618(df, **params)
    return (df["close"] - level) / level * 100


def create_pivot_p(df: pd.DataFrame, **params) -> pd.Series:
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)
    return (prev_high + prev_low + prev_close) / 3.0


def create_pivot_r1(df: pd.DataFrame, **params) -> pd.Series:
    pivot = create_pivot_p(df, **params)
    prev_low = df["low"].shift(1)
    return pivot * 2 - prev_low


def create_pivot_s1(df: pd.DataFrame, **params) -> pd.Series:
    pivot = create_pivot_p(df, **params)
    prev_high = df["high"].shift(1)
    return pivot * 2 - prev_high


def create_pivot_p_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_p(df, **params)
    return (df["close"] - level) / level * 100


def create_pivot_r1_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_r1(df, **params)
    return (df["close"] - level) / level * 100


def create_pivot_s1_pct(df: pd.DataFrame, **params) -> pd.Series:
    level = create_pivot_s1(df, **params)
    return (df["close"] - level) / level * 100


NUM_BINS = 24
VALUE_AREA_PCT = 0.7


def _volume_profile(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """VPVR_POC/VAH/VAL 3개가 공유하는 계산. engine/indicators/price_levels.py의
    VolumeProfile(bt.Indicator)과 같은 알고리즘을 순수 파이썬 루프로 옮긴 것이다.
    backtrader 쪽도 POC/VAH/VAL 요청마다 VolumeProfile 인스턴스를 따로 만들어 3번
    재계산하므로(engine/indicators/price_levels.py의 create_vpvr_* 참고), 여기서도 매
    호출마다 재계산하는 게 backtrader와 일관된 동작이다."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    volumes = df["volume"].tolist()
    n = len(highs)
    poc_out: list[float] = [float("nan")] * n
    vah_out: list[float] = [float("nan")] * n
    val_out: list[float] = [float("nan")] * n

    hi_win: deque = deque(maxlen=period)
    lo_win: deque = deque(maxlen=period)
    vol_win: deque = deque(maxlen=period)

    for i in range(n):
        hi_win.append(highs[i])
        lo_win.append(lows[i])
        vol_win.append(volumes[i])
        if len(hi_win) < period:
            continue

        window_high = max(hi_win)
        window_low = min(lo_win)
        if window_high == window_low:
            poc_out[i] = vah_out[i] = val_out[i] = window_high
            continue

        bin_width = (window_high - window_low) / NUM_BINS
        bin_volumes = [0.0] * NUM_BINS
        for h, l, v in zip(hi_win, lo_win, vol_win):
            if h == l:
                idx = min(int((h - window_low) / bin_width), NUM_BINS - 1)
                bin_volumes[idx] += v
                continue
            for b in range(NUM_BINS):
                bin_bottom = window_low + b * bin_width
                bin_top = bin_bottom + bin_width
                overlap = min(h, bin_top) - max(l, bin_bottom)
                if overlap > 0:
                    bin_volumes[b] += v * (overlap / (h - l))

        total_volume = sum(bin_volumes)
        poc_idx = max(range(NUM_BINS), key=lambda k: bin_volumes[k])
        poc_price = window_low + (poc_idx + 0.5) * bin_width

        lo, hi = poc_idx, poc_idx
        accumulated = bin_volumes[poc_idx]
        target = total_volume * VALUE_AREA_PCT
        while accumulated < target and (lo > 0 or hi < NUM_BINS - 1):
            expand_lo = bin_volumes[lo - 1] if lo > 0 else -1.0
            expand_hi = bin_volumes[hi + 1] if hi < NUM_BINS - 1 else -1.0
            if expand_hi >= expand_lo:
                hi += 1
                accumulated += expand_hi
            else:
                lo -= 1
                accumulated += expand_lo

        poc_out[i] = poc_price
        vah_out[i] = window_low + (hi + 1) * bin_width
        val_out[i] = window_low + lo * bin_width

    idx = df.index
    return pd.Series(poc_out, index=idx), pd.Series(vah_out, index=idx), pd.Series(val_out, index=idx)


def create_vpvr_poc(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 50))
    return _volume_profile(df, period)[0]


def create_vpvr_vah(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 50))
    return _volume_profile(df, period)[1]


def create_vpvr_val(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 50))
    return _volume_profile(df, period)[2]


def create_market_trend(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 10))
    btc_close = df["btc_close"]
    return btc_close - btc_close.rolling(period).mean()


def _rolling_pearson_corr(a: pd.Series, b: pd.Series, period: int) -> pd.Series:
    """두 종가 시리즈의 봉 대비 등락률(ROC100, period=1)을 최근 period봉 모아 피어슨
    상관계수를 구한다. pandas rolling corr는 윈도우 내 분산이 0이면 NaN을 내는데,
    engine/indicators/market.py의 RollingCorrelation은 이 경우 0.0을 반환하므로(페그·
    스테이블코인 마켓 대응) 여기서도 같은 값으로 보정한다."""
    roc_a = a.pct_change(fill_method=None) * 100
    roc_b = b.pct_change(fill_method=None) * 100
    corr = roc_a.rolling(period).corr(roc_b)
    std_a = roc_a.rolling(period).std()
    std_b = roc_b.rolling(period).std()
    is_flat = (std_a == 0) | (std_b == 0)
    return corr.where(~is_flat, 0.0)


def create_btc_correlation(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return _rolling_pearson_corr(df["close"], df["btc_close"], period)


def create_usdt_correlation(df: pd.DataFrame, **params) -> pd.Series:
    period = int(params.get("period", 20))
    return _rolling_pearson_corr(df["close"], df["usdt_close"], period)


def create_fear_greed_cmc(df: pd.DataFrame, **params) -> pd.Series:
    return df["fear_greed_value"]


def create_korea_premium(df: pd.DataFrame, **params) -> pd.Series:
    return df["korea_premium_value"]


def create_funding_rate(df: pd.DataFrame, **params) -> pd.Series:
    return df["funding_rate_value"]


def compute_korea_premium_value(df: pd.DataFrame) -> pd.Series:
    """한국프리미엄 = (대상마켓 종가 / (바이낸스 현물종가 x USDT/KRW 환율) - 1) x 100.
    backend/main.py의 백테스트 캔들 병합 로직(korea_premium_value 컬럼 생성, 결정 8과
    무관하게 이미 존재하던 공식)과 동일하다. df["binance_close"]/df["usdt_close"] 중
    하나라도 NaN이면 결과도 자연히 NaN이 되어 eval_group_values()가 unknown으로
    처리한다(스펙 결정 8) — 별도 방어코드가 필요 없다."""
    return (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100


FEAR_GREED_STALE_AFTER = timedelta(days=2)


def fetch_live_fear_greed_value(now: datetime | None = None) -> float | None:
    """alternative.me 공포탐욕지수의 가장 최근 값을 조회한다. API 호출이 실패하거나
    가장 최근 값의 날짜가 FEAR_GREED_STALE_AFTER(2일)보다 오래됐으면(정상적인 하루
    발행 지연을 넘어 파이프라인이 며칠째 멈춘 경우) 오래된 값을 forward-fill하지 않고
    None을 반환한다(스펙 결정 8)."""
    now = now or datetime.now(timezone.utc)
    try:
        df = get_fear_greed_cmc(now - timedelta(days=7), now)
    except RuntimeError:
        return None
    if df.empty:
        return None
    latest = df.iloc[-1]
    if now - latest["date"] > FEAR_GREED_STALE_AFTER:
        return None
    return float(latest["fear_greed_value"])


FUNDING_RATE_STALE_AFTER = timedelta(hours=16)


def fetch_live_funding_rate_value(market: str, now: datetime | None = None) -> float | None:
    """바이낸스 무기한 선물 펀딩비의 가장 최근 값(퍼센트 단위)을 조회한다. API 호출이
    실패하거나 가장 최근 값의 funding_time이 FUNDING_RATE_STALE_AFTER(16시간,
    merge_funding_rate()가 백테스트 병합에 쓰는 tolerance와 동일한 근거)보다 오래됐으면
    None을 반환한다(스펙 결정 8)."""
    now = now or datetime.now(timezone.utc)
    symbol = binance_symbol(market)
    try:
        df = get_binance_funding_rate(symbol, now - timedelta(hours=24), now)
    except RuntimeError:
        return None
    if df.empty:
        return None
    latest = df.iloc[-1]
    if now - latest["funding_time"] > FUNDING_RATE_STALE_AFTER:
        return None
    return float(latest["funding_rate"])


BINANCE_CLOSE_STALE_MULTIPLIER = 2


def fetch_live_binance_close(market: str, timeframe: str, now: datetime | None = None) -> float | None:
    """KOREA_PREMIUM 계산(compute_korea_premium_value())에 필요한, 대상 코인의 바이낸스
    현물 종가 최신값. 심볼이 바이낸스에 없거나(BinanceSymbolNotFoundError) API 호출이
    실패하거나, 가장 최근 봉이 timeframe 길이의 BINANCE_CLOSE_STALE_MULTIPLIER(2)배보다
    오래됐으면 None을 반환한다(스펙 결정 8). usdt_close(KRW-USDT aux 마켓 종가)와 결합해
    korea_premium_value를 만드는 건 compute_korea_premium_value()의 몫 — 이 함수는 원시
    종가 조회 + 결측 판정만 한다."""
    now = now or datetime.now(timezone.utc)
    symbol = binance_symbol(market)
    duration = timeframe_duration(timeframe)
    try:
        df = get_binance_close(symbol, timeframe, now - 5 * duration, now)
    except (RuntimeError, BinanceSymbolNotFoundError):
        return None
    if df.empty:
        return None
    latest = df.iloc[-1]
    if now - latest["candle_time"] > BINANCE_CLOSE_STALE_MULTIPLIER * duration:
        return None
    return float(latest["close"])


LIVE_INDICATOR_FACTORY: dict[str, object] = {
    "SMA": create_sma,
    "EMA": create_ema,
    "WMA": create_wma,
    "RSI": create_rsi,
    "MACD_line": create_macd_line,
    "MACD_signal": create_macd_signal,
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
    "STOCH_K": create_stoch_k,
    "STOCH_D": create_stoch_d,
    "CCI": create_cci,
    "WILLIAMS_R": create_williams_r,
    "MOMENTUM_PCT": create_momentum_pct,
    "ATR": create_atr,
    "ATR_PCT": create_atr_pct,
    "BB_upper": create_bb_upper,
    "BB_lower": create_bb_lower,
    "BB_middle": create_bb_middle,
    "BB_PERCENT_B": create_bb_percent_b,
    "OBV": create_obv,
    "VOLUME_SMA": create_volume_sma,
    "TRADE_VALUE": create_trade_value,
    "TRADE_VALUE_SMA": create_trade_value_sma,
    "VPIN": create_vpin,
    "FIB_382": create_fib_382,
    "FIB_500": create_fib_500,
    "FIB_618": create_fib_618,
    "FIB_382_PCT": create_fib_382_pct,
    "FIB_500_PCT": create_fib_500_pct,
    "FIB_618_PCT": create_fib_618_pct,
    "PIVOT_P": create_pivot_p,
    "PIVOT_R1": create_pivot_r1,
    "PIVOT_S1": create_pivot_s1,
    "PIVOT_P_PCT": create_pivot_p_pct,
    "PIVOT_R1_PCT": create_pivot_r1_pct,
    "PIVOT_S1_PCT": create_pivot_s1_pct,
    "VPVR_POC": create_vpvr_poc,
    "VPVR_VAH": create_vpvr_vah,
    "VPVR_VAL": create_vpvr_val,
    "MARKET_TREND": create_market_trend,
    "BTC_CORRELATION": create_btc_correlation,
    "USDT_CORRELATION": create_usdt_correlation,
    "FEAR_GREED_CMC": create_fear_greed_cmc,
    "KOREA_PREMIUM": create_korea_premium,
    "FUNDING_RATE": create_funding_rate,
}

__all__ = [
    "LIVE_INDICATOR_FACTORY",
    "compute_korea_premium_value",
    "fetch_live_fear_greed_value",
    "fetch_live_funding_rate_value",
    "fetch_live_binance_close",
]
