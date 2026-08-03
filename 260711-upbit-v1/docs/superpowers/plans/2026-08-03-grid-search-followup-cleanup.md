# Grid Search 후속 마이너 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/plans/2026-08-03-grid-search-minor-cleanup.md`의 최종 전체 브랜치
리뷰(및 memory `upbit-v1-grid-search-minor-cleanup`)에 "정확성엔 영향 없으니 후속 과제로"
로그만 해뒀던 8개 항목을 정리한다.

**Architecture:** 8건 모두 서로 다른 파일/관심사의 정리 작업이라 새 아키텍처 결정은 없다.
그리드서치 웹 탭(`backend/grid_search_service.py`+`backend/main.py`,
`frontend/components/GridSearch*.tsx`, `.claude/skills/grid-search/SKILL.md`)과, 그리드서치가
파생시킨 공용 유틸(`frontend/lib/format.ts`, `engine/metrics.VALID_TIMEFRAMES`)이 백테스트
실행 화면(`backend/main.py`의 `RunBacktestRequest`, `frontend/components/BacktestRunsTable.tsx`
등)까지 퍼진 곳의 코드/문서 품질만 개선한다.

**Tech Stack:** 기존과 동일 — Python 3.11.9 표준 라이브러리(`logging` 포함) + pytest, Next.js 14
(App Router) + TypeScript, 새 의존성 없음.

## Global Constraints

- 이 8건은 전부 "정확성엔 영향 없음/낮음"으로 분류됐던 후속 항목이다. 다만 Task 2(로깅 추가)와
  Task 3(날짜 비교 방식 변경)은 실질적인 동작 변화를 포함한다 — 나머지는 순수 리팩터/문서 수정.
- 각 태스크는 서로 다른 파일/관심사를 다루므로 순서 의존성이 없다 — 병렬로 진행해도 되지만, 같은
  파일을 건드리는 태스크 쌍은 순차 실행을 권장한다(머지 충돌 방지): `backend/main.py`와
  `tests/test_backend.py`를 건드리는 Task 3과 Task 6.
- 프론트엔드 검증은 `npx tsc --noEmit`과 `npx eslint <파일>`만 사용한다. **`npm run build`는 쓰지
  않는다** — 이 저장소는 `npm run dev`가 이미 떠 있는 상태에서 `npm run build`를 돌리면 `.next`가
  깨지는 알려진 문제가 있다(memory: upbit-frontend-tailwind-opacity-gotcha).
- 백엔드 변경 후 실제로 브라우저에서 확인하려면 `uvicorn`을 재시작해야 한다(`--reload`가 이
  저장소에서 종종 파일 변경을 놓치는 문제가 있음, 알려진 반복 이슈).
- 전체 정리 후 `pytest`(현재 기준 311 passed)와 `npx tsc --noEmit`이 모두 깨끗해야 완료로 간주한다.

---

### Task 1: 그리드서치 timeframe 라벨 테이블 3중 중복 제거

**Files:**
- Modify: `frontend/lib/format.ts`
- Modify: `frontend/components/GridSearchForm.tsx`

**Interfaces:**
- Consumes: 없음
- Produces: `format.ts`의 `export const TIMEFRAME_CODES: string[]` — `TIMEFRAME_LABELS`의 key를
  그대로 노출한 순서 보장 배열(`Object.keys`는 문자열 key의 삽입 순서를 보장). 이후 timeframe
  선택 UI가 필요한 곳은 이 배열 + 기존 `formatTimeframe()`을 조합해서 만든다(하드코딩된 배열을
  새로 만들지 않는다).

**배경:** 8개 timeframe 코드-한글라벨 쌍이 `frontend/lib/format.ts`의 `TIMEFRAME_LABELS`(비공개)와
`frontend/components/GridSearchForm.tsx`의 `TIMEFRAME_OPTIONS`(공개 배열 리터럴)에 각각 따로
하드코딩돼 있다. 지금은 두 곳이 동기화돼있지만, 나중에 한쪽만 고치면 조용히 어긋난다(예:
`minutes3`이 처음 추가됐을 때 실제로 한 파일에서 빠뜨렸던 전례가 있음). `TIMEFRAME_LABELS`를
단일 소스로 두고 `GridSearchForm.tsx`가 거기서 파생하도록 바꾼다.

