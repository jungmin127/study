"""
backend/main.py

EDA 대시보드용 FastAPI 앱. engine.cache의 SQLite 저장소를 직접 조회한다.
Run: uvicorn backend.main:app --reload --port 8000  (저장소 루트에서 실행)
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.cache import (
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    load_result,
    run_backtest_cached,
)
from engine.strategies import SignalStrategy
from engine.sweep import DEFAULT_RISK_CONFIG
from signals import SIGNAL_REGISTRY
from upbit_data_service import get_candles

app = FastAPI(title="Upbit Strategy EDA API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/v1/eda/history")
def get_history(
    signal_set_name: str = Query(...),
    market: str = Query(...),
    timeframe: str = Query(...),
    is_combined: bool = Query(...),
) -> list[dict]:
    return list_sweep_history(signal_set_name, market, timeframe, is_combined)


@app.get("/api/v1/backtests/{run_id}")
def get_backtest_detail(run_id: str) -> dict:
    result = load_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 결과를 찾을 수 없습니다")
    return result


class RunBacktestRequest(BaseModel):
    market: str
    timeframe: str
    start: str
    end: str
    signal_keys: list[str]


@app.post("/api/v1/backtests/run")
def run_backtest_endpoint(req: RunBacktestRequest) -> dict:
    if not req.signal_keys:
        raise HTTPException(status_code=400, detail="전략을 최소 1개 선택해야 합니다")

    unknown_keys = [k for k in req.signal_keys if k not in SIGNAL_REGISTRY]
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 전략입니다: {unknown_keys}")

    if req.start >= req.end:
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 빨라야 합니다")

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

    signals = [SIGNAL_REGISTRY[k] for k in req.signal_keys]

    result = run_backtest_cached(
        df=df,
        strategy_cls=SignalStrategy,
        risk_config=DEFAULT_RISK_CONFIG,
        market=req.market,
        timeframe=req.timeframe,
        start=start_dt,
        end=end_dt,
        strategy_params={"signals": signals},
    )
    return {"run_id": result["run_id"]}
