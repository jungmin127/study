# Grid Search 웹 탭 마이너 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/plans_v1/2026-08-03-grid-search-web-tab.md`의 최종 전체 브랜치 리뷰에서
정확성에는 영향 없다고 미뤄뒀던 마이너 9건을 정리한다.

**Architecture:** 9건 모두 서로 독립적인 파일/영역의 정리 작업이라 새 아키텍처 결정은 없다.
기존 grid search 웹 탭 기능(백엔드 `backend/grid_search_service.py`+`backend/main.py`, 프론트
`frontend/components/GridSearch*.tsx`, `.claude/skills/grid-search/SKILL.md`)의 코드/문서 품질만
개선한다.

**Tech Stack:** 기존과 동일 — Python 3.11.9 표준 라이브러리 + pytest, Next.js 14(App Router) +
TypeScript, 새 의존성 없음.

## Global Constraints

- 이 9건은 전부 "정확성에 영향 없음"으로 분류된 마이너 항목이다 — 동작을 바꾸지 않는 리팩터/정리가
  원칙이되, 리뷰에서 실제로 옳은 동작으로 지적된 항목(타임프레임/날짜 서버측 검증 추가, cancel/complete
  레이스 좁히기)은 실질적인 동작 변화를 포함한다.
- 각 태스크는 서로 다른 파일/관심사를 다루므로 순서 의존성이 없다 — 병렬로 진행해도 되지만, 같은
  파일을 건드리는 태스크 쌍은 순차 실행을 권장한다(머지 충돌 방지): `backend/grid_search_service.py`를
  건드리는 Task 1과 Task 9, `frontend/lib/format.ts`를 건드리는 Task 4와 Task 6.
- 프론트엔드 검증은 `npx tsc --noEmit`과 `npx eslint <파일>`만 사용한다. **`npm run build`는 쓰지
  않는다** — 이 저장소는 `npm run dev`가 이미 떠 있는 상태에서 `npm run build`를 돌리면 `.next`가
  깨지는 알려진 문제가 있다(memory: upbit-frontend-tailwind-opacity-gotcha).
- 백엔드 변경 후 실제로 브라우저에서 확인하려면 `uvicorn`을 재시작해야 한다(`--reload`가 이
  저장소에서 종종 파일 변경을 놓치는 문제가 있음, 알려진 반복 이슈).
- 전체 정리 후 `pytest`(현재 기준 304 passed)와 `npx tsc --noEmit`이 모두 깨끗해야 완료로 간주한다.

---

### Task 1: `grid_search_service.py` import를 파일 상단으로 정리

**Files:**
- Modify: `backend/grid_search_service.py:8-51`

**Interfaces:**
- Consumes: 없음(순수 코드 이동, 공개 인터페이스 불변)
- Produces: 없음

**배경:** 현재 `os`/`signal`/`subprocess`/`sys`/`threading`/`uuid`/`Path` import가 정규식 헬퍼
함수들(`_parse_progress_line` 등) 뒤, 파일 중간(45번째 줄)에 있다 — PEP8 E402(모듈 레벨 import가
파일 최상단에 있지 않음) 위반. 동작에는 영향 없는 순수 스타일 이슈.

- [x] **Step 1: import를 파일 최상단, 기존 `import json`/`import re` 바로 아래로 옮긴다**

`backend/grid_search_service.py` 8~57번째 줄을 아래로 교체한다(즉, `from __future__ import
annotations` 다음에 모든 import를 모으고, 정규식 헬퍼 함수들은 그 뒤로 민다):

```python
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from engine.cache import (
    create_grid_search_job,
    finish_grid_search_job,
    update_grid_search_job_progress,
)

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


REPO_ROOT = Path(__file__).resolve().parent.parent
```

이후 원래 59번째 줄(`REPO_ROOT = Path(...)`)부터 이어지던 나머지 코드(`class
JobAlreadyRunningError` 이하)는 그대로 두되, 파일 안에 `REPO_ROOT` 정의가 중복되지 않도록
원래 위치(59번째 줄)의 `REPO_ROOT = Path(__file__).resolve().parent.parent`와
`class JobAlreadyRunningError` 사이 빈 줄 하나만 남기고, 위에서 옮긴 `REPO_ROOT` 정의는
위 블록에 있는 것 하나만 남긴다(즉 파일 전체에 `REPO_ROOT` 정의가 정확히 한 번만 존재해야
한다).

