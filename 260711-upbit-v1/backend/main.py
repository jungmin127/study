"""
backend/main.py

EDA 대시보드용 FastAPI 앱. engine.cache의 SQLite 저장소를 직접 조회한다.
Run: uvicorn backend.main:app --reload --port 8000  (저장소 루트에서 실행)
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone
from typing import Literal, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from engine.cache import (
    delete_backtest_run,
    delete_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    get_run_config,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_grid_search_jobs,
    list_latest_sweep_results,
    list_segment_classification,
    list_sweep_history,
    load_result,
    remove_grid_search_result,
    run_backtest_cached,
    save_result,
    update_backtest_run_metadata,
)
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import (
    collect_blocks,
    find_unknown_indicators,
    is_empty,
    max_required_period,
    required_aux_markets,
)
from engine.runner import AUX_MARKET_LINE_NAME, run_backtest
from binance_data_service import (
    BinanceSymbolNotFoundError,
    binance_symbol,
    get_binance_close,
    get_binance_funding_rate,
    merge_funding_rate,
)
from external_data_service import get_fear_greed_cmc, merge_fear_greed
from engine.live_valuation import has_revaluable_open_trade, revalue_open_trades
from engine.metrics import VALID_TIMEFRAMES, calculate_metrics, top_trade_contribution_pct
from engine.segment_analysis import run_segment_batch
from engine.trend_segments import EARLIEST_CANDLE_START, get_or_compute_trend_segments
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles, get_current_prices, get_krw_markets, get_krw_markets_with_ticker
from backend.grid_search_service import (
    JobAlreadyRunningError,
    JobNotActiveError,
    cancel_job,
    reset_active_job,
    start_job,
)
import httpx

import trading.db as trading_db
from backend.trading_analytics_service import get_journal_summary, get_market_journal
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client
from trading.upbit_client import UpbitCredentialsError, UpbitRateLimitError

def _to_utc_iso(value: str) -> str:
    """naive 문자열(오프셋 표기 없이 UTC 값만 담고 있는 경우가 대부분)에도 항상
    UTC 오프셋이 붙은 ISO 문자열을 반환한다. 프론트가 new Date(...)로 파싱할 때
    오프셋 없는 문자열은 브라우저 로컬 시간대로 잘못 해석되므로(예: KST 환경에서
    9시간 어긋남), API 경계에서 한 번에 명시적으로 맞춰준다."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


app = FastAPI(title="Upbit Strategy EDA API", version="0.1.0")


def _resolve_allowed_origin() -> str:
    """프론트엔드가 배포된 origin. 로컬 개발은 기본값(localhost:3000), 서버 배포
    (Oracle 등)는 ALLOWED_ORIGIN 환경변수로 Tailscale 주소를 지정한다(2026-08-14
    배포 스펙 결정4). 프로세스 시작 시 한 번 결정되면 충분하다 — systemd
    EnvironmentFile로 주입되는 값이라 실행 중 바뀌지 않는다. `.env`에 빈 값으로
    남아있는 경우(`ALLOWED_ORIGIN=`)도 python-dotenv가 os.environ에 빈 문자열로
    심으므로(미설정과 다름) `or`로 폴백해 빈 문자열도 미설정과 동일하게 취급한다
    (리뷰에서 실증된 버그 — 안 그러면 allow_origins=[""]로 CORS가 조용히 깨짐)."""
    return os.environ.get("ALLOWED_ORIGIN") or "http://localhost:3000"


# 라이브 전략 승인/일시정지/재개/중지 API가 실거래(자금 이동에 준하는 조작)와 붙어 있어,
# 와이드오픈 CORS("*")는 실제 계좌에 대한 위험이다(Fix 4, 최종 리뷰 Important).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_resolve_allowed_origin()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 모바일 LTE 데이터 절약(설계 결정: 500바이트 미만은 압축 이득보다 CPU 비용이 더 큼) —
# 백테스트/그리드서치/매매일지 목록 JSON 응답이 수십~수백 KB인 경우가 많아 효과가 크다.
app.add_middleware(GZipMiddleware, minimum_size=500)


def _run_segment_batch_safely() -> None:
    try:
        n = run_segment_batch()
        print(f"[segment_batch] 완료: {n}개 마켓 분류")
    except Exception as exc:
        print(f"[segment_batch] 실패: {exc}")


@app.on_event("startup")
def _start_segment_batch() -> None:
    """세그먼트(규모) 분류 배치를 백그라운드 스레드로 실행한다.

    코인마다 캔들 조회가 필요해 271개 KRW 마켓 기준 1~2분 걸린다(요청당 rate-limit
    딜레이 0.15초). 서버 기동을 이 시간만큼 막지 않기 위해 별도 스레드로 돌린다.
    _run_segment_batch_safely로 감싸 실패 시에도 서버 로그에 남도록 한다(성공/실패
    모두 조용히 사라지면 배치가 계속 도는 중인지 죽었는지 구분할 수 없다)."""
    threading.Thread(target=_run_segment_batch_safely, daemon=True).start()


def _fail_orphaned_grid_search_jobs() -> None:
    """이 프로세스가 죽으면 stdout 리더 스레드도 함께 사라진다 — 재기동 시 남아 있는
    running 행은 추적 불가능한 고아이므로 실패로 정리한다(스크립트 자체는 계속
    돌고 있을 수 있고, 결과는 "백테스트 결과" 탭의 [Grid] 항목에서 확인 가능)."""
    for job in list_grid_search_jobs():
        if job["status"] == "running":
            finish_grid_search_job(
                job["id"], status="failed",
                error_message=(
                    "백엔드가 재시작되어 진행률 추적이 끊겼습니다. "
                    "실제 결과는 '백테스트 결과' 탭의 [Grid] 항목을 확인하세요."
                ),
            )


