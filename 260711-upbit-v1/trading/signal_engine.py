"""
trading/signal_engine.py

live_indicators.py(서브플랜②③)와 condition_tree.eval_group_values()(서브플랜①)를 결합해
새 봉 마감마다 매수/매도 신호를 계산하고 signals에 기록한다. 캔들은 REST 폴링
(upbit_data_service.get_candles)으로 감지한다 — 업비트 공개 WS에는 캔들 채널이 없다
(서브플랜④에서 확인). 서킷브레이커 체크나 실제 주문 실행은 다루지 않는다(⑤-3/⑤-4의 몫) —
이 모듈은 신호의 True/False/판단불가만 계산·기록한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from engine.condition_tree import (
    POSITION_RELATIVE_INDICATORS,
    apply_operator,
    collect_blocks,
    eval_group_values,
    indicator_key,
    max_required_period,
    required_aux_markets,
)
from upbit_data_service import get_candles, timeframe_duration
from trading.live_indicators import (
    LIVE_INDICATOR_FACTORY,
    compute_korea_premium_value,
    fetch_live_binance_close,
    fetch_live_fear_greed_value,
    fetch_live_funding_rate_value,
)
from trading.position_manager import get_open_position
import trading.db as db
from trading.risk_manager import today_kst, is_circuit_tripped_today


def _to_utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

# 이 딕셔너리는 engine/condition_tree.py의 AUX_MARKET_INDICATORS(지표->마켓)와
# engine/runner.py의 AUX_MARKET_LINE_NAME(마켓->컬럼, backend/main.py가 사용)과 값이
# 같아야 하는 의도적 중복이다 — signal_engine.py는 engine.condition_tree 외의 engine/
# 서브모듈을 import할 수 없다는 Global Constraint 때문에 별도 사본을 둔다. 새 보조마켓을
# 추가할 때는 세 곳(여기, engine/condition_tree.py의 AUX_MARKET_INDICATORS,
# engine/runner.py의 AUX_MARKET_LINE_NAME) 모두 갱신할 것 — 하나만 고치면 라이브에서만
# 조용히 KeyError가 나서 데몬이 죽을 수 있다(tests/test_signal_engine.py의 drift 방지
# 테스트가 값 집합이 어긋나면 잡아낸다).
_AUX_MARKET_LINE_NAME: dict[str, str] = {"KRW-BTC": "btc_close", "KRW-USDT": "usdt_close"}

# _WARMUP_MULTIPLIER/_WARMUP_BUFFER_BARS: required_bars에 붙이는 워밍업 여유분. 단순
# +5 정도로는 부족한 지표가 있다 — CCI/VPIN은 이중 rolling/버킷 구조라 마지막 값이
# non-NaN이 되려면 period의 약 2~2.4배가 필요하고, MACD_signal/MACD_PPO_signal은
# create_macd_line을 거쳐 slow EMA + signal EMA 두 단계 워밍업이 누적된다(예:
# fast=12/slow=26/signal=9면 최소 34봉 필요, required_bars는 26). 부족하면 예외도 로그도
# 없이 해당 지표값이 NaN -> eval_group_values가 "판단불가"로 조용히 건너뛰어 실거래
# 조건이 무시된다. required_bars*3+30은 LIVE_INDICATOR_FACTORY 39개 지표를 대표
# 파라미터로 전수 검증해 나온 값이다(tests/test_signal_engine_warmup.py).
_WARMUP_MULTIPLIER = 3
_WARMUP_BUFFER_BARS = 30


def _fetch_candles_with_warmup(
    market: str, timeframe: str, required_bars: int, now: datetime,
) -> pd.DataFrame:
    """대상(또는 보조) 마켓의 캔들을 required_bars*_WARMUP_MULTIPLIER + _WARMUP_BUFFER_BARS
    만큼 워밍업 포함해 조회한다(왜 이 공식인지는 위 상수 주석 참고)."""
    duration = timeframe_duration(timeframe)
    start = now - (required_bars * _WARMUP_MULTIPLIER + _WARMUP_BUFFER_BARS) * duration
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


def _populate_b_group_columns(
    df: pd.DataFrame, market: str, timeframe: str, indicator_names: set[str], now: datetime,
) -> pd.DataFrame:
    """FEAR_GREED_CMC/FUNDING_RATE/KOREA_PREMIUM이 조건 트리에 있으면 fetch_live_*()로
    현재값을 조회해 df의 마지막 행에만 채운다(설계 스펙 결정1). 조회 실패/스테일이면 None이
    그대로 남아 컬럼이 NaN인 채로 유지되고, eval_group_values가 이를 unknown으로 처리한다
    (스펙 결정8) — 이 함수는 별도 방어코드를 두지 않는다."""
    last_idx = df.index[-1]

    if "FEAR_GREED_CMC" in indicator_names:
        df = df.assign(fear_greed_value=float("nan"))
        value = fetch_live_fear_greed_value(now=now)
        if value is not None:
            df.loc[last_idx, "fear_greed_value"] = value

    if "FUNDING_RATE" in indicator_names:
        df = df.assign(funding_rate_value=float("nan"))
        value = fetch_live_funding_rate_value(market, now=now)
        if value is not None:
            df.loc[last_idx, "funding_rate_value"] = value

    if "KOREA_PREMIUM" in indicator_names:
        df = df.assign(korea_premium_value=float("nan"))
        binance_close = fetch_live_binance_close(market, timeframe, now=now)
        if binance_close is not None and "usdt_close" in df.columns:
            df.loc[last_idx, "binance_close"] = binance_close
            df.loc[last_idx, "korea_premium_value"] = compute_korea_premium_value(
                df.loc[[last_idx]]
            ).iloc[0]

    return df


def _compute_indicator_values(df: pd.DataFrame, blocks: list[dict]) -> dict[str, float]:
    """조건 트리의 모든 ConditionBlock에 대해 지표값을 계산한다(설계 스펙 결정1 —
    A그룹/B그룹 구분 없이 동일하게 LIVE_INDICATOR_FACTORY를 호출, df 준비 방식만 다름)."""
    values: dict[str, float] = {}
    for block in blocks:
        name = block["indicator"]
        params = block.get("params", {})
        if name not in LIVE_INDICATOR_FACTORY:
            raise ValueError(f"알 수 없는 지표: {name}")
        key = indicator_key(name, params)
        if key in values:
            continue
        series = LIVE_INDICATOR_FACTORY[name](df, **params)
        values[key] = series.iloc[-1]
    return values


def _position_context(
    live_strategy_id: str, latest_close: float, latest_candle_time, timeframe: str,
) -> tuple[float | None, int | None]:
    """오픈 포지션이 있으면 (수익률%, 보유 봉 수)를, 없으면 (None, None)을 반환한다
    (STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS 평가용, 설계 스펙 결정7)."""
    position = get_open_position(live_strategy_id)
    if position is None:
        return None, None

    entry_price = position["entry_price"]
    position_return_pct = (latest_close - entry_price) / entry_price * 100

    entry_time = _to_utc_timestamp(position["entry_time"])
    candle_time = _to_utc_timestamp(latest_candle_time)
    elapsed = candle_time - entry_time
    position_holding_bars = max(int(elapsed / timeframe_duration(timeframe)), 0)

    return position_return_pct, position_holding_bars


def _no_new_candle_result() -> dict:
    return {
        "new_candle": False, "candle_time": None,
        "buy_signal": None, "sell_signal": None,
        "paused": False, "resumed": False,
    }


def evaluate_signals(live_strategy_id: str, now: datetime | None = None) -> dict:
    """새 봉 마감을 감지하면 지표 계산 + 조건평가를 수행해 signals에 기록하고,
    live_strategies.status를 필요시 갱신한다. 새 봉이 아니면 즉시 조기 반환한다(daemon이
    폴링 주기마다 안전하게 반복 호출할 수 있는 멱등적 인터페이스, 설계 스펙)."""
    now = now or datetime.now(timezone.utc)

    strategy = db.get_live_strategy(live_strategy_id)
    if strategy is None:
        raise ValueError(f"전략을 찾을 수 없습니다: {live_strategy_id}")

    # daemon은 지금 status='running'인 전략만 폴링하지만(암묵적 전제), 그 전제가 미래에
    # 깨지면(예: 수동 일시정지 UI 추가) 'stopped'/'pending' 같은 사용자가 명시적으로 멈췄거나
    # 아직 시작 안 한 상태의 전략도 여기로 들어올 수 있다. 그런 상태에서 판단불가가 나오면
    # 아래 로직이 이를 'paused'로 덮어써버려 사용자 의도(정지)를 조용히 뒤집는다(최종 리뷰
    # Minor #4) — 그래서 running/paused가 아니면 새 봉 계산·기록 없이 조기 반환한다.
    if strategy["status"] not in ("running", "paused"):
        return _no_new_candle_result()

    market = strategy["market"]
    timeframe = strategy["timeframe"]
    buy_conditions = json.loads(strategy["buy_conditions_json"])
    sell_conditions = json.loads(strategy["sell_conditions_json"])

    required_bars = max(max_required_period(buy_conditions), max_required_period(sell_conditions))

    df = _fetch_candles_with_warmup(market, timeframe, required_bars, now)
    if df.empty:
        return _no_new_candle_result()

    latest_candle_time = df["candle_time"].iloc[-1]
    last_processed = strategy["last_processed_candle_time"]
    if last_processed is not None and latest_candle_time <= pd.Timestamp(last_processed):
        return _no_new_candle_result()

    aux_markets = required_aux_markets(buy_conditions) | required_aux_markets(sell_conditions)
    if aux_markets:
        df = _merge_aux_markets(df, aux_markets, market, timeframe, required_bars, now)

    blocks = [
        b for b in collect_blocks(buy_conditions) + collect_blocks(sell_conditions)
        if b["indicator"] not in POSITION_RELATIVE_INDICATORS
    ]
    indicator_names = {b["indicator"] for b in blocks}
    b_group_names = indicator_names & {"FEAR_GREED_CMC", "FUNDING_RATE", "KOREA_PREMIUM"}
    if b_group_names:
        df = _populate_b_group_columns(df, market, timeframe, b_group_names, now)

    values = _compute_indicator_values(df, blocks)

    latest_close = df["close"].iloc[-1]
    position_return_pct, position_holding_bars = _position_context(
        live_strategy_id, latest_close, latest_candle_time, timeframe,
    )

    buy_result = eval_group_values(buy_conditions, values, position_return_pct, position_holding_bars)
    sell_result = eval_group_values(sell_conditions, values, position_return_pct, position_holding_bars)

    snapshot_json = json.dumps({k: (None if v != v else v) for k, v in values.items()})
    candle_time_str = latest_candle_time.isoformat()

    buy_signal_id = db.insert_signal(
        live_strategy_id, "buy", candle_time_str, snapshot_json,
        skip_reason="unknown" if buy_result is None else None,
    )
    sell_signal_id = db.insert_signal(
        live_strategy_id, "sell", candle_time_str, snapshot_json,
        skip_reason="unknown" if sell_result is None else None,
    )

    paused = False
    resumed = False
    if buy_result is None or sell_result is None:
        if strategy["status"] != "paused":
            db.update_live_strategy_status(live_strategy_id, "paused")
        paused = True
    elif strategy["status"] == "paused":
        if not is_circuit_tripped_today(live_strategy_id):
            db.update_live_strategy_status(live_strategy_id, "running")
            resumed = True
            # circuit_breaker_state.resumed_at을 채운다 — 재개가 실제로 일어나는 유일한
            # 지점이 여기다(risk_manager.py 어디에도 resumed_at을 쓰는 곳이 없었다, 최종
            # 리뷰 Important #3). upsert_circuit_breaker_state는 UPSERT라 다른 필드를
            # 실수로 지우지 않도록 기존 cb_state 값을 그대로 넘긴다(cb_state가 아예 없으면
            # 아직 서킷브레이커 이력이 없는 전략이므로 오늘 날짜의 안전한 기본값을 쓴다).
            cb_state = db.get_circuit_breaker_state(live_strategy_id)
            resumed_at = datetime.now(timezone.utc).isoformat()
            if cb_state is not None:
                db.upsert_circuit_breaker_state(
                    live_strategy_id, cb_state["trading_date"], cb_state["consecutive_losses"],
                    cb_state["tripped"], cb_state["tripped_reason"], cb_state["tripped_at"],
                    resumed_at,
                )
            else:
                db.upsert_circuit_breaker_state(
                    live_strategy_id, today_kst(), 0, 0, None, None, resumed_at,
                )

    db.update_live_strategy_last_candle(live_strategy_id, candle_time_str)

    return {
        "new_candle": True,
        "candle_time": candle_time_str,
        "buy_signal": buy_result,
        "sell_signal": sell_result,
        "buy_signal_id": buy_signal_id,
        "sell_signal_id": sell_signal_id,
        "latest_close": float(latest_close),
        "paused": paused,
        "resumed": resumed,
    }


_TICKER_RISK_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT"}


def has_risk_exit_conditions(sell_conditions: dict) -> bool:
    """sell_conditions_json에 STOP_LOSS_PCT/TAKE_PROFIT_PCT 블록이 하나라도 있는지
    확인한다(⑤-4c: 없는 전략은 daemon.py가 ticker WS 연결 자체를 안 열기 위한
    최적화용, 설계 스펙 결정7)."""
    return any(b["indicator"] in _TICKER_RISK_INDICATORS for b in collect_blocks(sell_conditions))


def matched_risk_exit_indicator(sell_conditions: dict, position_return_pct: float) -> str | None:
    """STOP_LOSS_PCT/TAKE_PROFIT_PCT를 sell_conditions_json 안의 다른 조건과의 AND/OR
    결합과 무관하게 독립 안전망으로 평가한다(⑤-4c 설계 스펙 결정1). 위반된 블록의
    indicator 이름(트리에서 먼저 발견된 것)을 반환, 없으면 None. daemon.py가 반환값을
    order_executor.exit_for_risk()의 close_reason 기록에 그대로 쓴다."""
    for block in collect_blocks(sell_conditions):
        if block["indicator"] in _TICKER_RISK_INDICATORS:
            if apply_operator(position_return_pct, block["operator"], float(block["threshold"])):
                return block["indicator"]
    return None