- [x] **Step 2: `python -m py_compile`로 문법 확인**

Run: `python -m py_compile backend/grid_search_service.py`
Expected: 에러 없이 종료(exit code 0)

- [x] **Step 3: 관련 테스트 실행**

Run: `python -m pytest tests/test_grid_search_service.py -v`
Expected: 기존 테스트 전부 PASS (동작 변경 없는 순수 이동이므로 실패하면 이동 중 실수)

- [x] **Step 4: 커밋**

```bash
git add backend/grid_search_service.py
git commit -m "style: grid_search_service.py의 import를 파일 상단으로 정리"
```

---

### Task 2: `_FakePopen` 테스트 더블의 클래스 상태를 매 테스트마다 초기화

**Files:**
- Modify: `tests/test_grid_search_service.py:66-93`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(테스트 인프라만 변경)

**배경:** `_FakePopen.stdout_lines`/`_FakePopen.returncode`는 클래스 속성이고, 각 테스트가
`_FakePopen.stdout_lines = [...]`/`_FakePopen.returncode = ...`로 직접 덮어쓴다. 지금은 모든
테스트가 두 값을 빠짐없이 설정해서 실제로 깨지진 않지만, 나중에 둘 중 하나라도 빼먹은 새
테스트가 추가되면 실행 순서에 따라 이전 테스트가 남긴 값을 조용히 물려받는다(order-coupled
버그). autouse fixture에서 매번 기본값으로 리셋해 이 위험을 없앤다.

- [x] **Step 1: `_reset_grid_search_service_state` fixture에 `_FakePopen` 리셋 추가**

`tests/test_grid_search_service.py:88-93`을 아래로 교체:

```python
@pytest.fixture(autouse=True)
def _reset_grid_search_service_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(gss, "_active", None)
    monkeypatch.setattr(gss.threading, "Thread", _SyncThread)
    monkeypatch.setattr(gss.subprocess, "Popen", _FakePopen)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0
```

- [x] **Step 2: 전체 grid search 테스트 재실행**

Run: `python -m pytest tests/test_grid_search_service.py -v`
Expected: 기존 테스트 전부 PASS (각 테스트가 이미 필요한 값을 명시적으로 설정하므로 동작
변화 없음)

- [x] **Step 3: 커밋**

```bash
git add tests/test_grid_search_service.py
git commit -m "test: _FakePopen 클래스 상태를 매 테스트마다 리셋해 순서 결합 위험 제거"
```

---

### Task 3: grid search 요청의 timeframe/date 형식을 서버측에서 검증

**Files:**
- Modify: `engine/metrics.py:16-25`
- Modify: `backend/main.py:9-11`, `backend/main.py:872-883`
- Test: `tests/test_backend.py` (새 테스트 2개 추가, 기존 `_client`/`_run_request` 패턴 재사용)

**Interfaces:**
- Consumes: `engine.metrics._TIMEFRAME_MINUTES`(기존 dict, 8개 timeframe 코드가 key)
- Produces: `engine.metrics.VALID_TIMEFRAMES: frozenset[str]` — Task 4의 프론트 매핑과는 별개(백엔드
  전용), 다른 곳에서 timeframe 검증이 필요하면 이 심볼을 재사용한다.

**배경:** `_validate_grid_search_request`는 지금 `market`/`capital`/`top_n`/날짜 순서만 검증하고,
`timeframe`이 실제로 지원되는 8종 코드인지, `start`/`end`가 `YYYY-MM-DD` 형식인지는 검증하지
않는다. 잘못된 값이 그대로 `scripts/grid_search.py` 서브프로세스로 넘어가 늦게(서브프로세스
실행 후) 실패한다 — 200번대 응답 후 job이 "failed"로 끝나는 대신, API 요청 시점에 400으로
바로 거부하는 게 사용자 경험상 낫다.

- [x] **Step 1: 실패하는 테스트 먼저 작성**

`tests/test_backend.py`의 `test_create_grid_search_job_rejects_top_n_out_of_range`
(1286번째 줄 부근) 바로 아래에 추가:

```python
def test_create_grid_search_job_rejects_unsupported_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes999", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_rejects_malformed_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026/06/05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 400
```

