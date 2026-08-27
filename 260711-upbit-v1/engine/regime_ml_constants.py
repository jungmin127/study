"""
engine/regime_ml_constants.py

장세 판별 ML 파이프라인 전체(학습+추론)가 공유하는 상수. 학습 스크립트
(scripts/train_regime_ml.py)와 추론 서비스(backend/regime_ml_service.py)가 서로
다른 마켓 목록을 갖게 되는 걸 막기 위해 단일 소스로 뽑았다. 프론트엔드
(frontend/components/RegimeMlCurrentPrediction.tsx)는 이 값을 API로 받지 않고
하드코딩된 배열을 따로 유지하며, tests/test_regime_ml_constants_frontend_sync.py가
드리프트를 감시한다. scripts/regime_backtest.py(규칙기반 검증 CLI, ML 파이프라인과
무관)도 자체 MARKETS 상수를 별도로 정의하고 있는데, 이는 의도적으로 분리된 목록이다
(규칙기반 백테스트 검증 대상 vs. ML 학습 대상은 다른 개념) — TRAINING_MARKETS와
엮어서 단일화하지 말 것.
"""
from __future__ import annotations

TRAINING_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
