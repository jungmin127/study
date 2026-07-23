"""
backend/main.py

EDA 대시보드용 FastAPI 앱. engine.cache의 SQLite 저장소를 직접 조회한다.
Run: uvicorn backend.main:app --reload --port 8000  (저장소 루트에서 실행)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.cache import (
    delete_backtest_run,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
    run_backtest_cached,
)
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import find_unknown_indicators, is_empty, max_required_period
from engine.metrics import calculate_metrics
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles, get_krw_markets, get_krw_markets_with_ticker

app = FastAPI(title="Upbit Strategy EDA API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDICATOR_CATALOG: list[dict] = [
    {
        "value": "SMA", "label": "SMA (단순 이동평균)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 N개 봉의 종가를 산술평균한 값으로, 가격의 큰 흐름을 부드럽게 보여줍니다.",
        "example": "period=20이면 최근 20개 종가의 평균을 매 봉마다 다시 계산합니다. 예: 최근 20봉 종가 합이 2,000,000이면 SMA=100,000.",
    },
    {
        "value": "EMA", "label": "EMA (지수 이동평균)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 가격에 더 큰 가중치를 주는 이동평균으로, SMA보다 가격 변화에 빠르게 반응합니다.",
        "example": "period=20이면 가중치 α=2/(20+1)≈0.095를 적용해 EMA_today = 종가×α + EMA_어제×(1-α)로 계산합니다.",
    },
    {
        "value": "WMA", "label": "WMA (가중 이동평균)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "최근 봉일수록 더 큰 선형 가중치를 주는 이동평균입니다.",
        "example": "period=3이면 (종가1×1 + 종가2×2 + 종가3×3) / (1+2+3)로 계산합니다(가장 최근 종가의 가중치가 가장 큼).",
    },
    {
        "value": "RSI", "label": "RSI (상대강도지수)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "일정 기간의 평균 상승폭과 평균 하락폭을 비교해 0~100 사이 값으로 과매수/과매도를 나타냅니다.",
        "example": "period=14 기준 RSI < 30이면 과매도(매수 검토), RSI > 70이면 과매수(매도 검토) 구간으로 흔히 해석합니다.",
    },
    {
        "value": "MACD_line", "label": "MACD Line", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "단기 EMA에서 장기 EMA를 뺀 값으로, 모멘텀의 방향과 세기를 나타냅니다.",
        "example": "fast=12, slow=26이면 MACD Line = EMA(12) − EMA(26). 0보다 크면 상승 모멘텀입니다.",
    },
    {
        "value": "MACD_signal", "label": "MACD Signal", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "MACD Line을 다시 지수이동평균한 값으로, MACD Line과의 교차로 매매 신호를 잡을 때 씁니다.",
        "example": "signal=9이면 MACD Line의 9기간 EMA. MACD Line이 Signal을 상향 돌파하면 흔히 매수 신호로 봅니다.",
    },
    {
        "value": "STOCH_K", "label": "스토캐스틱 %K", "category": "오실레이터",
        "params": [
            {"key": "k_period", "label": "K기간", "default": 14},
            {"key": "d_period", "label": "D기간", "default": 3},
        ],
        "description": "최근 기간의 최고가·최저가 대비 현재 종가의 위치를 0~100으로 나타냅니다.",
        "example": "k_period=14일 때 %K = (현재종가 − 14기간 최저가) / (14기간 최고가 − 14기간 최저가) × 100.",
    },
    {
        "value": "STOCH_D", "label": "스토캐스틱 %D", "category": "오실레이터",
        "params": [
            {"key": "k_period", "label": "K기간", "default": 14},
            {"key": "d_period", "label": "D기간", "default": 3},
        ],
        "description": "%K를 다시 이동평균한 값으로, %K보다 완만하게 움직여 노이즈를 줄입니다.",
        "example": "d_period=3이면 최근 3개 %K값의 단순평균이 %D입니다.",
    },
    {
        "value": "CCI", "label": "CCI (상품채널지수)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "가격이 통계적 평균에서 얼마나 벗어났는지를 나타내는 지표로, 극단값에서 평균 회귀를 노릴 때 씁니다.",
        "example": "일반적으로 CCI < -100이면 과매도, CCI > 100이면 과매수 구간으로 해석합니다.",
    },
    {
        "value": "WILLIAMS_R", "label": "Williams %R", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "스토캐스틱 %K와 유사하나 0~-100 범위로 표현되는 과매수/과매도 지표입니다.",
        "example": "%R = (기간 최고가 − 현재종가) / (기간 최고가 − 기간 최저가) × -100. -80 이하면 과매도로 흔히 해석합니다.",
    },
    {
        "value": "BB_upper", "label": "볼린저밴드 상단", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "이동평균에 표준편차의 2배를 더한 상단 밴드로, 돌파 시 강한 상승 모멘텀으로 해석하기도 합니다.",
        "example": "period=20이면 SMA(20) + 2×표준편차(20). 종가가 이 값을 상향 돌파하면 과열 신호로도, 추세 시작으로도 해석 가능합니다.",
    },
    {
        "value": "BB_lower", "label": "볼린저밴드 하단", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "이동평균에서 표준편차의 2배를 뺀 하단 밴드로, 이탈 시 과매도 반등을 노리는 전략에 흔히 쓰입니다.",
        "example": "period=20이면 SMA(20) − 2×표준편차(20). 종가가 이 아래로 내려가면 매수 후보 구간으로 봅니다.",
    },
    {
        "value": "BB_middle", "label": "볼린저밴드 중간선", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "볼린저밴드의 기준이 되는 단순 이동평균선입니다(상단/하단 밴드의 중심).",
        "example": "period=20이면 그냥 SMA(20)과 동일한 값입니다.",
    },
    {
        "value": "ATR", "label": "ATR (평균실질변동폭)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "일정 기간의 평균 변동폭(고가-저가 등)을 나타내며, 변동성 기반 돌파 조건의 임계값으로 흔히 씁니다.",
        "example": "period=14면 최근 14봉의 True Range 평균. 예: 전일 종가 + ATR×2 를 오늘 고가가 넘으면 변동성 돌파로 봅니다.",
    },
    {
        "value": "OBV", "label": "OBV (누적 거래량)", "category": "거래량",
        "params": [],
        "description": "종가가 오른 날은 거래량을 더하고 내린 날은 뺀 누적값으로, 가격과 거래량의 방향이 일치하는지 봅니다.",
        "example": "어제 OBV=1000이고 오늘 종가가 상승, 거래량 500이면 오늘 OBV=1500.",
    },
    {
        "value": "VOLUME_SMA", "label": "거래량 SMA", "category": "거래량",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 N개 봉의 거래량을 산술평균한 값으로, 현재 거래량이 평소보다 급등했는지 비교할 때 기준으로 씁니다.",
        "example": "period=20이면 최근 20봉 거래량의 평균. 현재 거래량이 이 값의 2배를 넘으면 거래량 급등으로 판단하는 식으로 활용합니다.",
    },
    {
        "value": "STOP_LOSS_PCT", "label": "손절라인 (%)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": "<=",
        "description": "캔들 지표가 아니라 보유 포지션의 진입가 대비 현재 수익률(%)입니다. 이 값이 임계값 이하로 내려가면 매도합니다.",
        "example": "임계값 -5를 넣으면, 진입가 대비 수익률이 -5% 이하가 되는 순간(예: 진입가 100,000원 → 현재가 95,000원 이하) 매도 조건이 참이 됩니다.",
    },
    {
        "value": "TAKE_PROFIT_PCT", "label": "익절라인 (%)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": ">=",
        "description": "캔들 지표가 아니라 보유 포지션의 진입가 대비 현재 수익률(%)입니다. 이 값이 임계값 이상으로 오르면 매도합니다.",
        "example": "임계값 10을 넣으면, 진입가 대비 수익률이 +10% 이상이 되는 순간(예: 진입가 100,000원 → 현재가 110,000원 이상) 매도 조건이 참이 됩니다.",
    },
]


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/eda/heatmap")
def get_heatmap() -> list[dict]:
    return list_latest_sweep_results()


@app.get("/api/v1/eda/ranking")
def get_ranking() -> list[dict]:
    return list_combined_ranking()


@app.get("/api/v1/eda/combos")
def get_combos() -> list[dict]:
    return list_distinct_combos()


@app.get("/api/v1/eda/signals")
def get_signals() -> list[str]:
    return sorted(SIGNAL_REGISTRY.keys())


@app.get("/api/v1/markets")
def get_markets() -> list[dict]:
    return get_krw_markets_with_ticker()


@app.get("/api/v1/indicators/catalog")
def get_indicator_catalog() -> list[dict]:
    return INDICATOR_CATALOG


@app.get("/api/v1/eda/history")
def get_history(
    signal_set_name: str = Query(...),
    market: str = Query(...),
    timeframe: str = Query(...),
    is_combined: bool = Query(...),
) -> list[dict]:
    return list_sweep_history(signal_set_name, market, timeframe, is_combined)


@app.get("/api/v1/backtests")
def get_backtest_runs() -> list[dict]:
    return list_backtest_runs()


@app.get("/api/v1/backtests/{run_id}")
def get_backtest_detail(run_id: str) -> dict:
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")

    start_dt = datetime.fromisoformat(result["start"])
    end_dt = datetime.fromisoformat(result["end"])
    df = get_candles(result["market"], result["timeframe"], start_dt, end_dt)

    metrics = calculate_metrics(
        equity_curve=result["equity_curve"],
        trades=result["trades"],
        initial_capital=result["initial_capital"],
        df=df,
        timeframe=result["timeframe"],
    )

    # candle_time은 tz-aware(UTC)인데 trades의 entryTime/exitTime은 backtrader가
    # tz를 벗겨낸 naive 문자열이다(engine/runner.py의 df_bt.index.tz_localize(None)).
    # 프론트에서 new Date(...)로 파싱할 때 tz 표기 유무가 섞이면 로컬 타임존만큼
    # 어긋나 보이므로, 여기서도 naive로 맞춰 캔들/거래 시각의 기준을 통일한다.
    df_chart = df.copy()
    if df_chart["candle_time"].dt.tz is not None:
        df_chart["candle_time"] = df_chart["candle_time"].dt.tz_localize(None)
    ohlcv = [
        {
            "time": row.candle_time.isoformat(),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for row in df_chart.itertuples()
    ]

    return {
        "market": result["market"],
        "timeframe": result["timeframe"],
        "start": result["start"],
        "end": result["end"],
        "final_value": result["final_value"],
        "metrics": metrics,
        "ohlcv": ohlcv,
        "trades": result["trades"],
    }


@app.delete("/api/v1/backtests/{run_id}")
def delete_backtest(run_id: str) -> dict:
    deleted = delete_backtest_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    return {"deleted": True}


ComparisonOperator = Literal[">", "<", ">=", "<=", "=="]


class ConditionBlockRequest(BaseModel):
    indicator: str
    params: dict[str, float] = {}
    operator: ComparisonOperator
    threshold: float


class ConditionGroupRequest(BaseModel):
    type: Literal["AND", "OR"]
    conditions: list[Union[ConditionBlockRequest, "ConditionGroupRequest"]]


ConditionGroupRequest.model_rebuild()


class RunBacktestRequest(BaseModel):
    market: str
    timeframe: str
    start: str
    end: str
    initial_capital: float
    buy_conditions: ConditionGroupRequest
    sell_conditions: ConditionGroupRequest
    title: str | None = None
    description: str | None = None


def _validate_backtest_request(req: RunBacktestRequest) -> list[str]:
    """구조적 검증 + 시장 데이터 기반 검증을 모두 수행해 오류 사유 목록을 반환.
    Task 7의 /validate 엔드포인트와 이 함수를 공유한다."""
    errors: list[str] = []

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()

    if is_empty(buy_dict):
        errors.append("매수 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")
    if is_empty(sell_dict):
        errors.append("매도 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")

    unknown = sorted(set(find_unknown_indicators(buy_dict)) | set(find_unknown_indicators(sell_dict)))
    if unknown:
        errors.append(f"지원하지 않는 지표입니다: {', '.join(unknown)}")

    if req.start >= req.end:
        errors.append("시작일은 종료일보다 빨라야 합니다.")

    if req.initial_capital <= 0:
        errors.append("운용자금은 0보다 커야 합니다.")

    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")

    return errors


@app.post("/api/v1/backtests/run")
def run_backtest_endpoint(req: RunBacktestRequest) -> dict:
    errors = _validate_backtest_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    try:
        df = get_candles(req.market, req.timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=400, detail="해당 기간에 캔들 데이터가 없습니다")

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()
    required_bars = max(max_required_period(buy_dict), max_required_period(sell_dict))
    if len(df) < required_bars:
        raise HTTPException(
            status_code=400,
            detail=(
                f"선택한 지표가 최소 {required_bars}개의 봉을 필요로 하지만, "
                f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리거나 지표 파라미터를 줄이세요."
            ),
        )

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": req.initial_capital}

    result = run_backtest_cached(
        df=df,
        strategy_cls=ConditionTreeStrategy,
        risk_config=risk_config,
        market=req.market,
        timeframe=req.timeframe,
        start=start_dt,
        end=end_dt,
        strategy_params={"buy_conditions": buy_dict, "sell_conditions": sell_dict},
        title=req.title,
        description=req.description,
    )
    return {"run_id": result["run_id"]}


@app.post("/api/v1/backtests/validate")
def validate_backtest_endpoint(req: RunBacktestRequest) -> dict:
    errors = _validate_backtest_request(req)
    if errors:
        return {"valid": False, "errors": errors}

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    try:
        df = get_candles(req.market, req.timeframe, start_dt, end_dt)
    except (ValueError, RuntimeError) as exc:
        return {"valid": False, "errors": [str(exc)]}

    if df.empty:
        return {"valid": False, "errors": ["해당 기간에 캔들 데이터가 없습니다."]}

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()
    required_bars = max(max_required_period(buy_dict), max_required_period(sell_dict))
    if len(df) < required_bars:
        return {
            "valid": False,
            "errors": [
                f"선택한 지표가 최소 {required_bars}개의 봉을 필요로 하지만, "
                f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리거나 지표 파라미터를 줄이세요."
            ],
        }

    return {"valid": True, "errors": []}