- [x] **Step 1: `frontend/lib/format.ts`에 `TIMEFRAME_CODES` export 추가**

`frontend/lib/format.ts`의 `TIMEFRAME_LABELS` 정의 바로 아래(현재 `export function
formatTimeframe` 바로 위)에 추가:

```typescript
export const TIMEFRAME_CODES: string[] = Object.keys(TIMEFRAME_LABELS);
```

- [x] **Step 2: `GridSearchForm.tsx`가 `TIMEFRAME_CODES`+`formatTimeframe`에서 옵션을 파생하도록 변경**

`frontend/components/GridSearchForm.tsx`의 import 줄(`import { defaultDate, formatCapital }
from '@/lib/format';`)을 아래로 교체:

```typescript
import { defaultDate, formatCapital, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
```

같은 파일의 하드코딩된 배열 리터럴을 통째로 교체:

```typescript
const TIMEFRAME_OPTIONS = [
  { label: '1분', timeframe: 'minutes1' },
  { label: '3분', timeframe: 'minutes3' },
  { label: '5분', timeframe: 'minutes5' },
  { label: '15분', timeframe: 'minutes15' },
  { label: '30분', timeframe: 'minutes30' },
  { label: '1시간', timeframe: 'minutes60' },
  { label: '4시간', timeframe: 'minutes240' },
  { label: '1일', timeframe: 'days' },
];
```

아래로:

```typescript
const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));
```

`TIMEFRAME_OPTIONS`를 사용하는 나머지 JSX(버튼 렌더링 부분)는 그대로 둔다 — 배열의 각 원소
shape(`{ label, timeframe }`)이 동일하므로 코드 변경 불필요.

- [x] **Step 3: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/GridSearchForm.tsx lib/format.ts`
Expected: 에러 없음

- [x] **Step 4: 실제 렌더링 순서 확인**

`GridSearchForm.tsx`의 봉데이터 버튼 8개가 여전히 `1분/3분/5분/15분/30분/1시간/4시간/1일`
순서로 렌더링되는지 코드로 확인(`TIMEFRAME_LABELS`의 삽입 순서가 그대로이므로 `Object.keys`
결과도 동일 순서 — 실제 브라우저 재확인은 선택사항, `npm run dev`가 떠 있다면 `/grid-search`
방문해서 눈으로 확인해도 됨).

- [x] **Step 5: 커밋**

```bash
git add frontend/lib/format.ts frontend/components/GridSearchForm.tsx
git commit -m "refactor: GridSearchForm의 TIMEFRAME_OPTIONS를 format.ts의 TIMEFRAME_LABELS에서 파생하도록 변경"
```

---

### Task 2: `_reader_loop` 마무리 블록 정리 — `global` 위치 + 예외 로깅

**Files:**
- Modify: `backend/grid_search_service.py`
- Test: `tests/test_grid_search_service.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(내부 동작만 보강, `_reader_loop`의 시그니처/반환값 불변)

**배경:** 두 가지를 같은 함수의 같은 블록에서 함께 고친다.

1. **가독성:** `global _active`가 `finally:` 블록 안에 텍스트상 위치해 있다. Python에서
   `global` 선언은 어디에 써도 함수 전체에 적용되므로 문법적으로는 문제 없지만, `finally`
   블록에만 적용되는 것처럼 오독하기 쉽다. 함수 상단(다른 지역변수들 옆)으로 옮긴다.
2. **관찰 가능성:** `finish_grid_search_job(...)`이 예외를 던지면(예: SQLite 잠금 에러)
   `try/finally`가 `_active`는 반드시 비워주지만(이전 태스크에서 고침), 그 DB 행 자체는
   영원히 `status="running"`으로 남는다. 서버 재시작 시 `_fail_orphaned_grid_search_jobs`가
   결국 회수하므로 회귀는 아니지만, 지금은 이 실패가 Python의 기본 스레드 예외 출력(스택
   트레이스만 stderr에 찍힘) 외엔 아무 단서도 남기지 않는다. `job_id`를 포함한 명시적 에러
   로그를 남겨서 운영자가 재시작 없이도 무슨 job이 왜 막혔는지 알 수 있게 한다.