@app.on_event("startup")
def _cleanup_orphaned_grid_search_jobs() -> None:
    _fail_orphaned_grid_search_jobs()

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
        "value": "BB_PERCENT_B", "label": "%B (볼린저밴드 정규화)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "종가가 볼린저밴드 내에서 어느 위치에 있는지를 0~1 사이 값으로 정규화합니다(하단=0, 상단=1). 코인 시세와 무관하게 항상 같은 범위입니다.",
        "example": "%B < 0.2면 하단 근접(과매도), %B > 0.8이면 상단 근접(과매수)으로 흔히 해석합니다.",
    },
    {
        "value": "MACD_PPO", "label": "PPO (MACD 정규화)", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "MACD Line을 장기 EMA 대비 비율(%)로 표현해 코인 가격과 무관하게 만든 지표입니다.",
        "example": "PPO = (EMA(12) − EMA(26)) / EMA(26) × 100. 0보다 크면 상승 모멘텀.",
    },
    {
        "value": "MACD_PPO_signal", "label": "PPO Signal", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "PPO를 다시 지수이동평균한 시그널 라인입니다.",
        "example": "PPO가 Signal을 상향 돌파하면 흔히 매수 신호로 봅니다.",
    },
    {
        "value": "ATR_PCT", "label": "ATR% (변동성 정규화)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "ATR을 현재가 대비 비율(%)로 표현해 코인마다 다른 가격 스케일을 제거한 지표입니다.",
        "example": "ATR% = ATR / 종가 × 100. 예: ATR%=2면 최근 변동폭이 종가의 2% 수준.",
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
        "value": "VPIN", "label": "VPIN (주문흐름 독성도)", "category": "거래량",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "거래량 버킷 단위로 매수/매도 주문 불균형을 추정한 값(0~1)입니다. 1에 가까울수록 그 구간 거래가 한쪽(매수 또는 매도)으로 강하게 쏠렸다는 뜻으로, 급등락 직전의 정보거래(독성 주문흐름) 징후로 해석합니다. 틱 데이터가 아니라 캔들 가격 변화로 매수/매도 비율을 확률적으로 추정하는 방식(Bulk Volume Classification)을 씁니다.",
        "example": "period=20, 연산자 >, 임계값 0.55면: 최근 20개 거래량 버킷 동안 주문흐름 불균형이 뚜렷한(변동성 폭발 전조로 흔히 해석되는) 구간을 포착합니다.",
    },
    {
        "value": "TRADE_VALUE", "label": "거래대금 (KRW)", "category": "거래대금",
        "params": [],
        "description": "해당 봉에서 실제로 오간 금액(가격×거래량, KRW)입니다. 거래량(수량)과 달리 가격이 반영돼 있어, 저가 잡코인이 수량만 많이 거래된 착시 없이 진짜 큰돈이 들어온 종목을 거를 때 씁니다.",
        "example": "임계값 5000000000(50억), 연산자 >=를 넣으면 해당 봉에서 거래대금이 50억 원 이상인 순간을 포착합니다.",
    },
    {
        "value": "TRADE_VALUE_SMA", "label": "거래대금 SMA", "category": "거래대금",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 N개 봉의 거래대금(KRW)을 산술평균한 값으로, 현재 거래대금이 평소보다 급증했는지 비교할 때 기준으로 씁니다.",
        "example": "period=20이면 최근 20봉 거래대금의 평균. 현재 거래대금이 이 값의 2배를 넘으면 진짜 자금이 유입된 급증 구간으로 판단하는 식으로 활용합니다.",
    },
    {
        "value": "FIB_382", "label": "피보나치 38.2%", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 고점과 저점 사이에서 38.2% 되돌림 지점을 계산합니다. 상승 추세 중 조정이 어디까지 진행될지 가늠하는 지지선으로 흔히 씁니다.",
        "example": "period=20이면 최근 20봉의 최고가·최저가 구간에서, 고점 대비 38.2% 되돌아온 가격입니다.",
    },
    {
        "value": "FIB_500", "label": "피보나치 50%", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 고점과 저점의 정중앙(50%) 되돌림 지점입니다. 엄밀히는 피보나치 비율이 아니지만 관례적으로 함께 봅니다.",
        "example": "period=20이면 최근 20봉 구간의 정확히 중간 가격입니다.",
    },
    {
        "value": "FIB_618", "label": "피보나치 61.8%", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "황금비율로 불리는 61.8% 되돌림 지점입니다. 조정이 깊게 들어와도 추세가 살아있는지 가늠하는 마지노선급 지지/저항으로 흔히 해석합니다.",
        "example": "period=20이면 최근 20봉 구간에서 고점 대비 61.8% 되돌아온 가격입니다.",
    },
    {
        "value": "FIB_382_PCT", "label": "피보나치 38.2% (정규화)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 고점·저점으로 계산한 피보나치 38.2% 되돌림 레벨 대비, 종가가 몇 % 위/아래에 있는지 나타냅니다. 코인 시세와 무관하게 항상 같은 범위입니다.",
        "example": "레벨이 95,000원, 종가가 100,000원이면 FIB_382_PCT ≈ +5.26입니다(레벨보다 5.26% 위).",
    },
    {
        "value": "FIB_500_PCT", "label": "피보나치 50% (정규화)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "최근 period봉의 스윙 구간 정중앙(50%) 되돌림 레벨 대비 종가의 이격을 %로 나타냅니다.",
        "example": "레벨 대비 종가가 3% 아래면 FIB_500_PCT = -3입니다.",
    },
    {
        "value": "FIB_618_PCT", "label": "피보나치 61.8% (정규화)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "황금비율 61.8% 되돌림 레벨 대비 종가의 이격을 %로 나타냅니다.",
        "example": "FIB_618_PCT > 0이면 종가가 61.8% 되돌림 레벨보다 위에 있다는 뜻입니다.",
    },
    {
        "value": "PIVOT_P", "label": "Pivot 기준선", "category": "가격대",
        "params": [],
        "description": "직전 1봉의 고가·저가·종가 평균으로 계산하는 기준선입니다. 오늘 가격이 이 선 위/아래 어디서 노는지로 매수/매도 심리 우위를 가늠하는 전통적 지표입니다.",
        "example": "직전 봉 고가 110, 저가 100, 종가 105면 Pivot = (110+100+105)/3 ≈ 105입니다.",
    },
    {
        "value": "PIVOT_R1", "label": "Pivot 저항선(R1)", "category": "가격대",
        "params": [],
        "description": "Pivot 기준선을 기준으로 계산하는 1차 저항선입니다. 종가가 이 선을 넘으면 상승 모멘텀이 강하다고 흔히 해석합니다.",
        "example": "Pivot이 105, 직전 봉 저가가 100이면 R1 = 105×2 − 100 = 110입니다.",
    },
    {
        "value": "PIVOT_S1", "label": "Pivot 지지선(S1)", "category": "가격대",
        "params": [],
        "description": "Pivot 기준선을 기준으로 계산하는 1차 지지선입니다. 종가가 이 선 아래로 내려가면 하락 압력이 강하다고 흔히 해석합니다.",
        "example": "Pivot이 105, 직전 봉 고가가 110이면 S1 = 105×2 − 110 = 100입니다.",
    },
    {
        "value": "PIVOT_P_PCT", "label": "Pivot 기준선 (정규화, %)", "category": "가격대",
        "params": [],
        "description": "Pivot 기준선(P) 대비 종가가 몇 % 위/아래에 있는지 나타냅니다.",
        "example": "PIVOT_P_PCT > 2면 기준선보다 2% 이상 위에서 거래되는 구간입니다.",
    },
    {
        "value": "PIVOT_R1_PCT", "label": "Pivot 저항선(R1) (정규화, %)", "category": "가격대",
        "params": [],
        "description": "1차 저항선(R1) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "PIVOT_R1_PCT > 0이면 저항선을 이미 돌파했다는 뜻입니다.",
    },
    {
        "value": "PIVOT_S1_PCT", "label": "Pivot 지지선(S1) (정규화, %)", "category": "가격대",
        "params": [],
        "description": "1차 지지선(S1) 대비 종가의 이격을 %로 나타냅니다.",
        "example": "PIVOT_S1_PCT < 0이면 지지선 아래로 이탈했다는 뜻입니다.",
    },
    {
        "value": "VPVR_POC", "label": "VPVR POC (거래량 최다 가격대)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "최근 period봉의 거래량을 가격대별로 나눠 쌓았을 때, 거래량이 가장 많이 몰린 가격대(Point of Control)입니다. 시장이 '공정하다'고 합의한 가격으로 해석되어 반등/저항이 자주 일어나는 자리로 흔히 씁니다.",
        "example": "period=50이면 최근 50봉의 가격대별 거래량 분포에서 가장 거래가 많았던 가격대입니다.",
    },
    {
        "value": "VPVR_VAH", "label": "VPVR Value Area 상단(VAH)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "최근 period봉 거래량의 70%가 몰려있는 구간(Value Area)의 상단 가격입니다. 이 위는 상대적으로 거래가 적었던 구간이라 가격이 빠르게 통과하는 경향이 있습니다.",
        "example": "period=50, 연산자 >, threshold를 이 값으로 두면 가격이 Value Area 위로 벗어난 구간을 포착합니다.",
    },
    {
        "value": "VPVR_VAL", "label": "VPVR Value Area 하단(VAL)", "category": "가격대",
        "params": [{"key": "period", "label": "기간", "default": 50}],
        "description": "최근 period봉 거래량의 70%가 몰려있는 구간(Value Area)의 하단 가격입니다. 이 아래는 상대적으로 거래가 적었던 구간이라 가격이 빠르게 통과하는 경향이 있습니다.",
        "example": "period=50, 연산자 <, threshold를 이 값으로 두면 가격이 Value Area 아래로 벗어난 구간을 포착합니다.",
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
    {
        "value": "HOLDING_PERIOD_BARS", "label": "보유기간 (봉)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": ">=",
        "description": "캔들 지표가 아니라 포지션을 진입한 이후 지난 봉의 개수입니다. 이 값이 임계값 이상이 되면 매도합니다(캘린더 일수가 아니라 봉 개수 기준).",
        "example": "임계값 20을 넣으면, 진입 후 20개 봉이 지나는 순간(15분봉이면 5시간, 일봉이면 20일) 매도 조건이 참이 됩니다.",
    },
    {
        "value": "MARKET_TREND", "label": "시장 추세 (BTC 종가-이동평균)", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 10}],
        "description": "대상 코인이 아니라 KRW-BTC 종가에서 KRW-BTC의 이동평균을 뺀 값입니다. 알트코인이 BTC 추세를 따라가는 경향을 이용해, 시장 전체가 약세일 때 매수를 쉬거나 매도하는 필터로 씁니다.",
        "example": "period=10이고 연산자 <, 임계값 0이면: KRW-BTC 종가가 자신의 10봉 이동평균보다 낮을 때(BTC가 하락 추세일 때) 조건이 참이 됩니다.",
    },
    {
        "value": "BTC_CORRELATION", "label": "BTC 상관계수", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "대상 코인과 KRW-BTC의 봉 대비 등락률(%)을 최근 period봉 동안 비교한 Pearson 상관계수(-1~1)입니다. 1에 가까울수록 BTC와 같은 방향으로, -1에 가까울수록 반대 방향으로 움직입니다.",
        "example": "period=20, 연산자 <, 임계값 0.3이면: 최근 20봉 동안 BTC와의 상관관계가 약해진(디커플링된) 상태를 포착합니다.",
    },
    {
        "value": "USDT_CORRELATION", "label": "테더 상관계수", "category": "시장 심리",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "대상 코인과 KRW-USDT(테더)의 봉 대비 등락률(%)을 최근 period봉 동안 비교한 Pearson 상관계수(-1~1)입니다.",
        "example": "period=20, 연산자 >, 임계값 0.5면: 최근 20봉 동안 원화 유동성(테더 시세)과 강하게 같이 움직이는 구간을 포착합니다.",
    },
    {
        "value": "FEAR_GREED_CMC", "label": "공포/탐욕 지수(CMC)", "category": "시장 심리",
        "params": [],
        "description": "alternative.me(CMC)가 산출하는 암호화폐 시장 전체의 공포/탐욕 지수(0~100)입니다. 코인과 무관하게 시장 전체에 적용되는 공통값이며, 2018-02-01 이전 구간은 데이터가 없어 그 이전 기간의 백테스트에는 이 지표를 쓸 수 없습니다.",
        "example": "연산자 <, 임계값 20이면: 시장 전체가 극단적 공포 상태(패닉 매도)인 구간을 포착해 역발상 매수 필터로 씁니다. 연산자 >, 임계값 80이면: 극단적 탐욕(과열) 상태를 포착해 매도 필터로 씁니다.",
    },
    {
        "value": "KOREA_PREMIUM", "label": "한국프리미엄", "category": "시장 심리",
        "params": [],
        "description": "대상 코인의 업비트(KRW) 시세가 바이낸스(USDT, 업비트 KRW-USDT 환율로 환산) 시세보다 몇 % 비싼지를 나타냅니다. 코인별로 계산되며, 해당 코인이 바이낸스에 상장돼 있지 않으면 이 지표를 쓸 수 없습니다.",
        "example": "연산자 <, 임계값 0이면: 역프리미엄(국내가가 더 싼) 구간을 포착합니다. 연산자 >, 임계값 5면: 프리미엄이 +5%를 넘는 과열 구간을 매도 필터로 씁니다.",
    },
    {
        "value": "FUNDING_RATE", "label": "펀딩비(바이낸스 선물)", "category": "시장 심리",
        "params": [],
        "description": "대상 코인의 바이낸스 무기한 선물 펀딩비를 퍼센트로 나타냅니다. 양수면 롱이 숏에게 수수료를 지불(롱 우세/과열), 음수면 그 반대(숏 우세)입니다.",
        "example": "펀딩비 > 0.05%면 롱 포지션이 과열된 구간으로, < -0.03%면 숏 포지션이 과열된 구간으로 흔히 해석합니다.",
    },
    {
        "value": "MOMENTUM_PCT", "label": "모멘텀 (N봉 전 대비 등락률 %)", "category": "추세",
        "params": [{"key": "period", "label": "기간", "default": 5}],
        "description": "N봉 전 종가 대비 현재 종가의 등락률(%)입니다. 양수 임계값이면 최근 상승 흐름(모멘텀)을, 음수 임계값이면 최근 급락(눌림목)을 포착하는 조건으로 쓸 수 있습니다.",
        "example": "period=5, 연산자 >, 임계값 3이면: 5봉 전보다 종가가 3% 이상 오른 상태(모멘텀 진입)를 포착합니다. period=5, 연산자 <, 임계값 -5면: 5봉 전보다 5% 이상 급락한 상태(눌림목/역추세 진입)를 포착합니다.",
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


@app.get("/api/v1/analysis/segments/size")
def get_segment_size_analysis() -> list[dict]:
    return list_segment_classification()


def _trend_segment_ohlcv(market: str) -> list[dict]:
    df = get_candles(market, "days", EARLIEST_CANDLE_START, datetime.now(timezone.utc))
    return [
        {
            "time": _to_utc_iso(row.candle_time.isoformat()),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
        }
        for row in df.itertuples()
    ]


@app.get("/api/v1/analysis/trend-segments/{market}")
def get_trend_segments_endpoint(market: str) -> dict:
    result = get_or_compute_trend_segments(market)
    return {**result, "ohlcv": _trend_segment_ohlcv(market)}


@app.post("/api/v1/analysis/trend-segments/{market}/refresh")
def refresh_trend_segments_endpoint(market: str) -> dict:
    result = get_or_compute_trend_segments(market, force_refresh=True)
    return {**result, "ohlcv": _trend_segment_ohlcv(market)}


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
def get_backtest_runs(market: str | None = Query(None)) -> list[dict]:
    runs = list_backtest_runs(market=market)
    markets_needing_price = {r["market"] for r in runs if has_revaluable_open_trade(r["trades"])}

    live_prices: dict[str, float] = {}
    if markets_needing_price:
        try:
            live_prices = get_current_prices(list(markets_needing_price))
        except Exception:
            live_prices = {}

    result: list[dict] = []
    for r in runs:
        live_price = live_prices.get(r["market"])
        is_live = False
        final_value = r["final_value"]
        return_rate = r["return_rate"]
        trades = r["trades"]
        if live_price is not None and has_revaluable_open_trade(r["trades"]):
            revalued, delta = revalue_open_trades(
                r["trades"], live_price, datetime.now(timezone.utc).isoformat(), r["commission_rate"],
            )
            if delta != 0.0:
                final_value = round(r["final_value"] + delta, 4)
                initial_capital = r["initial_capital"]
                return_rate = (final_value - initial_capital) / initial_capital * 100 if initial_capital else None
                is_live = True
                trades = revalued
        last_trade_status = "open" if (trades and trades[-1].get("forceClosed")) else ("closed" if trades else "none")
        result.append({
            "run_id": r["run_id"],
            "title": r["title"],
            "description": r["description"],
            "market": r["market"],
            "timeframe": r["timeframe"],
            "start": r["start"],
            "end": r["end"],
            "created_at": _to_utc_iso(r["created_at"]),
            "final_value": final_value,
            "return_rate": return_rate,
            "sharpe": r["sharpe"],
            "max_drawdown": r["max_drawdown"],
            "top_trade_contribution_pct": top_trade_contribution_pct(trades),
            "is_live": is_live,
            "last_trade_status": last_trade_status,
            "buy_conditions": r["buy_conditions"],
            "sell_conditions": r["sell_conditions"],
        })
    return result


@app.get("/api/v1/backtests/{run_id}")
def get_backtest_detail(run_id: str) -> dict:
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")

    start_dt = datetime.fromisoformat(result["start"])
    end_dt = datetime.fromisoformat(result["end"])

    # 미청산 포지션이 있고 백테스트 종료일이 이미 지났으면, 그 이후 캔들도 함께
    # 조회해서(a) 가격 차트에 종료일 이후 흐름을 보여주고 (b) 그 마지막 종가를
    # "현재가"로 재사용해 미청산 포지션을 재평가한다(별도 ticker 호출 불필요).
    has_open = has_revaluable_open_trade(result["trades"])
    fetch_end_dt = end_dt
    now = datetime.now(timezone.utc)
    if has_open and now > end_dt:
        fetch_end_dt = now

    try:
        df = get_candles(result["market"], result["timeframe"], start_dt, fetch_end_dt)
    except Exception:
        df = get_candles(result["market"], result["timeframe"], start_dt, end_dt)
        fetch_end_dt = end_dt

    # candle_time은 tz-aware(UTC)인데 trades의 entryTime/exitTime은 backtrader가
    # tz를 벗겨낸 naive 문자열이다(engine/runner.py의 df_bt.index.tz_localize(None)).
    # 프론트에서 new Date(...)로 파싱할 때 tz 표기 유무가 섞이면 로컬 타임존만큼
    # 어긋나 보이므로, 여기서도 naive로 맞춰 캔들/거래 시각의 기준을 통일한다.
    df_chart = df.copy()
    if not df_chart.empty and df_chart["candle_time"].dt.tz is not None:
        df_chart["candle_time"] = df_chart["candle_time"].dt.tz_localize(None)

    trades = result["trades"]
    equity_curve = result["equity_curve"]
    final_value = result["final_value"]
    live_price_as_of = None

    if has_open and fetch_end_dt > end_dt and not df_chart.empty:
        live_close = float(df_chart["close"].iloc[-1])
        live_time = df_chart["candle_time"].iloc[-1].isoformat()
        revalued, delta = revalue_open_trades(trades, live_close, live_time, result["commission_rate"])
        if delta != 0.0:
            final_value = round(final_value + delta, 4)
            equity_curve = equity_curve + [{"timestamp": live_time, "value": final_value}]
            trades = revalued
            live_price_as_of = live_time

    metrics = calculate_metrics(
        equity_curve=equity_curve,
        trades=trades,
        initial_capital=result["initial_capital"],
        df=df,
        timeframe=result["timeframe"],
    )

    ohlcv = [
        {
            "time": _to_utc_iso(row.candle_time.isoformat()),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for row in df_chart.itertuples()
    ]

    trades_out = [
        {**t, "entryTime": _to_utc_iso(t["entryTime"]), "exitTime": _to_utc_iso(t["exitTime"])}
        for t in trades
    ]

    return {
        "market": result["market"],
        "timeframe": result["timeframe"],
        "start": result["start"],
        "end": result["end"],
        "initial_capital": result["initial_capital"],
        "final_value": final_value,
        "metrics": metrics,
        "ohlcv": ohlcv,
        "trades": trades_out,
        "live_price_as_of": _to_utc_iso(live_price_as_of) if live_price_as_of else None,
        "title": result["title"],
        "description": result["description"],
        "created_at": _to_utc_iso(result["created_at"]),
    }


@app.get("/api/v1/backtests/{run_id}/config")
def get_backtest_config_endpoint(run_id: str) -> dict:
    config = get_run_config(run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    return {
        "market": config["market"],
        "timeframe": config["timeframe"],
        "buy_conditions": config["buy_conditions"],
        "sell_conditions": config["sell_conditions"],
    }


@app.delete("/api/v1/backtests/{run_id}")
def delete_backtest(run_id: str) -> dict:
    deleted = delete_backtest_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    return {"deleted": True}


class UpdateBacktestMetadataRequest(BaseModel):
    """title/description을 모두 명시적으로 요구한다(기본값 없음) — 한쪽만 보내면
    없는 쪽이 NULL로 조용히 덮어써지는 사고를 막기 위해, 생략 시 422를 반환하게
    한다. 필드를 실제로 비우고 싶으면 값으로 null을 명시적으로 보내면 된다."""
    title: str | None
    description: str | None


@app.patch("/api/v1/backtests/{run_id}")
def update_backtest_metadata_endpoint(run_id: str, req: UpdateBacktestMetadataRequest) -> dict:
    updated = update_backtest_run_metadata(run_id, req.title, req.description)
    if not updated:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    return {
        "title": result["title"],
        "description": result["description"],
        "created_at": _to_utc_iso(result["created_at"]),
    }


@app.post("/api/v1/backtests/{run_id}/refresh")
def refresh_backtest_endpoint(run_id: str) -> dict:
    config = get_run_config(run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")

    start_dt = datetime.fromisoformat(config["start"])
    end_dt = datetime.now(timezone.utc)
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="시작일이 아직 지나지 않아 갱신할 수 없습니다")

    buy_dict = config["buy_conditions"]
    sell_dict = config["sell_conditions"]
    df = _fetch_backtest_dataframe(config["market"], config["timeframe"], start_dt, end_dt, buy_dict, sell_dict)

    result = run_backtest(
        df, ConditionTreeStrategy, config["risk_config"],
        {"buy_conditions": buy_dict, "sell_conditions": sell_dict},
    )
    save_result(
        run_id=run_id,
        strategy_name=config["strategy_name"],
        strategy_params={"buy_conditions": buy_dict, "sell_conditions": sell_dict},
        market=config["market"],
        timeframe=config["timeframe"],
        start=start_dt,
        end=end_dt,
        risk_config=config["risk_config"],
        result=result,
        title=config["title"],
        description=config["description"],
    )
    return {"run_id": run_id}


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

    if req.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"지원하지 않는 봉데이터입니다: {req.timeframe}")

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()

    if is_empty(buy_dict):
        errors.append("매수 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")
    if is_empty(sell_dict):
        errors.append("매도 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")

    unknown = sorted(set(find_unknown_indicators(buy_dict)) | set(find_unknown_indicators(sell_dict)))
    if unknown:
        errors.append(f"지원하지 않는 지표입니다: {', '.join(unknown)}")

    parsed_dates: dict[str, datetime] = {}
    for label, field, value in (("시작일", "start", req.start), ("종료일", "end", req.end)):
        try:
            parsed_dates[field] = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            errors.append(f"{label} 형식이 올바르지 않습니다(YYYY-MM-DD): {value}")

    if "start" in parsed_dates and "end" in parsed_dates:
        if parsed_dates["start"] >= parsed_dates["end"]:
            errors.append("시작일은 종료일보다 빨라야 합니다.")

    if req.initial_capital <= 0:
        errors.append("운용자금은 0보다 커야 합니다.")

    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")

    return errors


def _fetch_backtest_dataframe(
    market: str, timeframe: str, start_dt: datetime, end_dt: datetime,
    buy_dict: dict, sell_dict: dict,
):
    try:
        df = get_candles(market, timeframe, start_dt, end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=400, detail="해당 기간에 캔들 데이터가 없습니다")

    required_bars = max(max_required_period(buy_dict), max_required_period(sell_dict))
    if len(df) < required_bars:
        raise HTTPException(
            status_code=400,
            detail=(
                f"선택한 지표가 최소 {required_bars}개의 봉을 필요로 하지만, "
                f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리거나 지표 파라미터를 줄이세요."
            ),
        )

    aux_markets = required_aux_markets(buy_dict) | required_aux_markets(sell_dict)
    for aux_market in aux_markets:
        line_name = AUX_MARKET_LINE_NAME[aux_market]
        if market == aux_market:
            df = df.assign(**{line_name: df["close"]})
            continue
        try:
            aux_df = get_candles(aux_market, timeframe, start_dt, end_dt)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if aux_df.empty:
            raise HTTPException(
                status_code=400,
                detail=f"이 조건에 필요한 {aux_market} 캔들 데이터가 해당 기간에 없습니다",
            )
        df = df.merge(
            aux_df[["candle_time", "close"]].rename(columns={"close": line_name}),
            on="candle_time",
            how="left",
        )
        if df[line_name].isna().all():
            raise HTTPException(
                status_code=400,
                detail=f"이 조건에 필요한 {aux_market} 캔들 데이터가 해당 기간에 없습니다",
            )
        df[line_name] = df[line_name].ffill().bfill()

    used_indicators = {
        b["indicator"] for b in collect_blocks(buy_dict) + collect_blocks(sell_dict)
    }
    if "FEAR_GREED_CMC" in used_indicators:
        try:
            fng_df = get_fear_greed_cmc(start_dt, end_dt)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        df = merge_fear_greed(df, fng_df)
        if df["fear_greed_value"].isna().any():
            raise HTTPException(
                status_code=400,
                detail="이 조건에 필요한 공포탐욕지수 데이터가 해당 기간에 없습니다 (2018-02-01 이전 구간은 지원하지 않습니다)",
            )

    if "KOREA_PREMIUM" in used_indicators:
        symbol = binance_symbol(market)
        try:
            binance_df = get_binance_close(symbol, timeframe, start_dt, end_dt)
        except BinanceSymbolNotFoundError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{market}에 대응하는 바이낸스 심볼({symbol})이 없어 "
                    f"한국프리미엄을 계산할 수 없습니다"
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        # usdt_close는 AUX_MARKET_INDICATORS["KOREA_PREMIUM"] = "KRW-USDT" 등록 덕분에
        # 위 aux_markets 루프가 이미 채워둔 값이다.
        df = df.merge(
            binance_df.rename(columns={"close": "binance_close"}), on="candle_time", how="left"
        )
        if df["binance_close"].isna().any():
            raise HTTPException(
                status_code=400, detail=f"해당 기간에 {symbol} 캔들 데이터가 없습니다"
            )
        df["korea_premium_value"] = (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100

    if "FUNDING_RATE" in used_indicators:
        symbol = binance_symbol(market)
        try:
            funding_df = get_binance_funding_rate(symbol, start_dt, end_dt)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        df = merge_funding_rate(df, funding_df)
        if df["funding_rate_value"].isna().all():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{symbol}의 바이낸스 선물 펀딩비 데이터가 해당 기간에 없습니다"
                    "(선물 미상장 또는 기간 밖일 수 있습니다)"
                ),
            )

    return df


@app.post("/api/v1/backtests/run")
def run_backtest_endpoint(req: RunBacktestRequest) -> dict:
    errors = _validate_backtest_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()
    df = _fetch_backtest_dataframe(req.market, req.timeframe, start_dt, end_dt, buy_dict, sell_dict)

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


class GridSearchJobRequest(BaseModel):
    market: str
    timeframe: str
    capital: float
    start: str
    end: str
    top_n: int = 20


def _validate_grid_search_request(req: GridSearchJobRequest) -> list[str]:
    errors: list[str] = []
    if req.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"지원하지 않는 봉데이터입니다: {req.timeframe}")

    parsed_dates: dict[str, datetime] = {}
    for label, field, value in (("시작일", "start", req.start), ("종료일", "end", req.end)):
        try:
            parsed_dates[field] = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            errors.append(f"{label} 형식이 올바르지 않습니다(YYYY-MM-DD): {value}")

    if "start" in parsed_dates and "end" in parsed_dates:
        if parsed_dates["start"] >= parsed_dates["end"]:
            errors.append("시작일은 종료일보다 빨라야 합니다.")

    if req.capital <= 0:
        errors.append("운용자금은 0보다 커야 합니다.")
    if not (1 <= req.top_n <= 50):
        errors.append("상위N개는 1~50 사이여야 합니다.")
    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")
    return errors


