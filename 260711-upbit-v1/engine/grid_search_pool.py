"""
engine/grid_search_pool.py

그리드서치 지표 풀 스펙 레지스트리 + 그리드 빌더. `scripts/grid_search.py`가
`backend/main.py`의 `_fetch_backtest_dataframe`를 임포트하면서 생긴 순환 임포트
(`backend/main.py`가 이 파일의 `INDICATOR_POOL_SPECS`/`build_condition_grid`를 다시
임포트해야 하는 상황)를 끊기 위해 추출했다. 이 파일은 FastAPI를 포함해 이 프로젝트의
다른 어떤 모듈도 임포트하지 않는 순수 데이터/순수 함수만 담는다 — 그래야
`backend/main.py`와 `scripts/grid_search.py` 양쪽에서 안전하게 임포트할 수 있다.
"""
from __future__ import annotations

PERIOD_GRID = [10, 14, 20]


def _period_grid(key: str = "period") -> list[dict]:
    return [{key: p} for p in PERIOD_GRID]


OSCILLATOR_SPECS: dict[str, dict] = {
    "RSI": {"param_grid": _period_grid(), "low": [20, 30, 40], "high": [60, 70, 80], "bidirectional": False},
    "STOCH_K": {"param_grid": _period_grid("k_period"), "low": [10, 20, 30], "high": [70, 80, 90], "bidirectional": False},
    "STOCH_D": {"param_grid": _period_grid("k_period"), "low": [10, 20, 30], "high": [70, 80, 90], "bidirectional": False},
    "CCI": {"param_grid": _period_grid(), "low": [-140, -100, -60], "high": [60, 100, 140], "bidirectional": False},
    "WILLIAMS_R": {"param_grid": _period_grid(), "low": [-90, -80, -70], "high": [-30, -20, -10], "bidirectional": False},
    "BB_PERCENT_B": {"param_grid": _period_grid(), "low": [0.0, 0.1, 0.2], "high": [0.8, 0.9, 1.0], "bidirectional": False},
    "MACD_PPO": {
        "param_grid": [
            {"fast": f, "slow": s, "signal": sig} for f in [12, 16] for s in [26, 32] for sig in [9, 12]
        ],
        "low": [-3, -2, -1],
        "high": [1, 2, 3],
        "bidirectional": False,
    },
    "MACD_PPO_signal": {
        "param_grid": [
            {"fast": f, "slow": s, "signal": sig} for f in [12, 16] for s in [26, 32] for sig in [9, 12]
        ],
        "low": [-3, -2, -1],
        "high": [1, 2, 3],
        "bidirectional": False,
    },
    "ATR_PCT": {"param_grid": _period_grid(), "low": [0.5, 1, 2, 3, 5, 8], "high": [], "bidirectional": True},
}

# 아래 4개 카테고리의 low/high 값은 KRW-ETH/KRW-XRP, 1시간봉, 2026-01-01~2026-08-20 구간에서
# 실측한 값 분포의 백분위수(p10/p20/p30/p70/p80/p90)를 반올림해 정했다(스펙 문서 참고).
# FEAR_GREED_CMC만 표본 구간이 공포 국면에 쏠려 있어 문헌상 관례값(극단적 공포<25, 극단적 탐욕>75)으로 대체했다.
TREND_SPECS: dict[str, dict] = {
    "SMA_PCT": {"param_grid": _period_grid(), "low": [-1.0, -0.6, -0.3], "high": [0.3, 0.5, 0.9], "bidirectional": False},
    "EMA_PCT": {"param_grid": _period_grid(), "low": [-0.9, -0.5, -0.3], "high": [0.25, 0.45, 0.8], "bidirectional": False},
    "WMA_PCT": {"param_grid": _period_grid(), "low": [-0.8, -0.45, -0.25], "high": [0.2, 0.4, 0.7], "bidirectional": False},
    "MOMENTUM_PCT": {
        "param_grid": [{"period": p} for p in (5, 10, 20)],
        "low": [-1.1, -0.6, -0.35], "high": [0.3, 0.6, 1.0], "bidirectional": False,
    },
}