- [x] **Step 1: `logging` import 및 모듈 로거 추가**

`backend/grid_search_service.py`의 import 블록(`import json` ~ `from pathlib import Path`)을
아래로 교체(알파벳 순서 유지, `logging`을 `json`과 `os` 사이에 삽입):

```python
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
```

같은 파일의 `REPO_ROOT = Path(__file__).resolve().parent.parent` 바로 아래에 추가:

```python
_logger = logging.getLogger(__name__)
```

- [x] **Step 2: `_reader_loop`에서 `global _active`를 함수 상단으로 이동하고 예외 로깅 추가**

`_reader_loop` 함수 시작 부분(docstring 바로 아래)을:

```python
    error_lines: list[str] = []
    result: dict | None = None
    unexpected_error: str | None = None
```

아래로 교체:

```python
    global _active
    error_lines: list[str] = []
    result: dict | None = None
    unexpected_error: str | None = None
```

함수 마지막 블록(현재 아래 코드)을:

```python
    try:
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
    finally:
        global _active
        with _lock:
            _active = None
```

아래로 교체(`global _active` 제거 — 위 Step에서 함수 상단으로 옮겼으므로 여기선 중복 선언
불필요, 그리고 `except`로 로그 추가):

```python
    try:
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
    except Exception:
        _logger.exception(
            "grid search job %s의 최종 상태를 DB에 기록하지 못했습니다 — "
            "해당 job의 DB 행이 running 상태로 남아있을 수 있습니다 (재시작 시 자동 정리됨)",
            job_id,
        )
        raise
    finally:
        with _lock:
            _active = None
```

- [x] **Step 3: 기존 예외-안전성 테스트에 로그 검증 추가**

`tests/test_grid_search_service.py`의 `test_reader_loop_clears_active_even_when_finish_grid_search_job_raises`
(현재 아래 내용)를:

```python
def test_reader_loop_clears_active_even_when_finish_grid_search_job_raises(monkeypatch):
    def _raise_finish(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(gss, "finish_grid_search_job", _raise_finish)

    _FakePopen.stdout_lines = [
        "    완료 20,700/20,700건 (100.0%)\n",
        'RESULT_JSON: {"total_combos": 20700, "elapsed_sec": 1.0, "saved": []}\n',
    ]
    _FakePopen.returncode = 0

    with pytest.raises(RuntimeError):
        gss.start_job(
            market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
            start="2026-06-05", end="2026-08-03", top_n=20,
        )

    assert gss._active is None
```

아래로 교체(`caplog` 인자와 마지막 두 줄만 추가):

```python
def test_reader_loop_clears_active_even_when_finish_grid_search_job_raises(monkeypatch, caplog):
    def _raise_finish(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(gss, "finish_grid_search_job", _raise_finish)

    _FakePopen.stdout_lines = [
        "    완료 20,700/20,700건 (100.0%)\n",
        'RESULT_JSON: {"total_combos": 20700, "elapsed_sec": 1.0, "saved": []}\n',
    ]
    _FakePopen.returncode = 0

    with pytest.raises(RuntimeError):
        with caplog.at_level("ERROR", logger="backend.grid_search_service"):
            gss.start_job(
                market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
                start="2026-06-05", end="2026-08-03", top_n=20,
            )

    assert gss._active is None
    assert any("최종 상태를 DB에 기록하지 못했습니다" in record.message for record in caplog.records)
```

- [x] **Step 4: 실행해서 통과 확인**

Run: `python -m pytest tests/test_grid_search_service.py -v`
Expected: 전부 PASS (특히 방금 수정한 테스트)

- [x] **Step 5: 전체 스위트 재실행**