- [x] **Step 2: 실행해서 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "unsupported_timeframe or malformed_date" -v`
Expected: 둘 다 FAIL (현재는 timeframe/date 형식을 검증하지 않으므로 200이 돌아온다)

- [x] **Step 3: `engine/metrics.py`에 `VALID_TIMEFRAMES` 추가**

`engine/metrics.py:16-25`를 아래로 교체(기존 `_TIMEFRAME_MINUTES` 바로 아래 한 줄 추가):

```python
# 타임프레임별 1 bar당 분(minute) 수
_TIMEFRAME_MINUTES: dict[str, float] = {
    "minutes1": 1,
    "minutes3": 3,
    "minutes5": 5,
    "minutes15": 15,
    "minutes30": 30,
    "minutes60": 60,
    "minutes240": 240,
    "days": 1440,
}

VALID_TIMEFRAMES: frozenset[str] = frozenset(_TIMEFRAME_MINUTES)
```

- [x] **Step 4: `backend/main.py`에 `VALID_TIMEFRAMES` import 추가**

`backend/main.py:51`(`from engine.metrics import calculate_metrics`)을 아래로 교체:

```python
from engine.metrics import VALID_TIMEFRAMES, calculate_metrics
```

- [x] **Step 5: `_validate_grid_search_request`에 timeframe/date 형식 검증 추가**

`backend/main.py:872-883`을 아래로 교체:

```python
def _validate_grid_search_request(req: GridSearchJobRequest) -> list[str]:
    errors: list[str] = []
    if req.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"지원하지 않는 봉데이터입니다: {req.timeframe}")
    for label, value in (("시작일", req.start), ("종료일", req.end)):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            errors.append(f"{label} 형식이 올바르지 않습니다(YYYY-MM-DD): {value}")
    if req.start >= req.end:
        errors.append("시작일은 종료일보다 빨라야 합니다.")
    if req.capital <= 0:
        errors.append("운용자금은 0보다 커야 합니다.")
    if not (1 <= req.top_n <= 50):
        errors.append("상위N개는 1~50 사이여야 합니다.")
    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")
    return errors
```

- [x] **Step 6: 실행해서 통과 확인**

Run: `python -m pytest tests/test_backend.py -k grid_search -v`
Expected: 새 테스트 2개 포함 전부 PASS

- [x] **Step 7: 전체 스위트 재실행**

Run: `python -m pytest`
Expected: 전부 PASS (기존 통과 개수 + 새 테스트 2개)

- [x] **Step 8: 커밋**

```bash
git add engine/metrics.py backend/main.py tests/test_backend.py
git commit -m "fix: grid search 요청의 timeframe/date 형식을 서버측에서 사전 검증"
```

---

### Task 4: 프론트 이력 카드에 원본 timeframe 코드 대신 한글 라벨 표시

**Files:**
- Modify: `frontend/lib/format.ts`
- Modify: `frontend/components/GridSearchHistory.tsx:9`, `:63`

**Interfaces:**
- Consumes: 없음
- Produces: `formatTimeframe(timeframe: string): string` (frontend/lib/format.ts) — `minutesN`/`days`
  코드를 `N분`/`1일` 한글 라벨로 변환. 다른 컴포넌트에서 timeframe을 표시할 일이 생기면 이 함수를
  재사용한다(각자 라벨 배열을 새로 만들지 않는다).

**배경:** `GridSearchHistory.tsx`가 `job.timeframe`을 그대로 렌더링해서 `minutes60` 같은 원본
코드가 사용자에게 노출된다. `GridSearchForm.tsx`의 `TIMEFRAME_OPTIONS`가 이미 갖고 있는
라벨(`1분`/`3분`/.../`1일`)을 공용 함수로 뽑아 재사용한다.

- [x] **Step 1: `frontend/lib/format.ts`에 `formatTimeframe` 추가**

`frontend/lib/format.ts`의 `TIMEFRAME_MINUTES` 정의(18~28번째 줄) 바로 아래에 추가:

```typescript
const TIMEFRAME_LABELS: Record<string, string> = {
  minutes1: '1분',
  minutes3: '3분',
  minutes5: '5분',
  minutes15: '15분',
  minutes30: '30분',
  minutes60: '1시간',
  minutes240: '4시간',
  days: '1일',
};