def _grid_search_job_response(job: dict) -> dict:
    return {
        **job,
        "started_at": _to_utc_iso(job["started_at"]),
        "finished_at": _to_utc_iso(job["finished_at"]) if job["finished_at"] else None,
    }


@app.post("/api/v1/grid-search/jobs")
def create_grid_search_job_endpoint(req: GridSearchJobRequest) -> dict:
    errors = _validate_grid_search_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    try:
        job_id = start_job(
            market=req.market, timeframe=req.timeframe, capital=req.capital,
            start=req.start, end=req.end, top_n=req.top_n,
        )
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = get_grid_search_job(job_id)
    assert job is not None
    return _grid_search_job_response(job)


@app.get("/api/v1/grid-search/jobs")
def list_grid_search_jobs_endpoint() -> list[dict]:
    return [_grid_search_job_response(j) for j in list_grid_search_jobs()]


@app.post("/api/v1/grid-search/jobs/{job_id}/cancel")
def cancel_grid_search_job_endpoint(job_id: str) -> dict:
    try:
        cancel_job(job_id)
    except JobNotActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "canceling"}


@app.post("/api/v1/grid-search/jobs/reset")
def reset_grid_search_active_job_endpoint() -> dict:
    """`_active`가 실제로는 죽었거나 응답 없는 프로세스를 계속 물고 있어 새 grid
    search가 막힐 때(예: 백엔드를 재시작했다고 믿었는데 이전 프로세스가 Windows에서
    고아로 남아 있던 경우) 사용자가 명시적으로 슬롯을 비우는 엔드포인트."""
    job_id = reset_active_job()
    if job_id is not None:
        job = get_grid_search_job(job_id)
        if job is not None and job["status"] == "running":
            finish_grid_search_job(
                job_id, status="failed",
                error_message="사용자가 실행 중이던 job을 수동으로 초기화했습니다.",
            )
    return {"reset_job_id": job_id}