Run: `python -m pytest -q`
Expected: 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add backend/grid_search_service.py tests/test_grid_search_service.py
git commit -m "refactor: _reader_loop의 global 선언 위치 정리 + finish_grid_search_job 예외 시 명시적 로그 추가"
```

---

### Task 3: grid search 날짜 비교를 파싱된 date 객체 기준으로 강화 + 관련 테스트 정밀화

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(내부 로직만 보강)

**배경:** `_validate_grid_search_request`의 `req.start >= req.end` 체크가 여전히 원본 문자열을
그대로 비교한다. 같은 함수가 한 줄 위에서 `datetime.strptime`으로 형식을 검증하는데도 그
결과를 버리고 있다. `<input type="date">`는 항상 0-패딩된 값(`2026-06-05`)을 보내므로 실제
UI로는 이 버그가 안 뚫리지만, `.claude/skills/grid-search/SKILL.md`가 사용자에게 안내하는
수동 URL(`?start=...&end=...`)엔 그런 보장이 없다 — 예를 들어 `start=2026-6-5,
end=2026-12-01`처럼 0-패딩 없는 값을 넣으면, `"2026-6-5" >= "2026-12-01"`가 문자열
비교에서는 `True`(`'6' > '1'`)라 실제로는 순서가 맞는 날짜 범위인데도 "시작일은 종료일보다
빨라야 합니다" 에러로 잘못 거부된다. 이미 파싱해둔 `datetime` 객체를 재사용해 비교하도록
고친다.

이 태스크는 또한 이전 플랜(`2026-08-03-grid-search-minor-cleanup.md`) Task 3의 리뷰가
지적했던 "새 400 테스트 2개가 status_code만 확인하고 어떤 규칙이 발동했는지는 안 본다"는
점도 함께 강화한다.

- [x] **Step 1: 실패하는 테스트 먼저 작성 (0-패딩 없는 유효한 날짜 범위 오탐 재현)**

`tests/test_backend.py`의 `test_create_grid_search_job_rejects_malformed_date` 바로 아래에
추가:

```python
def test_create_grid_search_job_accepts_non_zero_padded_valid_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])
    monkeypatch.setattr(backend_module, "start_job", lambda **kwargs: "job-1")

    from engine.cache import create_grid_search_job
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-6-5", end="2026-12-01", top_n=20,
    )

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-6-5", "end": "2026-12-01", "top_n": 20,
    })
    assert resp.status_code == 200
```

- [x] **Step 2: 실행해서 실패 확인**

Run: `python -m pytest tests/test_backend.py -k non_zero_padded -v`
Expected: FAIL (현재는 `"2026-6-5" >= "2026-12-01"`가 문자열 비교로 `True`라 400이 돌아온다 —
`resp.status_code == 200` 어서션이 깨짐)

- [x] **Step 3: `_validate_grid_search_request`가 파싱된 date 객체를 비교하도록 변경**

`backend/main.py`의 `_validate_grid_search_request`(현재 아래 내용)를:

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

아래로 교체:

```python
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
```

주의: 둘 중 하나라도 형식 파싱에 실패하면(`parsed_dates`에 해당 key가 없으면) 순서 비교
자체를 건너뛴다 — 형식 에러 하나만 보고하고, 파싱되지 않은 값으로 순서를 비교해 의미 없는
추가 에러를 만들지 않는다. 기존 `test_create_grid_search_job_rejects_malformed_date`
테스트(`start`는 유효, `end`만 깨짐)는 이 변경 후에도 여전히 형식 에러 하나만으로 400을
받는다 — 동작 변화 없음.

- [x] **Step 4: 실행해서 새 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k non_zero_padded -v`
Expected: PASS

- [x] **Step 5: 기존 두 테스트가 detail 메시지 내용까지 확인하도록 강화**

`tests/test_backend.py`의 `test_create_grid_search_job_rejects_unsupported_timeframe`(현재
`assert resp.status_code == 400`로 끝남)을 아래로 교체:

```python
def test_create_grid_search_job_rejects_unsupported_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes999", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 400
    assert "지원하지 않는 봉데이터" in resp.json()["detail"]
```

같은 파일의 `test_create_grid_search_job_rejects_malformed_date`를 아래로 교체:

```python
def test_create_grid_search_job_rejects_malformed_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026/08/03", "top_n": 20,
    })
    assert resp.status_code == 400
    assert "형식이 올바르지 않습니다" in resp.json()["detail"]
```

- [x] **Step 6: 전체 grid-search 관련 테스트 실행**