export function formatTimeframe(timeframe: string): string {
  return TIMEFRAME_LABELS[timeframe] ?? timeframe;
}
```

- [x] **Step 2: `GridSearchHistory.tsx`에서 사용**

`frontend/components/GridSearchHistory.tsx:8`(`import { formatDateTime } from
'@/lib/format';`)을 아래로 교체:

```typescript
import { formatDateTime, formatTimeframe } from '@/lib/format';
```

`frontend/components/GridSearchHistory.tsx:63`(`<span className="text-muted-foreground">{job.timeframe}</span>`)을 아래로 교체:

```typescript
                <span className="text-muted-foreground">{formatTimeframe(job.timeframe)}</span>
```

- [x] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 4: 커밋**

```bash
git add frontend/lib/format.ts frontend/components/GridSearchHistory.tsx
git commit -m "fix: grid search 이력에 원본 timeframe 코드 대신 한글 라벨 표시"
```

---

### Task 5: 사용하지 않는 `GET /api/v1/grid-search/jobs/{job_id}` 엔드포인트 제거

**Files:**
- Modify: `backend/main.py:918-923`
- Modify: `tests/test_backend.py` (해당 엔드포인트 전용 테스트 제거)

**Interfaces:**
- Consumes: 없음
- Produces: 없음(엔드포인트 삭제)

**배경:** `frontend/lib/api/eda.ts`는 `createGridSearchJob`/`getGridSearchJobs`/
`cancelGridSearchJob`만 호출하고, 단일 job 조회(`GET /jobs/{job_id}`)는 어디서도 호출하지
않는다. 목록(`GET /jobs`)이 이미 전체 이력을 3초 폴링으로 가져오므로 단일 조회가 필요한
화면도 없다. YAGNI — 죽은 엔드포인트를 지운다.

- [x] **Step 1: 엔드포인트 제거**

`backend/main.py:918-923`을 통째로 삭제(바로 위 `list_grid_search_jobs_endpoint`와 바로 아래
`cancel_grid_search_job_endpoint` 사이):

```python
@app.get("/api/v1/grid-search/jobs/{job_id}")
def get_grid_search_job_endpoint(job_id: str) -> dict:
    job = get_grid_search_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 job을 찾을 수 없습니다")
    return _grid_search_job_response(job)
```

삭제 후 `list_grid_search_jobs_endpoint`의 `return` 다음에 빈 줄 두 개, 바로
`cancel_grid_search_job_endpoint`가 오도록 정리한다. `get_grid_search_job`은
`create_grid_search_job_endpoint`(908번째 줄)에서 여전히 쓰이므로 `engine.cache` import에서
지우지 않는다.

- [x] **Step 2: 해당 엔드포인트 전용 테스트 제거**

`tests/test_backend.py:1313-1316`의 아래 테스트를 통째로 삭제:

```python
def test_get_grid_search_job_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/grid-search/jobs/does-not-exist")
    assert resp.status_code == 404
```

- [x] **Step 3: 전체 스위트 실행**

Run: `python -m pytest`
Expected: 이전보다 1개 적은 개수로 전부 PASS (다른 실패 없음)

- [x] **Step 4: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "refactor: 사용하지 않는 GET /grid-search/jobs/{job_id} 엔드포인트 제거"
```

---

### Task 6: `GridSearchForm`/`PortSetupForm`의 중복 헬퍼(`defaultDate`/`formatCapital`)를 공용화

**Files:**
- Modify: `frontend/lib/format.ts`
- Modify: `frontend/components/GridSearchForm.tsx:24-33`
- Modify: `frontend/components/PortSetupForm.tsx:34-43`

**Interfaces:**
- Consumes: 없음
- Produces: `defaultDate(daysAgo: number): string`, `formatCapital(digits: string): string`
  (frontend/lib/format.ts) — 이후 운용자금/날짜 입력 폼이 새로 생기면 이 두 함수를 재사용한다.

**배경:** `GridSearchForm.tsx`와 `PortSetupForm.tsx`가 완전히 동일한 `defaultDate`/
`formatCapital` 함수를 각자 파일에 중복 정의하고 있다. 로직이 갈라질 이유가 없으므로 하나로
합친다.

