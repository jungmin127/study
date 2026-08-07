"""
trading/signal_engine.py

live_indicators.py(서브플랜②③)와 condition_tree.eval_group_values()(서브플랜①)를 결합해
새 봉 마감마다 매수/매도 신호를 계산하고 signals에 기록한다. 캔들은 REST 폴링
(upbit_data_service.get_candles)으로 감지한다 — 업비트 공개 WS에는 캔들 채널이 없다
(서브플랜④에서 확인). 서킷브레이커 체크나 실제 주문 실행은 다루지 않는다(⑤-3/⑤-4의 몫) —
이 모듈은 신호의 True/False/판단불가만 계산·기록한다.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from upbit_data_service import get_candles, timeframe_duration

_AUX_MARKET_LINE_NAME: dict[str, str] = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}
_WARMUP_BUFFER_BARS = 5


def _fetch_candles_with_warmup(
    market: str, timeframe: str, required_bars: int, now: datetime,
) -> pd.DataFrame:
    """대상(또는 보조) 마켓의 캔들을 required_bars + 여유분(_WARMUP_BUFFER_BARS)만큼
    워밍업 포함해 조회한다."""
    duration = timeframe_duration(timeframe)
    start = now - (required_bars + _WARMUP_BUFFER_BARS) * duration
    return get_candles(market, timeframe, start, now)


def _merge_aux_markets(
    df: pd.DataFrame, aux_markets: set[str], market: str, timeframe: str,
    required_bars: int, now: datetime,
) -> pd.DataFrame:
    """MARKET_TREND/BTC_CORRELATION/USDT_CORRELATION이 필요로 하는 보조마켓 종가를
    btc_close/usdt_close 컬럼으로 병합한다. 백테스트(backend/main.py)와 동일하게
    ffill().bfill()로 갭을 채운다(설계 스펙 결정2 — 이건 특정 타임스탬프 하나가 비는
    정상적인 갭 처리이지 스펙 결정8의 '전체 데이터 소스 장애'와는 다른 문제)."""
    for aux_market in aux_markets:
        line_name = _AUX_MARKET_LINE_NAME[aux_market]
        if aux_market == market:
            df = df.assign(**{line_name: df["close"]})
            continue
        aux_df = _fetch_candles_with_warmup(aux_market, timeframe, required_bars, now)
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time", how="left",
        )
        df[line_name] = df[line_name].ffill().bfill()
    return df