Run: `python -m pytest tests/test_backend.py -k grid_search -v`
Expected: 전부 PASS (새 테스트 1개 + 강화된 기존 테스트 2개 포함)

- [x] **Step 7: 전체 스위트 재실행**

Run: `python -m pytest -q`
Expected: 전부 PASS

- [x] **Step 8: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "fix: grid search 날짜 순서 검증이 문자열이 아닌 파싱된 date를 비교하도록 수정"
```

---

### Task 4: grid-search SKILL.md "파싱 규칙" 절의 옛 실행 문구 수정

**Files:**
- Modify: `.claude/skills/grid-search/SKILL.md`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(문서만 수정)

**배경:** 이전 플랜(Task 7)에서 이 파일의 frontmatter description과 도입부 문단은 "더 이상
직접 실행하지 않는다"로 이미 고쳤는데, "파싱 규칙" 절 바로 아래 한 줄이 옛 문구를 그대로
갖고 있다: "코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면,
**스크립트를 실행하지 말고** 사용자에게 되물어라." 이 스킬은 스크립트를 아예 실행하지
않으므로("실행하지 말고"라는 조건부 표현 자체가 성립하지 않음), 몇 줄 아래 "실행 절차"
섹션의 이미 고쳐진 표현("실행 안내 없이 사용자에게 되물어라")과 통일한다.

- [x] **Step 1: 문구 수정**

`.claude/skills/grid-search/SKILL.md`의 아래 문장(현재 35~36번째 줄 부근, "## 파싱 규칙" 표
바로 아래)을:

```markdown
코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면, 스크립트를
실행하지 말고 사용자에게 되물어라. 부분 입력으로 임의 진행하지 마라.
```

아래로 교체:

```markdown
코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면, 실행 안내
없이 사용자에게 되물어라. 부분 입력으로 임의 진행하지 마라.
```

- [x] **Step 2: 파일 전체 재검토**

파일을 다시 읽어 "파싱 규칙"/"실행 절차"/"주의 사항" 세 곳이 모두 동일하게 "이 스킬은
스크립트를 실행하지 않고, 파싱/검증 후 웹 탭으로 안내만 한다"는 사실을 말하는지 확인한다.

- [x] **Step 3: 커밋**

```bash
git add .claude/skills/grid-search/SKILL.md
git commit -m "docs: grid-search SKILL.md 파싱 규칙 절에 남아있던 옛 실행 문구 수정"
```

---

### Task 5: 4개 프론트 화면에 `formatTimeframe` 적용

**Files:**
- Modify: `frontend/components/BacktestRunsTable.tsx`
- Modify: `frontend/app/backtests/[runId]/page.tsx`
- Modify: `frontend/app/heatmap/page.tsx`
- Modify: `frontend/app/ranking/page.tsx`

**Interfaces:**
- Consumes: `formatTimeframe(timeframe: string): string` (이미 `frontend/lib/format.ts`에 존재,
  export 됨)
- Produces: 없음

**배경:** 그리드서치 이력/진행 카드는 이미 `formatTimeframe()`으로 한글 라벨을 보여주는데,
같은 유틸이 생긴 이후에도 백테스트 관련 화면 4곳은 여전히 raw timeframe 코드(`minutes60`
등)를 그대로 렌더링한다. 각 파일에서 **표시 전용** 자리만 고친다 — timeframe이 실제 로직
(URL 쿼리파라미터 생성, 차트 컴포넌트에 넘기는 prop, `formatHoldingPeriod` 같은 계산 함수의
입력)으로 쓰이는 자리는 원본 코드가 필요하므로 건드리지 않는다.

- [x] **Step 1: `BacktestRunsTable.tsx`의 표시 셀만 수정**

`frontend/components/BacktestRunsTable.tsx`의 `import { formatDateTime } from '@/lib/format';`를
아래로 교체:

```typescript
import { formatDateTime, formatTimeframe } from '@/lib/format';
```

같은 파일의 `<TableCell>{run.timeframe}</TableCell>`(테이블 행 렌더링 부분, `buildCopyHref`
함수 안의 `timeframe: run.timeframe`과는 다른 자리 — 그건 URL 파라미터라 손대지 않는다)을:

```typescript
              <TableCell>{formatTimeframe(run.timeframe)}</TableCell>