@app.delete("/api/v1/grid-search/jobs/{job_id}/results/{run_id}")
def delete_grid_search_result_endpoint(job_id: str, run_id: str) -> dict:
    job = get_grid_search_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 job_id의 grid search를 찾을 수 없습니다")
    results = job.get("result_json") or []
    if not any(r["run_id"] == run_id for r in results):
        raise HTTPException(status_code=404, detail="해당 job에 이 run_id의 결과가 없습니다")
    delete_backtest_run(run_id)
    remove_grid_search_result(job_id, run_id)
    return {"deleted": True}


@app.delete("/api/v1/grid-search/jobs/{job_id}")
def delete_grid_search_job_endpoint(job_id: str) -> dict:
    job = get_grid_search_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 job_id의 grid search를 찾을 수 없습니다")
    # 이 job이 아직 _active가 추적 중인 대상이면 함께 정리한다 — 안 그러면 DB 행은
    # 지워졌는데 _active는 그 job_id를 계속 물고 있어 새 grid search가 영영 막힌다
    # (사용자가 실제로 겪은 버그). expected_job_id로 넘겨 무관한 다른 실행 중 job까지
    # 실수로 취소하지 않도록 한다.
    reset_active_job(expected_job_id=job_id)
    for result in job.get("result_json") or []:
        delete_backtest_run(result["run_id"])
    delete_grid_search_job(job_id)
    return {"deleted": True}


