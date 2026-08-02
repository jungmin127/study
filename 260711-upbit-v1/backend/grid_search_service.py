"""
backend/grid_search_service.py

scripts/grid_search.py를 서브프로세스로 실행하고 진행률/결과를 engine.cache의
grid_search_jobs 테이블에 기록하는 오케스트레이션 레이어. 스크립트 자체는 수정하지
않고, 이미 stdout에 찍고 있는 진행률 로그와 RESULT_JSON을 그대로 파싱 대상으로 삼는다.
"""
from __future__ import annotations

import json
import re

_PROGRESS_RE = re.compile(r"완료\s+([\d,]+)/([\d,]+)건")
_TOTAL_COMBOS_RE = re.compile(r"총\s+([\d,]+)개\s+조합")
_RESULT_JSON_PREFIX = "RESULT_JSON: "


def _parse_progress_line(line: str) -> tuple[int, int] | None:
    """"완료 1,005/20,700건 (4.9%)" 같은 줄에서 (완료 개수, 전체 개수)를 뽑는다.
    매치되지 않으면 None."""
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    done = int(match.group(1).replace(",", ""))
    total = int(match.group(2).replace(",", ""))
    return done, total


def _parse_total_combos_line(line: str) -> int | None:
    """"[2] 매수 조건 138개 x 매도 조건 150개 = 총 20,700개 조합" 같은 줄에서
    전체 조합 수를 뽑는다. 첫 진행률 로그(약 1~1.5분 후)보다 먼저 total_combos를 알 수
    있어 프론트 진행률 바 분모를 더 빨리 채울 수 있다."""
    match = _TOTAL_COMBOS_RE.search(line)
    return int(match.group(1).replace(",", "")) if match else None


def _parse_result_json_line(line: str) -> dict | None:
    """"RESULT_JSON: {...}" 줄에서 JSON payload를 파싱한다. 접두어가 없으면 None."""
    stripped = line.strip()
    if not stripped.startswith(_RESULT_JSON_PREFIX):
        return None
    return json.loads(stripped[len(_RESULT_JSON_PREFIX):])
