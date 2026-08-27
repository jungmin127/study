"""
tests/test_regime_ml_constants_frontend_sync.py

engine.regime_ml_constants.TRAINING_MARKETS와
frontend/components/RegimeMlCurrentPrediction.tsx의 TRAINED_MARKETS 배열이 어긋나지
않는지 감시하는 가드레일 테스트. 프론트는 API 호출 없이 하드코딩 배열을 그대로
유지하기로 결정했으므로(docs/superpowers/specs/2026-08-27-regime-ml-backlog-cleanup-
design.md 참고), 한쪽만 바뀌면 이 테스트가 실패해 드리프트를 잡는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from engine.regime_ml_constants import TRAINING_MARKETS

_FRONTEND_FILE = (
    Path(__file__).parent.parent
    / "frontend" / "components" / "RegimeMlCurrentPrediction.tsx"
)
_ARRAY_PATTERN = re.compile(r"TRAINED_MARKETS\s*=\s*\[([^\]]*)\]")
_QUOTED_STRING_PATTERN = re.compile(r"['\"]([^'\"]*)['\"]")


def _extract_frontend_markets() -> list[str]:
    content = _FRONTEND_FILE.read_text(encoding="utf-8")
    match = _ARRAY_PATTERN.search(content)
    assert match is not None, (
        f"{_FRONTEND_FILE}에서 TRAINED_MARKETS 배열을 찾지 못했습니다 — "
        "파일 구조가 바뀌었으면 이 테스트의 정규식도 갱신하세요."
    )
    return _QUOTED_STRING_PATTERN.findall(match.group(1))


def test_frontend_trained_markets_matches_backend_training_markets():
    frontend_markets = _extract_frontend_markets()

    assert sorted(frontend_markets) == sorted(TRAINING_MARKETS)