class CreateLiveStrategyRiskConfig(BaseModel):
    position_sizing_mode: Literal["fixed", "percent"]
    position_sizing_value: float
    max_position_per_market: float
    order_execution_mode: Literal["market", "limit", "limit_timeout"]
    order_timeout_sec: int = 10
    manual_intervention_policy: Literal["all_stop", "acknowledge_and_continue"]
    daily_loss_limit_pct: float
    consecutive_loss_limit: int


class CreateLiveStrategyRequest(BaseModel):
    source_run_id: str | None = None
    market: str
    timeframe: str
    buy_conditions: ConditionGroupRequest
    sell_conditions: ConditionGroupRequest
    risk_config: CreateLiveStrategyRiskConfig


def _validate_live_strategy_request(req: CreateLiveStrategyRequest) -> list[str]:
    errors: list[str] = []
    if req.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"지원하지 않는 봉데이터입니다: {req.timeframe}")
    try:
        krw_markets = {m["market"] for m in get_krw_markets()}
        if req.market not in krw_markets:
            errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")
    except Exception:
        errors.append("마켓 목록을 불러올 수 없습니다")

    buy_dict = req.buy_conditions.model_dump()
    sell_dict = req.sell_conditions.model_dump()

    if is_empty(buy_dict):
        errors.append("매수 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")
    if is_empty(sell_dict):
        errors.append("매도 조건이 없습니다. 최소 1개 이상의 조건을 추가하세요.")

    unknown = sorted(set(find_unknown_indicators(buy_dict)) | set(find_unknown_indicators(sell_dict)))
    if unknown:
        errors.append(f"지원하지 않는 지표입니다: {', '.join(unknown)}")

    risk = req.risk_config
    if risk.position_sizing_value <= 0:
        errors.append("자금관리 값은 0보다 커야 합니다.")
    if risk.position_sizing_mode == "percent" and risk.position_sizing_value > 100:
        errors.append("퍼센트 자금관리 값은 100 이하여야 합니다.")
    if risk.max_position_per_market <= 0:
        errors.append("코인당 최대 포지션 금액은 0보다 커야 합니다.")
    if risk.order_execution_mode == "limit_timeout" and risk.order_timeout_sec <= 0:
        errors.append("지정가+타임아웃 모드에서는 타임아웃 초가 0보다 커야 합니다.")
    if risk.daily_loss_limit_pct >= 0:
        errors.append("일일 손실 한도는 음수여야 합니다(예: -5.0).")
    if risk.consecutive_loss_limit <= 0:
        errors.append("연속 손실 한도는 0보다 커야 합니다.")
    return errors