PRICE_LEVEL_SPECS: dict[str, dict] = {
    "FIB_382_PCT": {"param_grid": _period_grid(), "low": [-1.8, -1.2, -0.8], "high": [0.15, 0.35, 0.7], "bidirectional": False},
    "FIB_500_PCT": {"param_grid": _period_grid(), "low": [-1.3, -0.8, -0.5], "high": [0.4, 0.7, 1.15], "bidirectional": False},
    "FIB_618_PCT": {"param_grid": _period_grid(), "low": [-0.85, -0.45, -0.2], "high": [0.7, 1.0, 1.6], "bidirectional": False},
    "PIVOT_P_PCT": {"param_grid": [{}], "low": [-0.5, -0.3, -0.15], "high": [0.15, 0.3, 0.5], "bidirectional": False},
    "PIVOT_R1_PCT": {"param_grid": [{}], "low": [-1.0, -0.65, -0.5], "high": [-0.1, 0.0, 0.17], "bidirectional": False},
    "PIVOT_S1_PCT": {"param_grid": [{}], "low": [-0.15, 0.0, 0.1], "high": [0.45, 0.65, 0.95], "bidirectional": False},
    "VPVR_POC_PCT": {
        "param_grid": [{"period": p} for p in (30, 50, 70)],
        "low": [-2.3, -1.3, -0.6], "high": [0.5, 1.0, 2.0], "bidirectional": False,
    },
    "VPVR_VAH_PCT": {
        "param_grid": [{"period": p} for p in (30, 50, 70)],
        "low": [-4.0, -2.7, -2.0], "high": [-0.3, 0.0, 0.5], "bidirectional": False,
    },
    "VPVR_VAL_PCT": {
        "param_grid": [{"period": p} for p in (30, 50, 70)],
        "low": [-0.6, 0.0, 0.35], "high": [1.7, 2.4, 3.7], "bidirectional": False,
    },
}

VOLUME_SPECS: dict[str, dict] = {
    "OBV_ROC": {"param_grid": _period_grid(), "low": [-45, -35, -23], "high": [12, 22, 35], "bidirectional": False},
    "VOLUME_PCT": {"param_grid": _period_grid(), "low": [-65, -53, -43], "high": [12, 40, 95], "bidirectional": False},
    "VPIN": {"param_grid": _period_grid(), "low": [0.4, 0.45, 0.5, 0.55], "high": [], "bidirectional": True},
}

TRADE_VALUE_SPECS: dict[str, dict] = {
    "TRADE_VALUE_PCT": {"param_grid": _period_grid(), "low": [-65, -53, -43], "high": [12, 40, 95], "bidirectional": False},
}

MARKET_SENTIMENT_SPECS: dict[str, dict] = {
    "MARKET_TREND_PCT": {"param_grid": _period_grid(), "low": [-0.7, -0.43, -0.24], "high": [0.22, 0.38, 0.69], "bidirectional": False},
    "BTC_CORRELATION": {"param_grid": _period_grid(), "low": [0.6, 0.71, 0.77], "high": [0.88, 0.91, 0.93], "bidirectional": False},
    "USDT_CORRELATION": {"param_grid": _period_grid(), "low": [-0.64, -0.54, -0.44], "high": [-0.1, 0.0, 0.17], "bidirectional": False},
    "FEAR_GREED_CMC": {"param_grid": [{}], "low": [20, 25, 30], "high": [65, 70, 75], "bidirectional": False},
    "KOREA_PREMIUM": {"param_grid": [{}], "low": [-0.09, -0.06, -0.03], "high": [0.05, 0.07, 0.1], "bidirectional": False},
    "FUNDING_RATE": {"param_grid": [{}], "low": [-0.008, -0.004, -0.0025], "high": [0.004, 0.0054, 0.008], "bidirectional": False},
}

# 카테고리명은 backend/main.py의 INDICATOR_CATALOG가 쓰는 표기와 동일하게 맞춘다(프론트 폼의
# 카테고리 체크박스 라벨이 그대로 이 dict의 키가 된다). "손익"은 여기 없다 — 포지션 청산
# 메커니즘이라 풀 선택과 무관하게 항상 SELL_ONLY로 매도 조건에 포함된다.
INDICATOR_POOL_SPECS: dict[str, dict[str, dict]] = {
    "오실레이터": OSCILLATOR_SPECS,
    "추세": TREND_SPECS,
    "가격대": PRICE_LEVEL_SPECS,
    "거래량": VOLUME_SPECS,
    "거래대금": TRADE_VALUE_SPECS,
    "시장 심리": MARKET_SENTIMENT_SPECS,
}

