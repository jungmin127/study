"""
engine/regime_ml_features.py

장세 판별 ML 분류기의 피처 매트릭스를 만든다. trading.live_indicators.LIVE_INDICATOR_FACTORY
(이미 백트레이더 대비 골든테스트로 검증된 순수 pandas 지표)를 재구현 없이 그대로
순회하고, engine.regime_features.py의 반전게이팅 실험용 5개 함수 + momentum/volatility
EWMA(raw_score)를 더한다. 코인 차별화 피처(자기상대적, 2026-08-29 문제 재정의 도입)도
추가한다 — VOLATILITY_PERCENTILE/LIQUIDITY_PERCENTILE(둘 다 이 마켓 자신의 과거 1년
분포 대비 백분위). 공유 풀링 모델이 코인마다 다른 신호를 갖게 하려는 목적이며, 다른
마켓 데이터를 참조하지 않아(자기 자신의 df만 사용) 추론 시 여러 마켓을 새로 불러올
필요가 없다. I/O 없는 순수 함수 — 입력 df는 engine/regime_ml_data.py가 준비한다. 설계
문서: docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md

원래 세 번째 피처로 LISTING_AGE_BARS(상장 후 경과 봉 수)도 있었으나 2026-08-30
ablation 실험으로 제거했다 — engine.regime_ml_data.load_market_training_data가 항상
고정된 start(TRAIN_START)부터 캔들을 불러오기 때문에, 그 시점 이전에 이미 상장돼
있던 마켓(14개 중 13개)에게는 "실제 상장 이후 경과"가 아니라 사실상 모든 마켓에
동일하게 찍히는 "TRAIN_START부터 경과한 시간"이었다. 워크포워드 fold는 시간순으로
나뉘므로 이 피처가 암묵적으로 "지금이 몇 번째 fold냐"를 알려주는 캘린더 프록시로
작동해, 학습 구간 초반에만 통하는 패턴을 학습시켜 out-of-fold 일반화를 오히려
깎아먹었다(게인 1위, 2위의 1.7배였는데도). 제거 후 pooled weighted kappa
0.028→0.065로 개선 확인.

같은 날 이어진 ablation에서 FEAR_GREED_CMC도 제거했다 — LISTING_AGE_BARS와
마찬가지로 모든 마켓에 동일 시점·동일 값이 찍히는 전 마켓 공유 매크로 시계열이라
(공포탐욕지수 자체는 진짜 정보값이 있어 순수 캘린더 프록시는 아니지만) 게인
2위였는데도 제거가 순이익이었다(단독 제거 kappa 0.060→0.063). barrier_k를
5.5→6.25로 조정(기존 5.5는 클래스 분포 균형만 기준으로 뽑은 값이라 kappa 기준
재탐색; 4.0/4.75/5.5/6.25/7.0 그리드서치)한 것과 조합하면 kappa 0.060→0.072로
두 효과가 거의 더해진다(상쇄되지 않음). 실험 스크립트는 커밋하지 않음(scratch,
일회성).
"""
from __future__ import annotations

import pandas as pd

from engine.regime_features import (
    level_proximity,
    pivot_levels,
    reversal_gate,
    vpin_score,
    volume_confirm,
)
from trading.live_indicators import LIVE_INDICATOR_FACTORY

# engine/regime_features.py:_MIN_VOLATILITY_FLOOR와 값이 같아야 한다(raw_score
# 0-나눗셈 방지) — 순환참조를 피하려 별도 정의.
_MIN_VOLATILITY_FLOOR = 1e-6
_PERCENTILE_WINDOW_BARS = 8760  # 1시간봉 기준 1년
_PERCENTILE_MIN_PERIODS = 100  # 약 4일치 이상 쌓이면 백분위 계산 시작(신규상장 코인도 이른 시점부터 값이 나오게)


def build_feature_matrix(df: pd.DataFrame, market: str, half_life_bars: float) -> pd.DataFrame:
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value/usdkrw_rate_value를
    전부 포함해야 한다(engine.regime_ml_data.load_market_training_data()가 반환하는
    형태). 반환
    DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN), 원본 OHLCV
    컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
    # OBV(create_obv)는 윈도우 없는 누적합이라 추론 시(짧은 최근 구간)와 학습
    # 시(수년치) 스케일이 어긋난다(backend/regime_ml_service.py 참고) — 피처에서
    # 제외한다. 같은 레지스트리의 OBV_ROC는 rolling window 기반 %지표라 스케일
    # 문제가 없으므로 그대로 둔다. FEAR_GREED_CMC는 모듈 독스트링에 적었듯
    # ablation으로 제거가 순이익임을 확인해 제외한다.
    _EXCLUDED_INDICATORS = {"OBV", "FEAR_GREED_CMC"}
    features: dict[str, pd.Series] = {
        name: factory(df) for name, factory in LIVE_INDICATOR_FACTORY.items() if name not in _EXCLUDED_INDICATORS
    }

    returns = df["close"].pct_change(fill_method=None)
    momentum = returns.ewm(halflife=half_life_bars).mean()
    volatility = returns.ewm(halflife=half_life_bars).std()
    raw_score = momentum / volatility.clip(lower=_MIN_VOLATILITY_FLOOR)
    r1, s1 = pivot_levels(df["high"], df["low"], df["close"])
    proximity = level_proximity(df["close"], raw_score, r1, s1, volatility)
    vpin = vpin_score(df["volume"], df["close"])

    features["RAW_SCORE"] = raw_score
    features["VOLUME_CONFIRM"] = volume_confirm(df["trade_value"])
    features["VPIN_SCORE"] = vpin
    features["LEVEL_PROXIMITY"] = proximity
    features["REVERSAL_GATE"] = reversal_gate(vpin, proximity)

    features["VOLATILITY_PERCENTILE"] = volatility.rolling(
        _PERCENTILE_WINDOW_BARS, min_periods=_PERCENTILE_MIN_PERIODS
    ).rank(pct=True)
    features["LIQUIDITY_PERCENTILE"] = df["trade_value"].rolling(
        _PERCENTILE_WINDOW_BARS, min_periods=_PERCENTILE_MIN_PERIODS
    ).rank(pct=True)

    # 환율 피처(2026-08-31 추가) — Frankfurter 공식 USD/KRW 환율의 변동률/변동성과,
    # 업비트 암묵환율(usdt_close) 대비 괴리(자본유출입/크립토 유동성 프리미엄 신호
    # 후보). 원시 레벨 자체는 넣지 않는다 — 2024~2026 구간에 추세적으로 움직이면
    # LISTING_AGE_BARS처럼 fold 위치 프록시가 될 위험이 있어 변동률/스프레드처럼
    # 상대적으로 정상성(stationary)이 높은 형태만 쓴다.
    fx_return = df["usdkrw_rate_value"].pct_change(fill_method=None)
    features["USDKRW_RETURN"] = fx_return
    features["USDKRW_VOLATILITY"] = fx_return.ewm(halflife=half_life_bars).std()
    features["UPBIT_FX_SPREAD"] = (df["usdt_close"] / df["usdkrw_rate_value"] - 1) * 100

    result = pd.DataFrame(features, index=df.index)
    result["market"] = pd.Categorical([market] * len(df))
    return result
