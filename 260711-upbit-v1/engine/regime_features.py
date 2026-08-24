"""
engine/regime_features.py

장세 판별기(engine/regime_detector.py)가 쓰는 보조 신호 — 거래량 확인, VPIN 매수/매도
불균형, 지지/저항 근접도. 전부 순수 pandas 함수(backtrader 의존 없음, I/O 없음)라
백테스트/그리드서치/라이브 데몬 어디서든 재사용 가능하다. 설계 문서:
docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md

engine/indicators/volume.py, price_levels.py의 backtrader 지표(Cerebro 전략 객체 모델
안에서만 동작)와 동일한 계산 로직을 pandas Series 기반으로 재구현한다 — regime_detector가
Cerebro 없이 순수 DataFrame만으로 호출돼야 하므로 기존 지표 클래스를 그대로 재사용할 수
없다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# regime_detector.py의 동명 상수와 값이 같아야 한다. regime_detector가 이 모듈을
# import하므로(반대 방향은 순환참조), backend/regime_service.py의 _to_utc_iso와
# 같은 이유로 별도 정의한다.
_MIN_VOLATILITY_FLOOR = 1e-6


def volume_confirm(trade_value: pd.Series, period: int = 20) -> pd.Series:
    """거래대금이 자체 이동평균(period봉) 대비 얼마나 실렸는지를 [0.7, 1.3] 배율로
    변환한다. engine/indicators/volume.py:111-124(TradeValueRatio)와 동일한 정의를
    pandas로 재구현. 방향(상승/하락) 무관 — 평균보다 거래대금이 실린 봉이면 모멘텀
    점수를 증폭, 안 실렸으면 감쇠시키는 용도."""
    sma = trade_value.rolling(period).mean()
    ratio = (trade_value - sma) / sma.replace(0.0, np.nan)
    ratio = ratio.fillna(0.0)
    return 1.0 + ratio.clip(-0.3, 0.3)