SELL_ONLY: dict[str, tuple[str, list[int]]] = {
    "STOP_LOSS_PCT": ("<=", [-3, -5, -7, -10]),
    "TAKE_PROFIT_PCT": (">=", [5, 10, 15, 20]),
    "HOLDING_PERIOD_BARS": (">=", [5, 10, 20, 40]),
}

# 대상 마켓이 이 지표가 비교 대상으로 삼는 마켓 자신이면 자기상관(항상 정확히 1.0)으로
# 퇴화한다 — RollingCorrelation(data.close, data.btc_close, ...)에서 market==KRW-BTC면
# data.close와 data.btc_close가 같은 라인이 되기 때문(engine/indicators/market.py 참고).
# 크래시는 안 나지만 threshold와 무관하게 조건이 항상 참/거짓으로 고정돼 그리드서치
# 결과를 의미 없이 오염시키므로, 해당 마켓을 백테스트할 때는 풀에서 제외한다.
SELF_CORRELATION_INDICATORS: dict[str, str] = {
    "KRW-BTC": "BTC_CORRELATION",
    "KRW-USDT": "USDT_CORRELATION",
}


def _selected_specs(pool: dict | None, market: str | None = None) -> dict[str, dict]:
    """pool 인자를 실제로 순회할 {지표명: 스펙} dict로 해석한다.

    pool이 None이면 기존 동작(오실레이터 전용)과 완전히 동일하게 OSCILLATOR_SPECS만
    반환한다 — build_condition_grid()를 인자 없이 호출하는 기존 호출부/테스트가
    바뀌지 않아야 하기 때문이다. market이 SELF_CORRELATION_INDICATORS에 등록된
    마켓이면(KRW-BTC/KRW-USDT) 그 마켓 자신과의 자기상관 지표를 결과에서 제외한다."""
    if pool is None:
        specs = dict(OSCILLATOR_SPECS)
    else:
        categories = pool.get("categories") or ["오실레이터"]
        excluded = set(pool.get("excluded_indicators") or [])
        specs = {
            indicator: spec
            for category in categories
            for indicator, spec in INDICATOR_POOL_SPECS.get(category, {}).items()
            if indicator not in excluded
        }
    self_correlation_indicator = SELF_CORRELATION_INDICATORS.get(market or "")
    if self_correlation_indicator:
        specs.pop(self_correlation_indicator, None)
    return specs


def build_condition_grid(pool: dict | None = None, market: str | None = None) -> tuple[list[dict], list[dict]]:
    """선택된 지표 풀의 매수/매도 ConditionBlock 그리드를 생성한다.

    Args:
        pool: {"categories": list[str], "excluded_indicators": list[str]} 또는 None.
            None이면 오실레이터 9종만 순회한다(기존 동작과 동일).
        market: 백테스트 대상 마켓코드(예: "KRW-BTC"). SELF_CORRELATION_INDICATORS에
            등록된 마켓이면 자기상관으로 퇴화하는 지표를 풀에서 제외한다. None이면
            제외 없이 기존 동작과 동일하다.

    Returns:
        (buy_conditions, sell_conditions) — 각각 ConditionBlock 딕셔너리 리스트
        ({"indicator": str, "params": dict, "operator": str, "threshold": float}).
        손익(SELL_ONLY) 3종은 풀 선택과 무관하게 항상 매도 조건에 포함된다.
    """
    buy_conditions: list[dict] = []
    sell_conditions: list[dict] = []

    for indicator, spec in _selected_specs(pool, market).items():
        for params in spec["param_grid"]:
            if spec["bidirectional"]:
                for t in spec["low"]:
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})
            else:
                for t in spec["low"]:
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                for t in spec["high"]:
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})

    for indicator, (operator, thresholds) in SELL_ONLY.items():
        for t in thresholds:
            sell_conditions.append({"indicator": indicator, "params": {}, "operator": operator, "threshold": t})

    return buy_conditions, sell_conditions