- [x] **Step 1: `frontend/lib/format.ts`에 두 함수 추가**

`frontend/lib/format.ts` 맨 아래(파일 끝)에 추가:

```typescript
export function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export function formatCapital(digits: string): string {
  if (!digits) return '';
  return Number(digits).toLocaleString('ko-KR');
}
```

- [x] **Step 2: `GridSearchForm.tsx`에서 로컬 정의 제거하고 import**

`frontend/components/GridSearchForm.tsx:9`(`import { SECTION_HEADER_CLASS } from
'@/lib/ui-classes';`)을 아래로 교체:

```typescript
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { defaultDate, formatCapital } from '@/lib/format';
```

`frontend/components/GridSearchForm.tsx:24-33`(로컬 `formatCapital`/`defaultDate` 정의 두 개)을
통째로 삭제한다.

- [x] **Step 3: `PortSetupForm.tsx`에서 로컬 정의 제거하고 import**

`frontend/components/PortSetupForm.tsx:23`(`import { SECTION_HEADER_CLASS } from
'@/lib/ui-classes';`)을 아래로 교체:

```typescript
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { defaultDate, formatCapital } from '@/lib/format';
```

`frontend/components/PortSetupForm.tsx:34-43`(로컬 `defaultDate`/`formatCapital` 정의 두 개)을
통째로 삭제한다.

- [x] **Step 4: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/GridSearchForm.tsx components/PortSetupForm.tsx lib/format.ts`
Expected: 에러 없음(미사용 import 경고가 뜨면 새로 추가한 import 줄을 다시 확인)

- [x] **Step 5: 커밋**

```bash
git add frontend/lib/format.ts frontend/components/GridSearchForm.tsx frontend/components/PortSetupForm.tsx
git commit -m "refactor: GridSearchForm/PortSetupForm의 defaultDate·formatCapital 중복 제거"
```

---

### Task 7: `grid-search` SKILL.md 소개 문단을 현재 동작(웹 탭 안내만)에 맞게 수정

**Files:**
- Modify: `.claude/skills/grid-search/SKILL.md:3`, `:8-11`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(문서만 수정)

**배경:** frontmatter `description`(3번째 줄)과 `# Grid Search` 아래 첫 문단(8~11번째 줄)이
"계산하고 ... 저장한다"처럼 이 스킬이 직접 실행/저장하는 것처럼 서술하는데, 파일 뒷부분
"주의 사항" 절(66~68번째 줄)은 이미 "더 이상 직접 실행하지 않는다"고 명시하고 있어 서로
모순된다. 도입부를 뒷부분과 일치하도록 고친다.

- [x] **Step 1: frontmatter description 수정**

`.claude/skills/grid-search/SKILL.md:3`을 아래로 교체:

```yaml
description: Parse a grid search request (coin/capital/timeframe/date-range/topN) and hand the user a prefilled link to the Grid Search web tab, which actually runs it. Trigger when the user sends a message starting with "grid search" followed by comma-separated 코인명,운용자금,봉데이터,운용기간,상위N개 (e.g. "grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20"). 업비트 백테스트 전략의 매수/매도 오실레이터 지표 조합 그리드서치 요청을 파싱해 웹 탭(/grid-search) 실행 링크로 안내할 때 사용합니다.
```

- [x] **Step 2: 본문 첫 문단 수정**

`.claude/skills/grid-search/SKILL.md:8-11`을 아래로 교체:

```markdown
`grid search` 명령을 받으면 오실레이터 9종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/BB_PERCENT_B/
MACD_PPO/MACD_PPO_signal/ATR_PCT — ATR_PCT만 매수·매도 양방향) + 매도전용 3종
(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(20,700개 조합) 요청을
파싱/검증하고, 실제 계산은 프론트엔드 "Grid Search" 탭(`/grid-search`)에서 사용자가 직접
실행하도록 프리필된 링크로 안내한다. 이 스킬 자신은 `scripts/grid_search.py`를 실행하지도,
결과를 저장하지도 않는다.
```

- [x] **Step 3: 파일 전체 재검토**

`.claude/skills/grid-search/SKILL.md`를 다시 읽어 "실행 절차"/"주의 사항" 절과 새 도입부가
서로 모순되지 않는지 확인한다(둘 다 "실행하지 않는다"는 동일한 사실을 말해야 한다).