def _open_position_summary(position: dict, current_price: float | None) -> dict:
    unrealized_pnl_pct = None
    if current_price is not None and position["entry_price"]:
        unrealized_pnl_pct = (current_price - position["entry_price"]) / position["entry_price"] * 100
    return {
        "entry_price": position["entry_price"],
        "entry_qty": position["entry_qty"],
        "entry_time": position["entry_time"],
        "unrealized_pnl_pct": unrealized_pnl_pct,
    }


def _live_strategy_response(strategy: dict, position: dict | None, current_price: float | None) -> dict:
    closed_positions = trading_db.list_closed_positions(strategy["id"])
    last_closed = closed_positions[0] if closed_positions else None

    if position is not None:
        last_buy_at = _to_utc_iso(position["entry_time"])
    elif last_closed is not None:
        last_buy_at = _to_utc_iso(last_closed["entry_time"])
    else:
        last_buy_at = None
    last_sell_at = _to_utc_iso(last_closed["exit_time"]) if last_closed is not None else None

    return {
        "id": strategy["id"],
        "source_run_id": strategy["source_run_id"],
        "market": strategy["market"],
        "timeframe": strategy["timeframe"],
        "status": strategy["status"],
        "current_capital": strategy["current_capital"],
        "created_at": strategy["created_at"],
        "approved_at": strategy["approved_at"],
        "started_at": strategy["started_at"],
        "stopped_at": strategy["stopped_at"],
        "open_position": _open_position_summary(position, current_price) if position else None,
        "last_buy_at": last_buy_at,
        "last_sell_at": last_sell_at,
        "buy_conditions": json.loads(strategy["buy_conditions_json"]),
        "sell_conditions": json.loads(strategy["sell_conditions_json"]),
        "risk_config": json.loads(strategy["risk_config_json"]),
        "capital_adjustments": [
            {
                "id": adj["id"],
                "adjusted_at": _to_utc_iso(adj["adjusted_at"]),
                "previous_capital": adj["previous_capital"],
                "new_capital": adj["new_capital"],
                "delta": adj["delta"],
            }
            for adj in trading_db.list_capital_adjustments(strategy["id"])
        ],
    }