```

- [x] **Step 2: `backtests/[runId]/page.tsx`의 표시 텍스트만 수정**

`frontend/app/backtests/[runId]/page.tsx`의 `import { formatDateTime, formatHoldingPeriod }
from '@/lib/format';`를 아래로 교체:

```typescript
import { formatDateTime, formatHoldingPeriod, formatTimeframe } from '@/lib/format';
```

같은 파일의 아래 줄(상세 페이지 상단 요약 텍스트)을:

```tsx
          {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
```

아래로 교체:

```tsx
          {detail.market} · {formatTimeframe(detail.timeframe)} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
```

`<PriceChart ... timeframe={detail.timeframe} .../>`(차트 컴포넌트 prop)와
`formatHoldingPeriod(t.holdingPeriod, detail.timeframe)`(계산 함수 입력) 두 자리는 원본
코드가 필요하므로 그대로 둔다.

- [x] **Step 3: `heatmap/page.tsx`의 표시 셀만 수정**

`frontend/app/heatmap/page.tsx`의 `import { returnRateColor } from '@/lib/return-rate-color';`
바로 아래에 추가:

```typescript
import { formatTimeframe } from '@/lib/format';
```

같은 파일의 `<TableCell>{row.timeframe}</TableCell>`을:

```tsx
                  <TableCell>{formatTimeframe(row.timeframe)}</TableCell>
```

`key={...row.timeframe}`(React key)는 원본 코드로 두어도 무방하므로 그대로 둔다.

- [x] **Step 4: `ranking/page.tsx`의 표시 텍스트만 수정**

`frontend/app/ranking/page.tsx`의 `import { returnRateColor } from '@/lib/return-rate-color';`
바로 아래에 추가:

```typescript
import { formatTimeframe } from '@/lib/format';
```

같은 파일의 아래 줄을:

```tsx
                    #{i + 1} {row.market} · {row.timeframe}
```

아래로 교체:

```tsx
                    #{i + 1} {row.market} · {formatTimeframe(row.timeframe)}
```

`key={...row.timeframe}`(React key)는 그대로 둔다.

- [x] **Step 5: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/BacktestRunsTable.tsx "app/backtests/[runId]/page.tsx" app/heatmap/page.tsx app/ranking/page.tsx`
Expected: 에러 없음

- [x] **Step 6: 커밋**

```bash
git add frontend/components/BacktestRunsTable.tsx "frontend/app/backtests/[runId]/page.tsx" frontend/app/heatmap/page.tsx frontend/app/ranking/page.tsx
git commit -m "fix: 백테스트 관련 4개 화면에도 원본 timeframe 코드 대신 한글 라벨 표시"
```

---

### Task 6: `RunBacktestRequest`(백테스트 실행)에도 timeframe 서버측 검증 추가

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.metrics.VALID_TIMEFRAMES`(이미 존재, `backend/main.py`가 이미 import함)
- Produces: 없음

**배경:** 그리드서치 요청(`GridSearchJobRequest`)은 timeframe이 `VALID_TIMEFRAMES`에 속하는지
검증하지만, 같은 파일의 `RunBacktestRequest`(`/api/v1/backtests/run`,
`/api/v1/backtests/validate`가 공유하는 `_validate_backtest_request`)는 여전히 timeframe을
전혀 검증하지 않는다. 잘못된 timeframe이 그대로 `get_candles`까지 흘러가 늦게 실패한다 —
그리드서치 Task 3와 같은 종류의 늦은 실패 문제.

- [x] **Step 1: 실패하는 테스트 먼저 작성**

`tests/test_backend.py`의 `test_run_backtest_rejects_empty_buy_conditions` 바로 아래에 추가:

```python
def test_run_backtest_rejects_unsupported_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(timeframe="minutes999"),
    )
    assert resp.status_code == 400
    assert "지원하지 않는 봉데이터" in resp.json()["detail"]