- [x] **Step 4: 커밋**

```bash
git add .claude/skills/grid-search/SKILL.md
git commit -m "docs: grid-search SKILL.md 도입부가 웹 탭 안내 전용 동작과 모순되던 것 수정"
```

---

### Task 8: `GridSearchPage`의 폴링 `useEffect`가 매 폴링마다 재등록되는 것 방지

**Files:**
- Modify: `frontend/components/GridSearchPage.tsx:34-40`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(내부 동작 최적화, 외부 인터페이스 불변)

**배경:** `runningJob`은 매 3초 폴링(`refresh()`)마다 서버에서 새로 온 객체라 참조가 매번
바뀐다. `useEffect(() => {...}, [runningJob, refresh])`가 `runningJob` 객체 참조에 의존하므로,
job이 실행 중인 동안 매 폴링마다 `clearInterval` + `setInterval`을 반복해서 불필요하게
재등록한다(동작은 맞지만 낭비). `runningJob`이 있는지 여부(`Boolean`)에만 의존하도록 바꾼다.

- [x] **Step 1: 의존성을 `Boolean(runningJob)`으로 좁힌다**

`frontend/components/GridSearchPage.tsx:36-40`을 아래로 교체:

```typescript
  const isJobRunning = runningJob !== null;

  useEffect(() => {
    if (!isJobRunning) return;
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isJobRunning, refresh]);
```

(이 한 줄 `const isJobRunning = ...`을 34번째 줄의 `const runningJob = jobs.find(...)` 바로
아래에 추가하고, 기존 두 번째 `useEffect`를 위 코드로 교체한다.)

- [x] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 3: 실제 동작 확인**

`npm run dev`가 떠 있는 상태에서 `/grid-search`를 열고 브라우저 devtools의 React DevTools
Profiler 없이도 확인 가능한 수준으로: grid search를 하나 시작하고, 3초 폴링이 계속되며
진행률이 갱신되는지, 취소 버튼이 여전히 동작하는지 눈으로 확인한다(수동 확인, 자동화된
테스트는 없음 — 이 프로젝트에 프론트 유닛 테스트 러너가 없다).

- [x] **Step 4: 커밋**

```bash
git add frontend/components/GridSearchPage.tsx
git commit -m "perf: GridSearchPage 폴링 effect가 매 폴링마다 재등록되지 않도록 의존성 좁히기"
```

---

### Task 9: `_reader_loop`의 `_active` 초기화를 `finish_grid_search_job` 이후로 옮겨 cancel/complete 레이스 좁히기

**Files:**
- Modify: `backend/grid_search_service.py` (Task 1 완료 후 파일 구조 기준 `_reader_loop` 함수 본문)

**Interfaces:**
- Consumes: 없음
- Produces: 없음(내부 순서 변경, `_reader_loop`의 시그니처/반환값 불변)

**배경:** 지금 `_reader_loop`는 서브프로세스가 끝나자마자 `with _lock: _active = None`으로
슬롯을 먼저 비우고, 그 다음에 `finish_grid_search_job(...)`을 호출해 DB에 최종 상태(completed/
failed/canceled)를 기록한다. 그 사이 아주 짧은 시간(수 밀리초) 동안 `_active`는 이미 `None`이라
`start_job()`이 새 job을 받아줄 수 있는데, 방금 끝난 job의 DB 행은 아직 `running`으로 남아있다.
최종 리뷰에서 "sub-ms, harmless"로 분류됐지만, 순서를 바꾸는 것만으로 이 창을 완전히 없앨 수
있으므로 고친다: DB에 최종 상태를 먼저 기록한 뒤 `_active`를 비운다.

- [x] **Step 1: 순서를 어서션하는 테스트를 먼저 작성**

`tests/test_grid_search_service.py`에 아래 테스트를 추가(파일 끝에). 핵심 불변식:
`finish_grid_search_job`이 호출되는 시점에 `gss._active`가 아직 `None`으로 비워지기 전이어야
한다(=DB 기록이 슬롯 반환보다 먼저 끝난다):

