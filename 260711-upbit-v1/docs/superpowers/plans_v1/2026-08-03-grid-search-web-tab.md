# Grid Search 웹 탭 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grid search를 채팅으로 LLM에게 요청해 실행하던 방식에서, 브라우저의 새 "Grid Search"
탭에서 코인/자금/봉데이터/기간/상위N개를 고르면 바로 실행되고 진행률/이력을 볼 수 있는 독립
웹 기능으로 바꾼다.

**Architecture:** `scripts/grid_search.py`는 코드 변경 없이 그대로 서브프로세스로 실행한다.
백엔드(`backend/grid_search_service.py`, 신규)가 그 서브프로세스의 stdout(진행률 로그 +
`RESULT_JSON`)을 읽는 백그라운드 스레드로 새 DB 테이블(`grid_search_jobs`)을 갱신하고,
프론트엔드가 이를 3초 간격으로 폴링해 진행률 바/이력을 보여준다. 한 번에 job 1개만 실행 가능.

**Tech Stack:** Python 3.11.9 표준 라이브러리(`subprocess`/`threading`/`re`/`json`/`uuid`/`signal`,
새 pip 의존성 없음), FastAPI, Next.js 14(App Router) + 기존 shadcn 스타일 컴포넌트.

## Global Constraints

- 스펙: `docs/superpowers/specs_v1/2026-08-03-grid-search-web-tab-design.md`(사용자 승인됨).
- `scripts/grid_search.py`는 이 플랜에서 코드를 수정하지 않는다 — 서브프로세스로 그대로 재사용.
- 서브프로세스는 `sys.executable`(현재 백엔드가 쓰는 것과 동일한 인터프리터)로 실행하고,
  `env`에 `PYTHONPATH`(저장소 루트)와 `PYTHONIOENCODING=utf-8`을 반드시 설정한다(안 하면
  `ModuleNotFoundError` 또는 한글 로그 깨짐 — `.claude/skills/grid-search/SKILL.md`의 기존
  경고와 동일한 이유).
- Windows에서 서브프로세스 취소는 `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`로 띄우고
  `CTRL_BREAK_EVENT` 신호로 그레이스풀 종료를 시도한다(기존 `pool.terminate()` 정리 로직을
  타게 함).
- 한 번에 grid search job 1개만 실행 가능 — 모듈 전역 변수 하나(`_active`)로 관리, DB나 큐
  기반 동시성 제어는 만들지 않는다.
- 백엔드 `--reload` 재시작 시 진행 중이던 job의 추적이 끊길 수 있음은 알려진 제약이다 — 재연결
  로직을 구현하지 않는다.
- DB 스키마 추가는 `engine/cache.py`의 기존 `_SCHEMA`/`CREATE TABLE IF NOT EXISTS` 관례를
  따른다.
- 프론트엔드 검증은 `frontend` 디렉터리에서 `npx tsc --noEmit`으로 한다. **`npm run build`는
  절대 실행하지 않는다** — 이미 떠 있는 `npm run dev`의 `.next` 빌드 산출물을 손상시킨다.
- 프론트엔드에는 이 저장소의 기존 관례대로 자동 단위테스트를 추가하지 않는다(수동 브라우저
  확인으로 대체, Task 11).

---

### Task 1: `grid_search_jobs` 테이블 + CRUD 함수

**Files:**
- Modify: `engine/cache.py` (스키마 블록에 테이블 추가, 파일 끝에 CRUD 함수 추가)
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `create_grid_search_job(job_id: str, market: str, timeframe: str, capital: float, start: str, end: str, top_n: int) -> None`
  - `update_grid_search_job_progress(job_id: str, done_combos: int, total_combos: int) -> None`
  - `finish_grid_search_job(job_id: str, status: str, elapsed_sec: float | None = None, result_json: str | None = None, error_message: str | None = None) -> None`
  - `get_grid_search_job(job_id: str) -> dict | None`
  - `list_grid_search_jobs() -> list[dict]` (최신순)
  - 반환 dict shape: `{"id", "market", "timeframe", "capital", "start", "end", "top_n", "status", "total_combos", "done_combos", "started_at", "finished_at", "elapsed_sec", "error_message", "result_json"}`.
    `result_json`은 저장 시 JSON 문자열이지만 조회 시 파싱된 `list[dict] | None`으로 반환된다.
  - Task 3(`backend/grid_search_service.py`)과 Task 4(`backend/main.py`)가 이 5개 함수를
    그대로 소비한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py` 맨 위 import 블록에 추가:

```python
from engine.cache import (
    create_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    list_grid_search_jobs,
    update_grid_search_job_progress,
)
```

파일 끝에 추가:

```python
def test_create_and_get_grid_search_job_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    job = get_grid_search_job("job-1")
    assert job["id"] == "job-1"
    assert job["market"] == "KRW-SOL"
    assert job["capital"] == 1_000_000.0
    assert job["top_n"] == 20
    assert job["status"] == "running"
    assert job["done_combos"] == 0
    assert job["total_combos"] is None
    assert job["finished_at"] is None
    assert job["result_json"] is None