```

- [x] **Step 2: 실행해서 실패 확인**

Run: `python -m pytest tests/test_backend.py -k rejects_unsupported_timeframe -v`
Expected: 두 개 매치(그리드서치 것 + 방금 추가한 것) 중 새 테스트가 FAIL — 지금은
`RunBacktestRequest`의 timeframe이 검증되지 않아 다음 단계(캔들 조회)까지 진행하다가 다른
사유로 실패하거나 200을 반환할 수 있음(캔들 조회를 목킹하지 않았으므로 실제로는 timeframe이
`get_candles`에 전달되는 시점에 따라 결과가 다를 수 있음 — 정확한 실패 모습을 관찰하고
보고서에 기록한다).

- [x] **Step 3: `_validate_backtest_request`에 timeframe 검증 추가**

`backend/main.py`의 `_validate_backtest_request` 시작 부분(현재 아래 내용)을:

```python
def _validate_backtest_request(req: RunBacktestRequest) -> list[str]:
    """구조적 검증 + 시장 데이터 기반 검증을 모두 수행해 오류 사유 목록을 반환.
    Task 7의 /validate 엔드포인트와 이 함수를 공유한다."""
    errors: list[str] = []

    buy_dict = req.buy_conditions.model_dump()
```

아래로 교체:

```python
def _validate_backtest_request(req: RunBacktestRequest) -> list[str]:
    """구조적 검증 + 시장 데이터 기반 검증을 모두 수행해 오류 사유 목록을 반환.
    Task 7의 /validate 엔드포인트와 이 함수를 공유한다."""
    errors: list[str] = []

    if req.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"지원하지 않는 봉데이터입니다: {req.timeframe}")

    buy_dict = req.buy_conditions.model_dump()
```

(`VALID_TIMEFRAMES`는 `backend/main.py`가 이미 `from engine.metrics import VALID_TIMEFRAMES,
calculate_metrics`로 import하고 있으므로 추가 import 불필요.)

- [x] **Step 4: 실행해서 통과 확인**

Run: `python -m pytest tests/test_backend.py -k rejects_unsupported_timeframe -v`
Expected: 두 테스트 모두 PASS

- [x] **Step 5: 기존 백테스트 관련 테스트가 깨지지 않았는지 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS (특히 `_run_request()`의 기본 `timeframe="days"`를 쓰는 기존 테스트들이
새 검증 때문에 깨지지 않아야 함 — `"days"`는 `VALID_TIMEFRAMES`에 포함되므로 통과해야 정상)

- [x] **Step 6: 전체 스위트 재실행**

Run: `python -m pytest -q`
Expected: 전부 PASS

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "fix: 백테스트 실행 요청(RunBacktestRequest)에도 timeframe 서버측 검증 추가"
```

---

### Task 7: `RunBacktestRequest` 날짜 검증도 파싱된 date 객체 기준으로 수정 (Task 3와 동일한 버그의 쌍둥이)

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(내부 로직만 보강)

**배경:** 이 플랜의 최종 전체 브랜치 리뷰(opus)가 계획 범위 밖에서 발견한 실제 버그. Task 3가
`_validate_grid_search_request`의 `req.start >= req.end` 문자열 비교를 파싱된 `datetime`
비교로 고쳤는데, 바로 200줄 아래 `_validate_backtest_request`(Task 6이 이번에 timeframe
검증을 추가한 바로 그 함수)는 여전히 같은 문자열 비교 버그를 그대로 갖고 있다. 게다가 이
함수는 날짜 **형식** 검증 자체가 아예 없다 — `req.start`/`req.end`가 나중에
`run_backtest_endpoint`(853번째 줄 부근)에서 무방비 `datetime.strptime(req.start,
"%Y-%m-%d")`로 다시 파싱되는데, 형식이 잘못된 값은 여기서 처리되지 않은 `ValueError`로
500을 낸다. 리뷰가 실측으로 확인한 두 가지 증상:
- `start="01-01-2026"`(형식 오류) → 지금은 문자열 비교로 순서 검증을 통과해버리고, 이후
  `datetime.strptime`에서 처리되지 않은 예외로 500이 난다.