def _full_live_strategy_response(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    position = trading_db.get_open_position(strategy_id)
    current_price = None
    if position is not None:
        try:
            prices = get_current_prices([strategy["market"]])
            current_price = prices.get(strategy["market"])
        except Exception:
            current_price = None
    return _live_strategy_response(strategy, position, current_price)


@app.post("/api/v1/live-strategies")
def create_live_strategy_endpoint(req: CreateLiveStrategyRequest) -> dict:
    errors = _validate_live_strategy_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    strategy_id = trading_db.insert_live_strategy(
        source_run_id=req.source_run_id,
        market=req.market,
        timeframe=req.timeframe,
        buy_conditions_json=json.dumps(req.buy_conditions.model_dump()),
        sell_conditions_json=json.dumps(req.sell_conditions.model_dump()),
        risk_config_json=json.dumps(req.risk_config.model_dump()),
    )
    return _full_live_strategy_response(strategy_id)


@app.get("/api/v1/live-strategies")
def list_live_strategies_endpoint() -> list[dict]:
    strategies = trading_db.list_live_strategies()
    positions = {s["id"]: trading_db.get_open_position(s["id"]) for s in strategies}
    open_markets = {s["market"] for s in strategies if positions[s["id"]] is not None}
    try:
        current_prices = get_current_prices(list(open_markets)) if open_markets else {}
    except Exception:
        current_prices = {}
    return [
        _live_strategy_response(s, positions[s["id"]], current_prices.get(s["market"]))
        for s in strategies
    ]


@app.post("/api/v1/live-strategies/{strategy_id}/approve")
async def approve_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] != "draft":
        raise HTTPException(status_code=409, detail="draft 상태의 전략만 승인할 수 있습니다")

    risk_config = json.loads(strategy["risk_config_json"])
    try:
        accounts = await upbit_client.get_accounts()
    except UpbitCredentialsError as exc:
        raise HTTPException(
            status_code=400, detail="업비트 API 인증에 실패했습니다 — API 키 설정을 확인하세요",
        ) from exc
    except UpbitRateLimitError as exc:
        raise HTTPException(
            status_code=400,
            detail="업비트 API 요청이 지나치게 많습니다 — 잠시 후 다시 시도하세요",
        ) from exc
    except httpx.HTTPStatusError as exc:
        # UpbitCredentialsError는 access/secret key 환경변수 자체가 없을 때만 던져진다
        # (upbit_client._build_jwt_headers) — 키는 있는데 값이 틀렸거나 IP 화이트리스트
        # 미등록 등으로 업비트가 401/403을 반환하면 여기로 온다. 이걸 아래 httpx.HTTPError
        # 분기로 뭉뚱그리면 "네트워크 장애"와 "인증 거부"가 똑같은 메시지가 돼 원인 파악이
        # 어려워진다(사용자가 실제로 겪은 혼선, 백로그 항목).
        if exc.response.status_code in (401, 403):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"업비트 API 인증이 거부됐습니다(status={exc.response.status_code}) — "
                    "API 키 값/권한/IP 화이트리스트를 확인하세요"
                ),
            ) from exc
        raise HTTPException(
            status_code=400,
            detail=f"업비트 서버가 오류를 반환했습니다(status={exc.response.status_code})",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400, detail="업비트 서버와 통신할 수 없습니다",
        ) from exc
    krw_account = next((a for a in accounts if a["currency"] == "KRW"), None)
    available_balance = float(krw_account["balance"]) if krw_account else 0.0

    try:
        initial_capital = position_manager.calculate_initial_capital(risk_config, available_balance)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    running_capital_sum = sum(s["current_capital"] or 0.0 for s in trading_db.list_active_strategies())
    required = running_capital_sum + initial_capital
    if required > available_balance:
        raise HTTPException(
            status_code=400,
            detail=(
                f"가용 잔고 {available_balance:,.0f}원, 필요 자금 {required:,.0f}원"
                f"(기존 전략 {running_capital_sum:,.0f}원 + 신규 {initial_capital:,.0f}원)"
            ),
        )

    approved = trading_db.approve_live_strategy(strategy_id, initial_capital)
    if not approved:
        raise HTTPException(
            status_code=409,
            detail="승인 처리 중 전략 상태가 변경되었습니다. 새로고침 후 다시 시도하세요",
        )
    return _full_live_strategy_response(strategy_id)