def test_get_grid_search_job_returns_none_for_missing_id(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert get_grid_search_job("does-not-exist") is None


def test_update_grid_search_job_progress_updates_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    update_grid_search_job_progress("job-1", done_combos=1005, total_combos=20700)

    job = get_grid_search_job("job-1")
    assert job["done_combos"] == 1005
    assert job["total_combos"] == 20700
    assert job["status"] == "running"


def test_finish_grid_search_job_marks_completed_with_results(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=1617.9,
        result_json='[{"rank": 1, "run_id": "abc", "return_pct": 33.65, "title": "[Grid] ..."}]',
    )

    job = get_grid_search_job("job-1")
    assert job["status"] == "completed"
    assert job["elapsed_sec"] == 1617.9
    assert job["finished_at"] is not None
    assert job["result_json"] == [{"rank": 1, "run_id": "abc", "return_pct": 33.65, "title": "[Grid] ..."}]


def test_finish_grid_search_job_marks_failed_with_error_message(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    finish_grid_search_job("job-1", status="failed", error_message="워커 응답 없음")

    job = get_grid_search_job("job-1")
    assert job["status"] == "failed"
    assert job["error_message"] == "워커 응답 없음"
    assert job["result_json"] is None


def test_list_grid_search_jobs_returns_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-02-01", top_n=20,
    )
    create_grid_search_job(
        job_id="job-2", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    jobs = list_grid_search_jobs()
    assert [j["id"] for j in jobs] == ["job-2", "job-1"]
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_cache.py::test_create_and_get_grid_search_job_roundtrips -v`
Expected: FAIL with `ImportError: cannot import name 'create_grid_search_job'`

- [x] **Step 3: 스키마 추가**

`engine/cache.py`에서 `segment_classification` 테이블 정의(24~75행 부근) 바로 다음에 추가:

```python
_SCHEMA += """
CREATE TABLE IF NOT EXISTS grid_search_jobs (
    id             TEXT PRIMARY KEY,
    market         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    capital        REAL NOT NULL,
    start          TEXT NOT NULL,
    end            TEXT NOT NULL,
    top_n          INTEGER NOT NULL,
    status         TEXT NOT NULL,
    total_combos   INTEGER,
    done_combos    INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    elapsed_sec    REAL,
    error_message  TEXT,
    result_json    TEXT
);
"""
```

- [x] **Step 4: CRUD 함수 추가**

`engine/cache.py` 파일 끝(`list_segment_classification` 정의 다음)에 추가:

```python
def create_grid_search_job(
    job_id: str, market: str, timeframe: str, capital: float,
    start: str, end: str, top_n: int,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO grid_search_jobs "
            "(id, market, timeframe, capital, start, end, top_n, status, done_combos, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 0, datetime('now'))",
            (job_id, market, timeframe, capital, start, end, top_n),
        )
        conn.commit()
    finally:
        conn.close()


def update_grid_search_job_progress(job_id: str, done_combos: int, total_combos: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE grid_search_jobs SET done_combos = ?, total_combos = ? WHERE id = ?",
            (done_combos, total_combos, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def finish_grid_search_job(
    job_id: str,
    status: str,
    elapsed_sec: float | None = None,
    result_json: str | None = None,
    error_message: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE grid_search_jobs "
            "SET status = ?, finished_at = datetime('now'), elapsed_sec = ?, "
            "    result_json = ?, error_message = ? "
            "WHERE id = ?",
            (status, elapsed_sec, result_json, error_message, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_grid_search_job_dict(row: tuple) -> dict:
    (job_id, market, timeframe, capital, start, end, top_n, status,
     total_combos, done_combos, started_at, finished_at, elapsed_sec,
     error_message, result_json) = row
    return {
        "id": job_id,
        "market": market,
        "timeframe": timeframe,
        "capital": capital,
        "start": start,
        "end": end,
        "top_n": top_n,
        "status": status,
        "total_combos": total_combos,
        "done_combos": done_combos,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_sec": elapsed_sec,
        "error_message": error_message,
        "result_json": json.loads(result_json) if result_json else None,
    }


def get_grid_search_job(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, market, timeframe, capital, start, end, top_n, status, "
            "       total_combos, done_combos, started_at, finished_at, elapsed_sec, "
            "       error_message, result_json "
            "FROM grid_search_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_grid_search_job_dict(row) if row else None


def list_grid_search_jobs() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, market, timeframe, capital, start, end, top_n, status, "
            "       total_combos, done_combos, started_at, finished_at, elapsed_sec, "
            "       error_message, result_json "
            "FROM grid_search_jobs ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_grid_search_job_dict(r) for r in rows]
```

- [x] **Step 5: 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_cache.py -v`
Expected: 전부 PASS (기존 테스트 + 신규 6개)

- [x] **Step 6: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: add grid_search_jobs table and CRUD functions"
```

---

### Task 2: stdout 로그 파싱 순수 함수

**Files:**
- Create: `backend/grid_search_service.py`
- Test: `tests/test_grid_search_service.py` (신규 파일)

**Interfaces:**
- Produces:
  - `_parse_progress_line(line: str) -> tuple[int, int] | None` — `(완료 개수, 전체 개수)`
  - `_parse_total_combos_line(line: str) -> int | None`
  - `_parse_result_json_line(line: str) -> dict | None`
  - Task 3의 `_reader_loop`가 이 3개 함수를 그대로 소비한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search_service.py` 새로 작성:

```python
from backend.grid_search_service import (
    _parse_progress_line,
    _parse_result_json_line,
    _parse_total_combos_line,
)


def test_parse_progress_line_extracts_done_and_total():
    assert _parse_progress_line("    완료 1,005/20,700건 (4.9%)") == (1005, 20700)


def test_parse_progress_line_returns_none_for_unrelated_line():
    assert _parse_progress_line("[1] 캔들 조회: KRW-SOL minutes60 2026-06-05 ~ 2026-08-03") is None


def test_parse_total_combos_line_extracts_total():
    line = "[2] 매수 조건 138개 x 매도 조건 150개 = 총 20,700개 조합"
    assert _parse_total_combos_line(line) == 20700


def test_parse_total_combos_line_returns_none_for_unrelated_line():
    assert _parse_total_combos_line("완료 1,005/20,700건 (4.9%)") is None


def test_parse_result_json_line_extracts_payload():
    line = 'RESULT_JSON: {"total_combos": 20700, "elapsed_sec": 1617.9, "saved": []}'
    assert _parse_result_json_line(line) == {"total_combos": 20700, "elapsed_sec": 1617.9, "saved": []}


def test_parse_result_json_line_returns_none_for_unrelated_line():
    assert _parse_result_json_line("완료.") is None
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.grid_search_service'`

- [x] **Step 3: 최소 구현**

`backend/grid_search_service.py` 새로 작성:

```python
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
```

- [x] **Step 4: 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search_service.py -v`
Expected: 전부 PASS (6개)

- [x] **Step 5: 커밋**

```bash
git add backend/grid_search_service.py tests/test_grid_search_service.py
git commit -m "feat: add pure stdout log parsers for grid search job orchestration"
```

---

### Task 3: job 오케스트레이션 (`start_job`/`cancel_job`)

**Files:**
- Modify: `backend/grid_search_service.py`
- Test: `tests/test_grid_search_service.py`

**Interfaces:**
- Consumes: Task 1의 `create_grid_search_job`/`update_grid_search_job_progress`/
  `finish_grid_search_job`/`get_grid_search_job`, Task 2의 3개 파서.
- Produces:
  - `start_job(market: str, timeframe: str, capital: float, start: str, end: str, top_n: int) -> str` (job_id 반환)
  - `cancel_job(job_id: str) -> None`
  - `JobAlreadyRunningError(Exception)`, `JobNotActiveError(Exception)`
  - Task 4(`backend/main.py`)가 이 4개를 그대로 소비한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search_service.py` 맨 위 import를 아래로 교체:

```python
import signal

import pytest

import backend.grid_search_service as gss
import engine.cache as cache_module
from backend.grid_search_service import (
    _parse_progress_line,
    _parse_result_json_line,
    _parse_total_combos_line,
)
from engine.cache import get_grid_search_job
```

파일 끝(기존 파서 테스트들 다음)에 추가:

```python
class _SyncThread:
    """threading.Thread를 흉내내되 start()가 target을 즉시(동기) 실행한다 — 테스트에서
    백그라운드 스레드 완료를 기다릴 필요 없이 바로 DB 상태를 확인하기 위함."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class _NoOpThread:
    """target을 저장만 하고 실행하지 않는다 — "아직 실행 중"인 상황(동시 실행 충돌,
    취소)을 테스트할 때 리더 스레드가 먼저 끝나버리는 걸 막기 위함."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args

    def start(self):
        pass


class _FakePopen:
    stdout_lines: list[str] = []
    returncode: int = 0

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242
        self.stdout = iter(type(self).stdout_lines)
        self.returncode = type(self).returncode
        self.signals_sent: list[int] = []

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass

    def send_signal(self, sig):
        self.signals_sent.append(sig)


@pytest.fixture(autouse=True)
def _reset_grid_search_service_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(gss, "_active", None)
    monkeypatch.setattr(gss.threading, "Thread", _SyncThread)
    monkeypatch.setattr(gss.subprocess, "Popen", _FakePopen)


def test_start_job_completes_and_saves_result():
    _FakePopen.stdout_lines = [
        "[1] 캔들 조회: KRW-SOL minutes60 2026-06-05 ~ 2026-08-03\n",
        "[2] 매수 조건 138개 x 매도 조건 150개 = 총 20,700개 조합\n",
        "    완료 20,700/20,700건 (100.0%)\n",
        'RESULT_JSON: {"total_combos": 20700, "elapsed_sec": 1617.9, '
        '"saved": [{"rank": 1, "run_id": "abc", "return_pct": 33.65, "title": "[Grid] x"}]}\n',
    ]
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    job = get_grid_search_job(job_id)
    assert job["status"] == "completed"
    assert job["done_combos"] == 20700
    assert job["total_combos"] == 20700
    assert job["elapsed_sec"] == 1617.9
    assert job["result_json"] == [{"rank": 1, "run_id": "abc", "return_pct": 33.65, "title": "[Grid] x"}]
    assert gss._active is None


def test_start_job_marks_failed_when_script_exits_nonzero():
    _FakePopen.stdout_lines = [
        "[1] 캔들 조회: KRW-ETH minutes60 2026-05-01 ~ 2026-05-03\n",
        "선택된 그리드가 최소 43개의 봉을 필요로 하지만, 해당 기간에는 20개의 봉만 있습니다. 기간을 늘리세요.\n",
    ]
    _FakePopen.returncode = 1

    job_id = gss.start_job(
        market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-05-01", end="2026-05-03", top_n=20,
    )

    job = get_grid_search_job(job_id)
    assert job["status"] == "failed"
    assert "43개의 봉" in job["error_message"]


def test_start_job_raises_when_already_running(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    with pytest.raises(gss.JobAlreadyRunningError):
        gss.start_job(
            market="KRW-BTC", timeframe="days", capital=1_000_000,
            start="2026-01-01", end="2026-02-01", top_n=20,
        )


def test_cancel_job_sends_ctrl_break_and_reader_loop_marks_canceled(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = ["일부 진행 로그\n"]
    _FakePopen.returncode = 1

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    proc = gss._active["proc"]
    canceled_event = gss._active["canceled"]

    gss.cancel_job(job_id)
    assert signal.CTRL_BREAK_EVENT in proc.signals_sent

    # 실제로는 신호를 받은 서브프로세스가 종료되며 stdout이 닫히고, 이미 떠 있던 리더
    # 스레드가 그걸 감지해 _reader_loop를 끝까지 실행한다. _NoOpThread는 스레드를 실제로
    # 돌리지 않으므로 여기서 직접 실행해 그 결과를 재현한다.
    gss._reader_loop(job_id, proc, canceled_event)

    job = get_grid_search_job(job_id)
    assert job["status"] == "canceled"
    assert gss._active is None


def test_cancel_job_raises_when_no_job_is_active():
    with pytest.raises(gss.JobNotActiveError):
        gss.cancel_job("does-not-exist")
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search_service.py -v`
Expected: FAIL — `AttributeError: module 'backend.grid_search_service' has no attribute 'start_job'`
(그리고 `_reset_grid_search_service_state`가 아직 없는 `gss._active`/`gss.threading`/
`gss.subprocess`를 참조해 fixture 단계에서부터 에러가 날 수 있음 — Step 3 구현 후 정상화됨)

- [x] **Step 3: 최소 구현**

`backend/grid_search_service.py`의 기존 파서 3개 함수 다음에 추가:

```python
import os
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

REPO_ROOT = Path(__file__).resolve().parent.parent


class JobAlreadyRunningError(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"이미 실행 중인 grid search가 있습니다: {job_id}")


class JobNotActiveError(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"실행 중인 job이 아닙니다: {job_id}")


_lock = threading.Lock()
_active: dict | None = None  # {"job_id": str, "proc": Popen, "canceled": threading.Event}


def _reader_loop(job_id: str, proc, canceled: threading.Event) -> None:
    """서브프로세스 stdout을 줄 단위로 읽으며 진행률/결과를 DB에 반영하고, 프로세스
    종료 후 최종 상태(completed/failed/canceled)를 기록한다. 도중에 예외(예: 진행률을
    DB에 쓰다가 발생하는 SQLite 잠금 에러, 손상된 RESULT_JSON 파싱 실패)가 나도 _active를
    반드시 비운다 — 안 그러면 단일 실행 슬롯이 영구히 막혀 서버를 재시작할 때까지 새
    grid search를 하나도 시작할 수 없게 된다."""
    error_lines: list[str] = []
    result: dict | None = None
    unexpected_error: str | None = None

    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")

            progress = _parse_progress_line(line)
            if progress is not None:
                done, total = progress
                update_grid_search_job_progress(job_id, done_combos=done, total_combos=total)
                continue

            early_total = _parse_total_combos_line(line)
            if early_total is not None:
                update_grid_search_job_progress(job_id, done_combos=0, total_combos=early_total)
                continue

            parsed_result = _parse_result_json_line(line)
            if parsed_result is not None:
                result = parsed_result
                continue

            if line.strip():
                error_lines.append(line.strip())

        proc.wait()
    except Exception as exc:
        unexpected_error = f"진행률 처리 중 예외 발생: {exc}"
        try:
            proc.terminate()
        except Exception:
            pass

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


def start_job(market: str, timeframe: str, capital: float, start: str, end: str, top_n: int) -> str:
    """grid search job을 시작하고 job_id를 반환한다. 이미 실행 중인 job이 있으면
    JobAlreadyRunningError를 던진다."""
    global _active
    with _lock:
        if _active is not None:
            raise JobAlreadyRunningError(_active["job_id"])

        job_id = uuid.uuid4().hex
        create_grid_search_job(
            job_id=job_id, market=market, timeframe=timeframe, capital=capital,
            start=start, end=end, top_n=top_n,
        )

        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            [
                sys.executable, "scripts/grid_search.py",
                "--market", market, "--timeframe", timeframe,
                "--capital", str(capital), "--start", start, "--end", end,
                "--top-n", str(top_n),
            ],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            env=env,
            creationflags=creationflags,
        )
        canceled = threading.Event()
        _active = {"job_id": job_id, "proc": proc, "canceled": canceled}

    # thread.start()는 반드시 `with _lock:` 블록 밖에서 호출한다. 테스트의 동기 실행
    # 더블(_SyncThread)은 .start() 안에서 _reader_loop를 즉시 실행하는데, _reader_loop도
    # 끝에서 `with _lock:`을 잡는다 — thread.start()가 잠금 블록 안에 있으면 같은 스레드가
    # 아직 풀리지 않은 threading.Lock을 다시 잡으려다 자기 자신과 데드락한다(실제 운영에서는
    # 스레드가 분리되어 있어 드러나지 않는 버그였다).
    thread = threading.Thread(target=_reader_loop, args=(job_id, proc, canceled), daemon=True)
    thread.start()

    return job_id


def cancel_job(job_id: str) -> None:
    """실행 중인 job을 취소한다. Windows에서는 CTRL_BREAK_EVENT로 그레이스풀 종료를
    시도하고(기존 pool.terminate() 정리 로직을 타게 함), 15초 내 안 죽으면 강제 종료한다."""
    with _lock:
        if _active is None or _active["job_id"] != job_id:
            raise JobNotActiveError(job_id)
        proc = _active["proc"]
        _active["canceled"].set()

    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.terminate()

    def _force_kill_if_still_running() -> None:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()

    threading.Thread(target=_force_kill_if_still_running, daemon=True).start()
```

- [x] **Step 4: 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search_service.py -v`
Expected: 전부 PASS (11개 — 파서 6개 + 오케스트레이션 5개)

- [x] **Step 5: 커밋**

```bash
git add backend/grid_search_service.py tests/test_grid_search_service.py
git commit -m "feat: run grid search as a subprocess with progress tracking and cancellation"
```

---

### Task 4: 백엔드 API 엔드포인트

**Files:**
- Modify: `backend/main.py` (import 추가, 엔드포인트 4개 추가)
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: Task 1의 `get_grid_search_job`/`list_grid_search_jobs`, Task 3의
  `start_job`/`cancel_job`/`JobAlreadyRunningError`/`JobNotActiveError`.
- Produces: `POST /api/v1/grid-search/jobs`, `GET /api/v1/grid-search/jobs`,
  `GET /api/v1/grid-search/jobs/{job_id}`, `POST /api/v1/grid-search/jobs/{job_id}/cancel`.
  Task 5(프론트엔드 API 클라이언트)가 이 4개를 그대로 소비한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_create_grid_search_job_returns_running_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    from engine.cache import create_grid_search_job
    monkeypatch.setattr(backend_module, "start_job", lambda **kwargs: "job-1")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "job-1"
    assert body["status"] == "running"


def test_create_grid_search_job_rejects_market_not_in_krw_list(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_rejects_reversed_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-08-03", "end": "2026-06-05", "top_n": 20,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_rejects_top_n_out_of_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 51,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_returns_409_when_already_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    def _raise(**kwargs):
        raise backend_module.JobAlreadyRunningError("job-existing")

    monkeypatch.setattr(backend_module, "start_job", _raise)

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 409


def test_get_grid_search_job_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/grid-search/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_grid_search_jobs_returns_saved_jobs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    resp = client.get("/api/v1/grid-search/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "job-1"


def test_cancel_grid_search_job_returns_409_when_not_active(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _raise(job_id):
        raise backend_module.JobNotActiveError(job_id)

    monkeypatch.setattr(backend_module, "cancel_job", _raise)

    resp = client.post("/api/v1/grid-search/jobs/job-1/cancel")
    assert resp.status_code == 409
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py::test_create_grid_search_job_returns_running_job -v`
Expected: FAIL with 404 (라우트가 아직 없음 — `assert resp.status_code == 200` 실패)

- [x] **Step 3: 최소 구현**

`backend/main.py`의 `from engine.cache import (...)` 블록(17~29행)을 아래로 교체:

```python
from engine.cache import (
    delete_backtest_run,
    get_grid_search_job,
    get_run_config,
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_grid_search_jobs,
    list_latest_sweep_results,
    list_segment_classification,
    list_sweep_history,
    load_result,
    run_backtest_cached,
    save_result,
)
```

`from upbit_data_service import ...` 임포트 다음 줄(53행 부근)에 추가:

```python
from backend.grid_search_service import JobAlreadyRunningError, JobNotActiveError, cancel_job, start_job
```

파일 끝(`validate_backtest_endpoint` 다음)에 추가:

```python
class GridSearchJobRequest(BaseModel):
    market: str
    timeframe: str
    capital: float
    start: str
    end: str
    top_n: int = 20


def _validate_grid_search_request(req: GridSearchJobRequest) -> list[str]:
    errors: list[str] = []
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


def _grid_search_job_response(job: dict) -> dict:
    return {
        **job,
        "started_at": _to_utc_iso(job["started_at"]),
        "finished_at": _to_utc_iso(job["finished_at"]) if job["finished_at"] else None,
    }


@app.post("/api/v1/grid-search/jobs")
def create_grid_search_job_endpoint(req: GridSearchJobRequest) -> dict:
    errors = _validate_grid_search_request(req)
    if errors:
        raise HTTPException(status_code=400, detail=" / ".join(errors))

    try:
        job_id = start_job(
            market=req.market, timeframe=req.timeframe, capital=req.capital,
            start=req.start, end=req.end, top_n=req.top_n,
        )
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = get_grid_search_job(job_id)
    assert job is not None
    return _grid_search_job_response(job)


@app.get("/api/v1/grid-search/jobs")
def list_grid_search_jobs_endpoint() -> list[dict]:
    return [_grid_search_job_response(j) for j in list_grid_search_jobs()]


@app.get("/api/v1/grid-search/jobs/{job_id}")
def get_grid_search_job_endpoint(job_id: str) -> dict:
    job = get_grid_search_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 job을 찾을 수 없습니다")
    return _grid_search_job_response(job)


@app.post("/api/v1/grid-search/jobs/{job_id}/cancel")
def cancel_grid_search_job_endpoint(job_id: str) -> dict:
    try:
        cancel_job(job_id)
    except JobNotActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "canceling"}
```

- [x] **Step 4: 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -v`
Expected: 전부 PASS (기존 테스트 + 신규 8개)

- [x] **Step 5: 전체 스위트 회귀 확인**

Run: `PYTHONPATH=. python -m pytest -v`
Expected: 전부 PASS

- [x] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: add grid search job API endpoints"
```

---

### Task 5: 프론트엔드 타입 + API 클라이언트

**Files:**
- Modify: `frontend/lib/types/eda.ts` (타입 추가)
- Modify: `frontend/lib/api/eda.ts` (함수 추가)

**Interfaces:**
- Produces: `GridSearchJob`, `GridSearchJobRequest`, `GridSearchSavedResult` 타입,
  `createGridSearchJob(req)`, `getGridSearchJobs()`, `cancelGridSearchJob(jobId)` 함수.
  Task 7~9의 컴포넌트가 이걸 그대로 소비한다.

- [x] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts` 파일 끝에 추가:

```typescript
export interface GridSearchJobRequest {
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
}

export interface GridSearchSavedResult {
  rank: number;
  run_id: string;
  return_pct: number;
  title: string;
}

export interface GridSearchJob {
  id: string;
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
  status: 'running' | 'completed' | 'failed' | 'canceled';
  total_combos: number | null;
  done_combos: number;
  started_at: string;
  finished_at: string | null;
  elapsed_sec: number | null;
  error_message: string | null;
  result_json: GridSearchSavedResult[] | null;
}
```

- [x] **Step 2: API 클라이언트 함수 추가**

`frontend/lib/api/eda.ts` 맨 위 import 블록의 `from '@/lib/types/eda'` 목록에
`GridSearchJob`, `GridSearchJobRequest` 추가(알파벳 순서 유지). 파일 끝에 추가:

```typescript
export function createGridSearchJob(req: GridSearchJobRequest): Promise<GridSearchJob> {
  return apiFetch<GridSearchJob>('/api/v1/grid-search/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function getGridSearchJobs(): Promise<GridSearchJob[]> {
  return apiFetch<GridSearchJob[]>('/api/v1/grid-search/jobs');
}

export function cancelGridSearchJob(jobId: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/grid-search/jobs/${jobId}/cancel`, { method: 'POST' });
}
```

- [x] **Step 3: 타입 체크 통과 확인**

Run (frontend 디렉터리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: add grid search job types and API client functions"
```

---

### Task 6: 네비게이션 탭 추가

**Files:**
- Modify: `frontend/components/NavTabs.tsx`

**Interfaces:** 없음(라우팅만).

- [x] **Step 1: 탭 추가**

`frontend/components/NavTabs.tsx`의 import 줄을 교체:

```typescript
import { BarChart3, BookOpen, FlaskConical, Grid3x3, Settings } from 'lucide-react';
```

`STEPS` 배열을 아래로 교체:

```typescript
const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/analysis', title: '분석', icon: BarChart3 },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];
```

- [x] **Step 2: 타입 체크 통과 확인**

Run (frontend 디렉터리에서): `npx tsc --noEmit`
Expected: 에러 없음(단, 이 시점엔 `/grid-search` 라우트가 아직 없어 `next lint`의 링크 검사 등은
Task 7 이후에나 완전히 통과함 — `tsc --noEmit`은 `NavTabs.tsx` 자체의 타입 오류만 체크하므로
문제 없다)

- [x] **Step 3: 커밋**

```bash
git add frontend/components/NavTabs.tsx
git commit -m "feat: add Grid Search nav tab"
```

---

### Task 7: Grid Search 폼 + 페이지 골격

**Files:**
- Create: `frontend/components/GridSearchForm.tsx`
- Create: `frontend/components/GridSearchPage.tsx`
- Create: `frontend/app/grid-search/page.tsx`

**Interfaces:**
- Consumes: Task 5의 `createGridSearchJob`/`getGridSearchJobs`/`cancelGridSearchJob`, `GridSearchJob`/`GridSearchJobRequest`.
- Produces: `GridSearchForm` props `{ initial: GridSearchFormInitial; disabled: boolean; onSubmit: (req: GridSearchJobRequest) => Promise<void> }`.
  Task 8/9가 `GridSearchPage.tsx`의 진행률/이력 플레이스홀더를 실제 컴포넌트로 교체한다.

- [x] **Step 1: `GridSearchForm.tsx` 작성**

```typescript
'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import type { GridSearchJobRequest } from '@/lib/types/eda';
import type { Market } from '@/lib/types/eda';

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

function formatCapital(digits: string): string {
  if (!digits) return '';
  return Number(digits).toLocaleString('ko-KR');
}

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export interface GridSearchFormInitial {
  market: string;
  timeframe: string;
  capital: string;
  start: string;
  end: string;
  topN: string;
}

interface GridSearchFormProps {
  initial: GridSearchFormInitial;
  disabled: boolean;
  onSubmit: (request: GridSearchJobRequest) => Promise<void>;
}

export default function GridSearchForm({ initial, disabled, onSubmit }: GridSearchFormProps) {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState(initial.market);
  const [timeframe, setTimeframe] = useState(initial.timeframe);
  const [capital, setCapital] = useState(initial.capital.replace(/[^0-9]/g, ''));
  const [start, setStart] = useState(initial.start || defaultDate(60));
  const [end, setEnd] = useState(initial.end || defaultDate(0));
  const [topN, setTopN] = useState(initial.topN);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  async function handleSubmit() {
    setValidationError(null);
    if (start >= end) {
      setValidationError('시작일은 종료일보다 빨라야 합니다.');
      return;
    }
    const topNValue = Number(topN);
    if (!Number.isInteger(topNValue) || topNValue < 1 || topNValue > 50) {
      setValidationError('상위N개는 1~50 사이의 정수여야 합니다.');
      return;
    }

    setSubmitting(true);
    try {
      await onSubmit({ market, timeframe, capital: Number(capital), start, end, top_n: topNValue });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">운용자금</label>
          <div className="flex items-center gap-2">
            <Input
              type="text"
              inputMode="numeric"
              value={formatCapital(capital)}
              onChange={(e) => setCapital(e.target.value.replace(/[^0-9]/g, ''))}
            />
            <span className="text-sm text-muted-foreground">원</span>
          </div>
        </div>
      </div>

      <div>
        <div className={SECTION_HEADER_CLASS}>봉데이터</div>
        <div className="flex flex-wrap gap-2 p-3">
          {TIMEFRAME_OPTIONS.map((opt) => (
            <Button
              key={opt.timeframe}
              type="button"
              variant={timeframe === opt.timeframe ? 'default' : 'outline'}
              size="sm"
              onClick={() => setTimeframe(opt.timeframe)}
            >
              {opt.label}
            </Button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[2fr_2fr_1fr]">
        <div>
          <label className="mb-1.5 block text-sm font-medium">시작일</label>
          <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">종료일</label>
          <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">상위N개</label>
          <Input type="number" min={1} max={50} value={topN} onChange={(e) => setTopN(e.target.value)} />
        </div>
      </div>

      {validationError && <p className="text-sm text-destructive">{validationError}</p>}
      {disabled && (
        <p className="text-sm text-muted-foreground">
          이미 실행 중인 grid search가 있습니다. 완료 후 새 요청을 시작할 수 있습니다.
        </p>
      )}

      <Button onClick={handleSubmit} disabled={disabled || submitting || !market}>
        {submitting ? '시작하는 중...' : '그리드서치 시작'}
      </Button>
      <p className="text-xs text-muted-foreground">
        9-오실레이터 전 교차 20,700개 조합, 워커 4개 병렬 기준 약 20~30분 소요됩니다.
      </p>
    </div>
  );
}
```

- [x] **Step 2: `GridSearchPage.tsx` 작성**

```typescript
'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ApiError } from '@/lib/api/client';
import { cancelGridSearchJob, createGridSearchJob, getGridSearchJobs } from '@/lib/api/eda';
import type { GridSearchJob, GridSearchJobRequest } from '@/lib/types/eda';
import GridSearchForm from '@/components/GridSearchForm';

const POLL_INTERVAL_MS = 3000;

export default function GridSearchPage() {
  const searchParams = useSearchParams();
  const [jobs, setJobs] = useState<GridSearchJob[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getGridSearchJobs();
      setJobs(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '이력을 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const runningJob = jobs.find((j) => j.status === 'running') ?? null;

  useEffect(() => {
    if (!runningJob) return;
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [runningJob, refresh]);

  async function handleSubmit(request: GridSearchJobRequest) {
    setSubmitError(null);
    try {
      const job = await createGridSearchJob(request);
      setJobs((prev) => [job, ...prev]);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'grid search 시작 중 오류가 발생했습니다.');
    }
  }

  async function handleCancel() {
    if (!runningJob) return;
    try {
      await cancelGridSearchJob(runningJob.id);
      await refresh();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '취소 중 오류가 발생했습니다.');
    }
  }

  return (
    <div className="space-y-6">
      <GridSearchForm
        initial={{
          market: searchParams.get('market') ?? '',
          timeframe: searchParams.get('timeframe') ?? 'minutes60',
          capital: searchParams.get('capital') ?? '1000000',
          start: searchParams.get('start') ?? '',
          end: searchParams.get('end') ?? '',
          topN: searchParams.get('topN') ?? '20',
        }}
        disabled={runningJob !== null}
        onSubmit={handleSubmit}
      />
      {submitError && <p className="text-sm text-destructive">{submitError}</p>}
      {runningJob && (
        <p className="text-sm">
          진행 중: {runningJob.market} {runningJob.done_combos}/{runningJob.total_combos ?? '?'}건
          <button type="button" className="ml-2 underline" onClick={handleCancel}>
            취소
          </button>
        </p>
      )}
      {loadError && <p className="text-sm text-destructive">{loadError}</p>}
      <div className="text-sm text-muted-foreground">요청 이력은 다음 단계에서 추가됩니다.</div>
    </div>
  );
}
```

- [x] **Step 3: `app/grid-search/page.tsx` 작성**

```typescript
import { Suspense } from 'react';
import GridSearchPage from '@/components/GridSearchPage';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Grid Search</h1>
      <Suspense fallback={null}>
        <GridSearchPage />
      </Suspense>
    </div>
  );
}
```

- [x] **Step 4: 타입 체크 통과 확인**

Run (frontend 디렉터리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 5: 커밋**

```bash
git add frontend/components/GridSearchForm.tsx frontend/components/GridSearchPage.tsx frontend/app/grid-search/page.tsx
git commit -m "feat: add grid search form and page skeleton"
```

---

### Task 8: 진행률 카드 + 취소 버튼

**Files:**
- Create: `frontend/components/GridSearchProgress.tsx`
- Modify: `frontend/components/GridSearchPage.tsx`

**Interfaces:**
- Consumes: Task 5의 `GridSearchJob` 타입.
- Produces: `GridSearchProgress` props `{ job: GridSearchJob; onCancel: () => void }`.

- [x] **Step 1: `GridSearchProgress.tsx` 작성**

```typescript
'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { GridSearchJob } from '@/lib/types/eda';

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}분 ${s}초`;
}

interface GridSearchProgressProps {
  job: GridSearchJob;
  onCancel: () => void;
}

export default function GridSearchProgress({ job, onCancel }: GridSearchProgressProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const elapsedSec = Math.max(0, (now - new Date(job.started_at).getTime()) / 1000);
  const pct = job.total_combos ? (job.done_combos / job.total_combos) * 100 : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          진행 중: {job.market} · {job.timeframe} · {job.start}~{job.end}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {job.total_combos
              ? `${pct.toFixed(1)}% (${job.done_combos.toLocaleString()} / ${job.total_combos.toLocaleString()}건)`
              : '계산 준비 중...'}
          </span>
          <span>경과 {formatElapsed(elapsedSec)}</span>
        </div>
        <Button variant="destructive" size="sm" onClick={onCancel}>
          취소
        </Button>
      </CardContent>
    </Card>
  );
}
```

- [x] **Step 2: `GridSearchPage.tsx`에서 플레이스홀더를 실제 컴포넌트로 교체**

`frontend/components/GridSearchPage.tsx`의 import 목록에 추가:

```typescript
import GridSearchProgress from '@/components/GridSearchProgress';
```

아래 블록을:

```typescript
      {runningJob && (
        <p className="text-sm">
          진행 중: {runningJob.market} {runningJob.done_combos}/{runningJob.total_combos ?? '?'}건
          <button type="button" className="ml-2 underline" onClick={handleCancel}>
            취소
          </button>
        </p>
      )}
```

아래로 교체:

```typescript
      {runningJob && <GridSearchProgress job={runningJob} onCancel={handleCancel} />}
```

- [x] **Step 3: 타입 체크 통과 확인**

Run (frontend 디렉터리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 4: 커밋**

```bash
git add frontend/components/GridSearchProgress.tsx frontend/components/GridSearchPage.tsx
git commit -m "feat: add grid search progress card with cancel button"
```

---

### Task 9: 요청 이력

**Files:**
- Create: `frontend/components/GridSearchHistory.tsx`
- Modify: `frontend/components/GridSearchPage.tsx`

**Interfaces:**
- Consumes: Task 5의 `GridSearchJob` 타입.
- Produces: `GridSearchHistory` props `{ jobs: GridSearchJob[] }`.

- [x] **Step 1: `GridSearchHistory.tsx` 작성**

```typescript
'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime } from '@/lib/format';
import type { GridSearchJob } from '@/lib/types/eda';

const STATUS_LABEL: Record<GridSearchJob['status'], string> = {
  running: '진행중',
  completed: '완료',
  failed: '실패',
  canceled: '취소',
};

const STATUS_VARIANT: Record<GridSearchJob['status'], 'secondary' | 'default' | 'destructive' | 'outline'> = {
  running: 'secondary',
  completed: 'default',
  failed: 'destructive',
  canceled: 'outline',
};

function formatElapsedMinutes(seconds: number | null): string {
  if (seconds === null) return '-';
  return `${(seconds / 60).toFixed(1)}분`;
}

interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
}

export default function GridSearchHistory({ jobs }: GridSearchHistoryProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 실행한 grid search가 없습니다.</p>;
  }

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">요청 이력</h2>
      <div className="space-y-2">
        {jobs.map((job) => {
          const results = job.result_json ?? [];
          const isExpanded = expanded.has(job.id);
          const visibleResults = isExpanded ? results : results.slice(0, 1);
          return (
            <div key={job.id} className="rounded-md border p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABEL[job.status]}</Badge>
                <span className="font-medium">{job.market}</span>
                <span className="text-muted-foreground">{job.timeframe}</span>
                <span className="text-muted-foreground">
                  {job.start}~{job.end}
                </span>
                <span className="text-muted-foreground">상위{job.top_n}</span>
                {job.elapsed_sec !== null && (
                  <span className="text-muted-foreground">{formatElapsedMinutes(job.elapsed_sec)}</span>
                )}
                <span className="ml-auto text-xs text-muted-foreground">{formatDateTime(job.started_at)}</span>
              </div>

              {job.status === 'failed' && job.error_message && (
                <p className="mt-2 text-sm text-destructive">{job.error_message}</p>
              )}

              {results.length > 0 && (
                <div className="mt-2 space-y-1">
                  {visibleResults.map((r) => (
                    <div key={r.run_id} className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">{r.rank}위</span>
                      <span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>
                      <Link href={`/backtests/${r.run_id}`} className="truncate underline">
                        {r.title}
                      </Link>
                    </div>
                  ))}
                  {results.length > 1 && (
                    <Button variant="link" size="sm" className="px-0" onClick={() => toggle(job.id)}>
                      {isExpanded ? '접기' : `나머지 ${results.length - 1}개 보기`}
                    </Button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [x] **Step 2: `GridSearchPage.tsx`에서 플레이스홀더를 실제 컴포넌트로 교체**

`frontend/components/GridSearchPage.tsx`의 import 목록에 추가:

```typescript
import GridSearchHistory from '@/components/GridSearchHistory';
```

아래 줄을:

```typescript
      <div className="text-sm text-muted-foreground">요청 이력은 다음 단계에서 추가됩니다.</div>
```

아래로 교체:

```typescript
      <GridSearchHistory jobs={jobs} />
```

- [x] **Step 3: 타입 체크 통과 확인**

Run (frontend 디렉터리에서): `npx tsc --noEmit`
Expected: 에러 없음

- [x] **Step 4: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx frontend/components/GridSearchPage.tsx
git commit -m "feat: add grid search request history list"
```

---

### Task 10: 기존 grid-search 스킬을 실행 → 안내로 변경

**Files:**
- Modify: `.claude/skills/grid-search/SKILL.md`

**Interfaces:** 없음(문서만).

- [x] **Step 1: "실행 절차"/"주의 사항" 교체**

`.claude/skills/grid-search/SKILL.md`의 `## 실행 절차`부터 파일 끝까지를 아래로 교체:

```markdown
## 실행 절차

1. 위 규칙대로 명령을 파싱한다.
2. 코인명/운용자금/봉데이터/운용기간 4개 필드 중 하나라도 파싱할 수 없거나 모호하면,
   실행 안내 없이 사용자에게 되물어라.
3. 파싱에 성공하면 아래 형태의 링크를 만든다(로컬 개발 서버 주소):

   ```
   http://localhost:3000/grid-search?market=KRW-SOL&timeframe=minutes60&capital=1000000&start=2026-06-05&end=2026-08-03&topN=20
   ```

4. `scripts/grid_search.py`를 직접 실행하지 말고, 파싱 결과 표와 함께 위 링크를 안내한다.
   예:

   > 아래 조건으로 "Grid Search" 탭에서 바로 실행할 수 있습니다:
   >
   > | 필드 | 값 |
   > |---|---|
   > | 마켓코드 | KRW-SOL |
   > | timeframe | minutes60 |
   > | 운용자금 | 1,000,000원 |
   > | 기간 | 2026-06-05 ~ 2026-08-03 |
   > | 상위N개 | 20 |
   >
   > http://localhost:3000/grid-search?market=KRW-SOL&timeframe=minutes60&capital=1000000&start=2026-06-05&end=2026-08-03&topN=20
   >
   > 진행률과 요청 이력도 그 탭에서 확인할 수 있습니다.

## 주의 사항

- 이 스킬은 더 이상 `scripts/grid_search.py`를 직접 실행하지 않는다 — 파싱/검증 후 웹 탭으로
  안내만 한다. 실제 실행/진행률 추적/취소는 프론트엔드 "Grid Search" 탭(`/grid-search`)과
  백엔드 `/api/v1/grid-search/jobs*` 엔드포인트가 담당한다.
- 링크의 쿼리파라미터는 `market`(마켓코드)/`timeframe`(timeframe 코드)/`capital`(원 단위
  정수)/`start`/`end`(`YYYY-MM-DD`)/`topN`(정수) 6개다. 값에 특수문자가 없으므로 별도 URL
  인코딩 없이 그대로 이어 붙이면 된다.
```

- [x] **Step 2: 커밋**

```bash
git add .claude/skills/grid-search/SKILL.md
git commit -m "docs: point grid-search skill to the web tab instead of executing the script"
```

---

### Task 11: 수동 통합 검증

**Files:** 없음(코드 변경 없음, 검증만).

**Interfaces:** 없음.

이 저장소는 프론트엔드 자동 테스트 관례가 없고, 서브프로세스/폴링/취소는 mock으로 검증하기
어려우므로, 실제 두 서버를 띄워 브라우저로 전 과정을 확인한다.

- [x] **Step 1: 서버 기동 확인**

`localhost:8000`(백엔드, `uvicorn backend.main:app --reload --port 8000`)과
`localhost:3000`(프론트, `npm run dev`)이 이미 떠 있는지 확인하고, 안 떠 있으면 저장소 루트와
`frontend/`에서 각각 기동한다.

- [x] **Step 2: 폼 제출 → 진행률 확인**

브라우저로 `http://localhost:3000/grid-search` 접속. 코인(예: KRW-BTC), 봉데이터를 `1일`로,
기간을 짧게(예: 최근 2개월) 선택해 "그리드서치 시작" 클릭(일봉은 캔들 수가 적어 한 조합
계산이 빨라 전체 시간이 짧다 — 조합 수 20,700개 자체는 고정이라 몇 분은 걸린다).

확인 항목:
- 제출 직후 진행 중 카드가 나타나고, 폼의 "그리드서치 시작" 버튼이 비활성화되는지
- 몇 초~1분 내에 진행률 바가 움직이기 시작하는지(총 조합 수가 먼저 뜬 다음 퍼센트가 올라감)
- 경과 시간 타이머가 매초 갱신되는지

- [x] **Step 3: 취소 확인**

진행 중 카드의 "취소" 버튼 클릭. 몇 초 내에 진행 중 카드가 사라지고, 요청 이력에 "취소"
상태로 해당 항목이 뜨는지 확인. 폼의 "그리드서치 시작" 버튼이 다시 활성화되는지 확인.

- [x] **Step 4: 완료 확인**

다시 짧은 기간으로 제출해 끝까지 완료시킨다(수 분 소요). 완료 후:
- 요청 이력에 "완료" 상태와 1위 결과(수익률/제목)가 보이는지
- "나머지 N개 보기"를 눌러 상위 결과 전체가 펼쳐지는지
- 결과 항목의 링크를 클릭하면 `/backtests/{run_id}` 상세 페이지로 이동해 자산곡선/거래내역이
  뜨는지

- [x] **Step 5: 동시 실행 차단 + 프리필 링크 확인**

- 진행 중인 job이 있는 상태에서 다시 폼을 제출해 보고, "이미 실행 중인 grid search가
  있습니다" 안내가 뜨는지 확인.
- 새 탭에서 `http://localhost:3000/grid-search?market=KRW-BTC&timeframe=days&capital=1000000&start=2026-06-01&end=2026-07-01&topN=10`
  로 접속해 폼 필드가 쿼리파라미터대로 채워지는지 확인.

- [x] **Step 6: 전체 pytest 스위트 최종 확인**

Run: `PYTHONPATH=. python -m pytest -v`
Expected: 전부 PASS

---

## Self-Review 결과

- **스펙 커버리지**: 스펙의 데이터 모델(Task 1) / 백엔드 오케스트레이션(Task 2, 3) / API
  엔드포인트(Task 4) / 화면(Task 5~9) / 취소 처리(Task 3, 8) / 스킬 변경(Task 10)을 모두
  태스크로 매핑했다. "알려진 트레이드오프"(동시 실행 1개, 재시작 시 추적 끊김)는 Global
  Constraints에 명시하고 별도 구현 태스크를 만들지 않았다(스펙에서도 의도적으로 제외).
- **타입 일관성 확인**: `GridSearchJob`(Task 5)의 필드명이 `engine/cache.py`의
  `_row_to_grid_search_job_dict`(Task 1) 반환 dict 키, `backend/main.py`의
  `_grid_search_job_response`(Task 4) 출력과 정확히 일치하는지 재확인함(`id`/`market`/
  `timeframe`/`capital`/`start`/`end`/`top_n`/`status`/`total_combos`/`done_combos`/
  `started_at`/`finished_at`/`elapsed_sec`/`error_message`/`result_json`).
- **플레이스홀더 스캔**: "TBD"/"나중에" 등 미완성 표현 없음. 모든 코드 스텝에 실제 동작하는
  전체 코드를 포함시켰다(부분 diff가 필요한 곳은 정확한 "이 블록을 저 블록으로 교체" 지시로
  대체).
- **fixture 재사용 확인**: Task 3의 `_FakePopen`/`_SyncThread`/`_NoOpThread`는 Task 2에서 만든
  파서 3개를 실제로 통과시키는 stdout 라인을 그대로 사용해, 파서와 오케스트레이션 사이의
  계약이 테스트 안에서도 어긋나지 않게 했다.