- `start="2026-6-5", end="2026-12-01"`(0-패딩 없는 유효한 범위) → `"2026-6-5" >=
  "2026-12-01"`가 문자열 비교로 `True`라 "시작일은 종료일보다 빨라야 합니다"로 잘못
  거부된다(Task 3가 그리드서치 쪽에서 고친 것과 정확히 같은 버그).

Task 3와 완전히 같은 패턴(파싱된 날짜를 저장해두고, 둘 다 파싱된 경우에만 순서 비교)을
`_validate_backtest_request`에도 적용한다.

- [x] **Step 1: 실패하는 테스트 먼저 작성 (형식 오류 500 재현)**

`tests/test_backend.py`의 `test_run_backtest_rejects_unsupported_timeframe`(Task 6에서 추가한
테스트) 바로 아래에 추가:

```python
def test_run_backtest_rejects_malformed_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(start="01-01-2026"),
    )
    assert resp.status_code == 400
    assert "형식이 올바르지 않습니다" in resp.json()["detail"]


def test_run_backtest_accepts_non_zero_padded_valid_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(start="2026-6-5", end="2026-12-01"),
    )
    assert resp.status_code == 200
```

- [x] **Step 2: 실행해서 실패 확인**

Run: `python -m pytest tests/test_backend.py -k "rejects_malformed_date or accepts_non_zero_padded" -v`
Expected: `test_run_backtest_rejects_malformed_date`는 500을 받아 FAIL(`assert 500 == 400`),
`test_run_backtest_accepts_non_zero_padded_valid_date_range`는 400을 받아 FAIL(`assert 400 ==
200`) — 실제 관찰된 실패를 보고서에 정확히 기록한다(추측하지 말고 실제 응답을 확인).

- [x] **Step 3: `_validate_backtest_request`가 파싱된 date를 비교하도록 변경**

`backend/main.py`의 `_validate_backtest_request`(현재 아래 내용)를:

```python
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

    if req.start >= req.end:
        errors.append("시작일은 종료일보다 빨라야 합니다.")

    if req.initial_capital <= 0:
        errors.append("운용자금은 0보다 커야 합니다.")

    krw_markets = {m["market"] for m in get_krw_markets()}
    if req.market not in krw_markets:
        errors.append(f"{req.market}은(는) 업비트 KRW 마켓 목록에 없습니다.")

    return errors
```

아래로 교체(`buy_dict`/`sell_dict`/조건 검증 블록은 그대로 두고, 날짜 검증 부분만 교체):

```python
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
```

- [x] **Step 4: 실행해서 통과 확인**

Run: `python -m pytest tests/test_backend.py -k "rejects_malformed_date or accepts_non_zero_padded" -v`
Expected: 둘 다 PASS. (`test_create_grid_search_job_rejects_malformed_date`처럼 이름이 비슷한
그리드서치 테스트도 같이 매치될 수 있으니, 백테스트 쪽 두 테스트가 확실히 포함/통과했는지
출력을 확인한다.)

- [x] **Step 5: 기존 백테스트 테스트가 깨지지 않았는지 확인**

Run: `python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS — 특히 `_run_request()`의 기본값(`start="2026-01-01",
end="2026-03-01"`)을 쓰는 기존 테스트들은 둘 다 정상 파싱되는 값이라 동작 변화가 없어야 한다.

- [x] **Step 6: 전체 스위트 재실행**

Run: `python -m pytest -q`
Expected: 전부 PASS

- [x] **Step 7: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "fix: 백테스트 실행 요청의 날짜 순서 검증도 파싱된 date 기준으로 수정 (그리드서치 Task 3와 동일한 버그)"
```

---

## 마무리 체크

- [x] `python -m pytest -q` 전체 그린 (6개 태스크 반영 후 이전 311개 + Task 2(+0, 기존 테스트
  수정) + Task 3(+1 신규 + 기존 2개 강화) + Task 6(+1 신규) = 313개 근방 — 정확한 숫자보다
  "전부 PASS"가 기준)
- [x] `cd frontend && npx tsc --noEmit` 클린
- [x] 백엔드(`uvicorn`) 재시작 후 `/grid-search` 탭과 `/backtests/{runId}` 상세 페이지에서
  timeframe이 한글 라벨로 정상 표시되는지 수동 확인