@app.post("/api/v1/live-strategies/{strategy_id}/pause")
def pause_live_strategy_endpoint(strategy_id: str) -> dict:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if not trading_db.pause_live_strategy_manually(strategy_id):
        raise HTTPException(status_code=409, detail="running 상태의 전략만 일시정지할 수 있습니다")
    return _full_live_strategy_response(strategy_id)


@app.post("/api/v1/live-strategies/{strategy_id}/resume")
def resume_live_strategy_endpoint(strategy_id: str) -> dict:
    if trading_db.get_live_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if not trading_db.resume_live_strategy_manually(strategy_id):
        raise HTTPException(status_code=409, detail="paused 상태의 전략만 재개할 수 있습니다")
    return _full_live_strategy_response(strategy_id)


@app.post("/api/v1/live-strategies/{strategy_id}/stop")
def stop_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] == "stopped":
        raise HTTPException(status_code=409, detail="이미 중지된 전략입니다")
    if not trading_db.stop_live_strategy_if_no_open_position(strategy_id):
        raise HTTPException(
            status_code=400,
            detail="열린 포지션이 있어 중지할 수 없습니다. 먼저 포지션을 정리하세요",
        )
    return _full_live_strategy_response(strategy_id)


class UpdateLiveStrategyCapitalRequest(BaseModel):
    new_capital: float


@app.patch("/api/v1/live-strategies/{strategy_id}/capital")
def update_live_strategy_capital_endpoint(strategy_id: str, req: UpdateLiveStrategyCapitalRequest) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="running 또는 paused 상태의 전략만 시드를 변경할 수 있습니다")
    if not math.isfinite(req.new_capital) or req.new_capital <= 0:
        raise HTTPException(status_code=400, detail="시드는 0보다 큰 유한한 숫자여야 합니다")
    max_position = json.loads(strategy["risk_config_json"]).get("max_position_per_market")
    if max_position is not None and req.new_capital > max_position:
        raise HTTPException(
            status_code=400,
            detail=f"코인당 최대 포지션 금액({max_position:,.0f}원)을 초과하는 시드로는 변경할 수 없습니다. 먼저 최대 포지션 설정을 올리세요.",
        )
    if not trading_db.adjust_live_strategy_capital_if_no_open_position(
        strategy_id, strategy["current_capital"], req.new_capital
    ):
        raise HTTPException(status_code=400, detail="포지션 보유 중에는 시드를 변경할 수 없습니다")
    return _full_live_strategy_response(strategy_id)


@app.delete("/api/v1/live-strategies/{strategy_id}")
def delete_live_strategy_endpoint(strategy_id: str) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] != "stopped":
        raise HTTPException(status_code=409, detail="중지된 전략만 삭제할 수 있습니다")
    trading_db.delete_live_strategy(strategy_id)
    return {"deleted": True}


class ReplaceLiveStrategyRequest(BaseModel):
    source_run_id: str


@app.post("/api/v1/live-strategies/{strategy_id}/replace-strategy")
def replace_live_strategy_endpoint(strategy_id: str, req: ReplaceLiveStrategyRequest) -> dict:
    strategy = trading_db.get_live_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="해당 id의 라이브 전략을 찾을 수 없습니다")
    if strategy["status"] == "draft":
        raise HTTPException(status_code=409, detail="draft 상태의 전략은 교체할 수 없습니다")

    config = get_run_config(req.source_run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    if config["strategy_name"] != "ConditionTreeStrategy":
        raise HTTPException(status_code=400, detail="지원하지 않는 백테스트 결과입니다")
    if config["market"] != strategy["market"]:
        raise HTTPException(status_code=400, detail="선택한 백테스트 결과의 마켓이 현재 전략과 다릅니다")
    if config["timeframe"] not in VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 봉데이터입니다: {config['timeframe']}")
    if is_empty(config["buy_conditions"]) or is_empty(config["sell_conditions"]):
        raise HTTPException(status_code=400, detail="매수/매도 조건이 비어 있는 백테스트 결과입니다")
    unknown = sorted(
        set(find_unknown_indicators(config["buy_conditions"]))
        | set(find_unknown_indicators(config["sell_conditions"]))
    )
    if unknown:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 지표입니다: {', '.join(unknown)}")

    replaced = trading_db.replace_live_strategy_strategy(
        strategy_id,
        source_run_id=req.source_run_id,
        timeframe=config["timeframe"],
        buy_conditions_json=json.dumps(config["buy_conditions"]),
        sell_conditions_json=json.dumps(config["sell_conditions"]),
    )
    if not replaced:
        raise HTTPException(status_code=409, detail="포지션이 열려 있어 교체할 수 없습니다")
    return _full_live_strategy_response(strategy_id)


@app.get("/api/v1/journal/summary")
def get_journal_summary_endpoint() -> dict:
    return get_journal_summary()


@app.get("/api/v1/journal/markets/{market}")
def get_journal_market_endpoint(market: str) -> dict:
    detail = get_market_journal(market)
    if detail is None:
        raise HTTPException(status_code=404, detail="실거래 이력이 없는 코인입니다")
    return detail