```python
def test_reader_loop_finishes_db_row_before_clearing_active_slot(monkeypatch):
    active_at_finish_time: list[dict | None] = []

    original_finish = gss.finish_grid_search_job

    def _tracking_finish(job_id, **kwargs):
        active_at_finish_time.append(gss._active)
        return original_finish(job_id, **kwargs)

    monkeypatch.setattr(gss, "finish_grid_search_job", _tracking_finish)

    _FakePopen.stdout_lines = [
        "    완료 20,700/20,700건 (100.0%)\n",
        'RESULT_JSON: {"total_combos": 20700, "elapsed_sec": 1.0, "saved": []}\n',
    ]
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    assert len(active_at_finish_time) == 1
    assert active_at_finish_time[0] is not None
    assert active_at_finish_time[0]["job_id"] == job_id
    assert gss._active is None
```

- [x] **Step 2: 실행해서 실패 확인**

Run: `python -m pytest tests/test_grid_search_service.py::test_reader_loop_finishes_db_row_before_clearing_active_slot -v`
Expected: FAIL — 현재 코드는 `_active`를 먼저 비우므로 `finish_grid_search_job` 호출 시점엔
이미 `gss._active is None`, 즉 `assert active_at_finish_time[0] is not None`에서 실패한다.

- [x] **Step 3: `_reader_loop`의 마무리 블록 순서 변경**

Task 1 적용 후 `_reader_loop` 함수의 마지막 부분(원래 120~140번째 줄, 아래 원문)을:

```python
    global _active
    with _lock:
        _active = None

    if unexpected_error is not None:
        finish_grid_search_job(job_id, status="failed", error_message=unexpected_error)
    elif canceled.is_set():
        finish_grid_search_job(job_id, status="canceled")
    elif proc.returncode == 0 and result is not None:
        finish_grid_search_job(
            job_id,
            status="completed",
            elapsed_sec=result.get("elapsed_sec"),
            result_json=json.dumps(result.get("saved", [])),
        )
    else:
        message = (
            error_lines[-1] if error_lines
            else f"grid search가 종료 코드 {proc.returncode}로 실패했습니다."
        )
        finish_grid_search_job(job_id, status="failed", error_message=message)
```

아래로 교체(DB 기록을 먼저, `_active` 초기화를 나중으로):

```python
    if unexpected_error is not None:
        finish_grid_search_job(job_id, status="failed", error_message=unexpected_error)
    elif canceled.is_set():
        finish_grid_search_job(job_id, status="canceled")
    elif proc.returncode == 0 and result is not None:
        finish_grid_search_job(
            job_id,
            status="completed",
            elapsed_sec=result.get("elapsed_sec"),
            result_json=json.dumps(result.get("saved", [])),
        )
    else:
        message = (
            error_lines[-1] if error_lines
            else f"grid search가 종료 코드 {proc.returncode}로 실패했습니다."
        )
        finish_grid_search_job(job_id, status="failed", error_message=message)

    global _active
    with _lock:
        _active = None
```

- [x] **Step 4: 실행해서 통과 확인**

Run: `python -m pytest tests/test_grid_search_service.py::test_reader_loop_finishes_db_row_before_clearing_active_slot -v`
Expected: PASS

- [x] **Step 5: 전체 grid search 테스트 스위트 재실행**

Run: `python -m pytest tests/test_grid_search_service.py -v`
Expected: 전부 PASS

- [x] **Step 6: 전체 pytest 스위트 실행**

Run: `python -m pytest`
Expected: 전부 PASS

- [x] **Step 7: 커밋**

```bash
git add backend/grid_search_service.py tests/test_grid_search_service.py
git commit -m "fix: reader_loop가 DB에 최종 상태를 기록한 뒤에 _active 슬롯을 비우도록 순서 변경"
```

---

## 마무리 체크

- [x] `python -m pytest` 전체 그린 (9개 태스크 반영 후 총 개수는 304 - 1(Task 5 삭제) + 2(Task 3
  신규) + 1(Task 9 신규) = 306개 근방이어야 한다 — 정확한 숫자보다 "전부 PASS"가 기준)
- [x] `cd frontend && npx tsc --noEmit` 클린
- [x] 백엔드(`uvicorn`) 재시작 후 `/grid-search` 탭에서 정상 실행 1회 수동 확인(Task 3의 새
  검증이 정상 케이스를 막지 않는지, Task 4의 한글 라벨이 이력에 표시되는지 눈으로 확인)
