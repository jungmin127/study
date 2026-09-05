"""
engine/regime_adx_constants.py

"장세 판별" 탭 오버뷰 히트맵 + 3단계 전략 라이브러리 UI가 공유하는 대상
코인 목록. 설계 문서: docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md

**확장 포인트**: 코인 추가/제외는 이 리스트만 수정하면 된다. 한글명은
저장하지 않는다 — 프론트가 기존 getMarkets() API로 조회해 표시한다.
프론트 미러(frontend/lib/constants/regime.ts)와 값을 반드시 동기화해야
하며, tests/test_regime_adx_constants_frontend_sync.py가 이를 감시한다.
"""
MAJOR_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA", "KRW-DOGE",
    "KRW-LINK", "KRW-DOT", "KRW-AVAX", "KRW-TRX", "KRW-POL", "KRW-BCH",
    "KRW-ETC", "KRW-XLM", "KRW-ATOM", "KRW-UNI", "KRW-NEAR", "KRW-ICP",
    "KRW-HBAR", "KRW-SUI",
]
