# 장세 판별 ML 재학습 셀프서비스 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/regime` 탭에 "학습 시작" + "모델 목록/배포" 관리자 패널을 추가해, 지금 터미널에서
수동으로 하던 ML 재학습(`scripts/train_regime_ml.py`)과 AWS 배포(`scripts/
push_regime_ml_model.sh`)를 로컬 웹 UI 버튼으로 온디맨드 실행할 수 있게 한다.

**Architecture:** `backend/grid_search_service.py`가 이미 쓰고 있는 패턴(subprocess +
스레드 + DB job 테이블 + 프론트 폴링)을 그대로 재사용한다. 학습은 항상 로컬에서만
실행되고, 배포는 기존 `push_regime_ml_model.sh`를 특정 모델 파일명 인자로 확장해
호출한다. `ENABLE_ML_TRAINING_UI` 환경변수 게이트로 AWS 배포 환경에서 이 기능
자체가 절대 활성화되지 않도록 이중으로 막는다(엔드포인트 + 프론트 버튼 노출 둘 다).

**Tech Stack:** FastAPI + SQLite(`engine/cache.py`) + subprocess/threading (백엔드),
Next.js 14 + TypeScript + shadcn/ui (프론트), bash (배포 스크립트).

## Global Constraints

- 학습은 항상 로컬에서만 실행한다(AWS `t4g.small` 2GB RAM에서 실행하지 않음) —
  스펙 문서 "배경 및 결정 경위" 참고.
- cron/Task Scheduler 기반 자동화는 만들지 않는다 — 온디맨드 버튼 방식으로 확정됨.
- 증분학습은 구현하지 않는다 — 스크래치 재학습(`scripts/train_regime_ml.py`의
  기존 동작)을 그대로 재사용하고 수정하지 않는다.
- `ENABLE_ML_TRAINING_UI` 플래그가 꺼져 있으면 학습/배포 엔드포인트는 반드시 403을
  반환해야 한다(프론트 버튼 숨김과 별개로 API 자체도 막는 이중 안전장치).
- 새 백엔드 테스트는 반드시 `_client(monkeypatch, tmp_path)` 헬퍼(`tests/
  test_backend.py`에 이미 정의됨)로 `TestClient(app)`를 생성해야 한다 — 이 헬퍼
  없이 `TestClient(app)`를 직접 만드는 테스트가 있으면 `tests/test_backend.py`의
  `test_no_bare_testclient_bypasses_db_isolation`(AST 검사)이 실패한다.
- 프론트엔드에는 테스트 러너가 없다(Jest/RTL 등 미설치, `frontend/package.json`
  확인됨) — 프론트 변경은 자동 테스트 대신 로컬 dev 서버로 브라우저에서 직접
  확인한다.

---

## 파일 구조 개요

| 파일 | 역할 |
|---|---|
| `engine/cache.py` | `regime_ml_jobs` 테이블 + CRUD 추가(수정) |
| `backend/regime_ml_training_service.py` | 학습 job 오케스트레이션(신규) |
| `backend/regime_ml_service.py` | 모델 목록/배포 마커/배포 실행 함수 추가(수정) |
| `scripts/push_regime_ml_model.sh` | 특정 모델 파일명 인자 지원(수정) |
| `backend/main.py` | 5개 신규 엔드포인트 + orphan 정리 startup 훅(수정) |
| `.env.example` | `ENABLE_ML_TRAINING_UI` 안내 주석 추가(수정) |
| `deploy/README.md` | AWS에서 이 플래그를 켜지 말라는 경고 추가(수정) |
| `frontend/lib/types/eda.ts` | `RegimeMlJob`/`RegimeMlModelSummary` 타입 추가(수정) |
| `frontend/lib/api/eda.ts` | 신규 API 함수 5개 추가(수정) |
| `frontend/components/RegimeMlAdminPanel.tsx` | 학습/배포 관리자 패널(신규) |
| `frontend/components/RegimeDashboard.tsx` | 관리자 패널 마운트(수정) |
| `tests/test_cache.py` | `regime_ml_jobs` CRUD 테스트(수정) |
| `tests/test_regime_ml_training_service.py` | job 오케스트레이션 테스트(신규) |
| `tests/test_regime_ml_service.py` | 모델 목록/배포 함수 테스트(수정) |
| `tests/test_backend.py` | 신규 엔드포인트 5개 테스트(수정) |

---

### Task 1: `regime_ml_jobs` DB 테이블 + CRUD

**Files:**
- Modify: `engine/cache.py` (스키마 블록 및 함수 추가, 파일 끝에 추가)
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `create_regime_ml_job(job_id: str) -> None`,
  `finish_regime_ml_job(job_id: str, status: str, error_message: str | None = None) -> None`,
  `get_regime_ml_job(job_id: str) -> dict | None`, `list_regime_ml_jobs() -> list[dict]`.
  각 dict는 `{"id": str, "status": str, "started_at": str, "finished_at": str | None,
  "error_message": str | None}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cache.py` 파일 끝에 추가:

```python
def test_create_and_get_regime_ml_job(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, get_regime_ml_job

    create_regime_ml_job("job-1")
    job = get_regime_ml_job("job-1")

    assert job["id"] == "job-1"
    assert job["status"] == "running"
    assert job["finished_at"] is None
    assert job["error_message"] is None


def test_finish_regime_ml_job_updates_status_and_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, finish_regime_ml_job, get_regime_ml_job

    create_regime_ml_job("job-1")
    finish_regime_ml_job("job-1", status="failed", error_message="boom")

    job = get_regime_ml_job("job-1")
    assert job["status"] == "failed"
    assert job["error_message"] == "boom"
    assert job["finished_at"] is not None


def test_get_regime_ml_job_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import get_regime_ml_job

    assert get_regime_ml_job("does-not-exist") is None


def test_list_regime_ml_jobs_orders_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, list_regime_ml_jobs

    create_regime_ml_job("job-1")
    create_regime_ml_job("job-2")

    jobs = list_regime_ml_jobs()
    assert [j["id"] for j in jobs] == ["job-2", "job-1"]
```

`tests/test_cache.py` 파일 맨 위에 이미 `import engine.cache as cache_module`가 있는지
확인하고, 없으면 다른 기존 테스트가 쓰는 import 문을 그대로 따른다(이 파일은 이미
`engine.cache`의 다른 함수들을 테스트하고 있으므로 fixture/import 스타일을 그대로
따르면 된다).

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_cache.py -k regime_ml_job -v`
Expected: FAIL with `ImportError: cannot import name 'create_regime_ml_job'`

- [ ] **Step 3: 스키마 + CRUD 구현**

`engine/cache.py`에서 `_SCHEMA += """..."""` 블록들이 나열된 곳(마지막
`_SCHEMA += """..."""` 블록, `trend_segments` 테이블 정의 다음) 뒤에 추가:

```python
_SCHEMA += """
CREATE TABLE IF NOT EXISTS regime_ml_jobs (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    error_message TEXT
);
"""
```

파일 끝(마지막 함수 뒤)에 추가:

```python
def create_regime_ml_job(job_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO regime_ml_jobs (id, status, started_at) "
            "VALUES (?, 'running', datetime('now'))",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


def finish_regime_ml_job(job_id: str, status: str, error_message: str | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE regime_ml_jobs "
            "SET status = ?, finished_at = datetime('now'), error_message = ? "
            "WHERE id = ?",
            (status, error_message, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_regime_ml_job_dict(row: tuple) -> dict:
    job_id, status, started_at, finished_at, error_message = row
    return {
        "id": job_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "error_message": error_message,
    }


def get_regime_ml_job(job_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, status, started_at, finished_at, error_message "
            "FROM regime_ml_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_regime_ml_job_dict(row) if row else None


def list_regime_ml_jobs() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, status, started_at, finished_at, error_message "
            "FROM regime_ml_jobs ORDER BY started_at DESC, rowid DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_regime_ml_job_dict(r) for r in rows]
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_cache.py -k regime_ml_job -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: regime_ml_jobs 테이블 + CRUD 추가"
```

---

### Task 2: 학습 job 오케스트레이션 서비스

**Files:**
- Create: `backend/regime_ml_training_service.py`
- Test: `tests/test_regime_ml_training_service.py`

**Interfaces:**
- Consumes: `create_regime_ml_job(job_id: str) -> None`,
  `finish_regime_ml_job(job_id, status, error_message=None) -> None` (Task 1)
- Produces: `start_job() -> str`(job_id 반환), `JobAlreadyRunningError` 예외 클래스.
  모듈 레벨 `_active: dict | None`, `_lock: threading.Lock`(테스트에서 monkeypatch
  대상).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_training_service.py` 신규 생성:

```python
import pytest

import backend.regime_ml_training_service as rmts
import engine.cache as cache_module
from engine.cache import get_regime_ml_job


class _SyncThread:
    """threading.Thread를 흉내내되 start()가 target을 즉시(동기) 실행한다."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class _NoOpThread:
    """target을 저장만 하고 실행하지 않는다 — "아직 실행 중"인 상황을 테스트할 때 쓴다."""

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
        self.stdout = iter(type(self).stdout_lines)
        self.returncode = type(self).returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


@pytest.fixture(autouse=True)
def _reset_regime_ml_training_service_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(rmts, "_active", None)
    monkeypatch.setattr(rmts.threading, "Thread", _SyncThread)
    monkeypatch.setattr(rmts.subprocess, "Popen", _FakePopen)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0


def test_start_job_completes_when_script_exits_zero():
    _FakePopen.stdout_lines = ["[fold 0] ...\n", "학습 완료\n"]
    _FakePopen.returncode = 0

    job_id = rmts.start_job()

    job = get_regime_ml_job(job_id)
    assert job["status"] == "completed"
    assert rmts._active is None


def test_start_job_marks_failed_when_script_exits_nonzero():
    _FakePopen.stdout_lines = ["Traceback (most recent call last):\n", "ValueError: 캔들 데이터가 없습니다\n"]
    _FakePopen.returncode = 1

    job_id = rmts.start_job()

    job = get_regime_ml_job(job_id)
    assert job["status"] == "failed"
    assert "ValueError" in job["error_message"]


def test_start_job_raises_when_already_running(monkeypatch):
    monkeypatch.setattr(rmts.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    rmts.start_job()

    with pytest.raises(rmts.JobAlreadyRunningError):
        rmts.start_job()


def test_start_job_marks_failed_when_popen_itself_raises(monkeypatch):
    def _raise_popen(*args, **kwargs):
        raise OSError("no such file or directory")

    monkeypatch.setattr(rmts.subprocess, "Popen", _raise_popen)

    with pytest.raises(OSError):
        rmts.start_job()

    from engine.cache import list_regime_ml_jobs
    jobs = list_regime_ml_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "failed"
    assert "학습 실행 실패" in jobs[0]["error_message"]
    assert rmts._active is None


def test_start_job_spawns_script_with_required_env(monkeypatch):
    monkeypatch.setattr(rmts.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    rmts.start_job()

    proc = rmts._active["proc"]
    assert proc.args[0] == rmts.sys.executable
    assert proc.args[1] == "scripts/train_regime_ml.py"
    assert proc.kwargs["env"]["PYTHONPATH"] == str(rmts.REPO_ROOT)
    assert proc.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert proc.kwargs["cwd"] == str(rmts.REPO_ROOT)
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_training_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.regime_ml_training_service'`

- [ ] **Step 3: 서비스 모듈 구현**

`backend/regime_ml_training_service.py` 신규 생성:

```python
"""
backend/regime_ml_training_service.py

scripts/train_regime_ml.py를 서브프로세스로 실행하고 상태를 engine.cache의
regime_ml_jobs 테이블에 기록하는 오케스트레이션 레이어.
backend/grid_search_service.py와 같은 단일 슬롯(_active) + 스레드 리더 패턴을
쓰되, 이 스크립트는 진행률/결과 JSON을 stdout에 구조화해서 찍지 않고 전체 실행이
2.5분 내외로 짧아(로컬 실측치) 세부 진행률 파싱은 하지 않는다 — running/completed/
failed 상태만 추적한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from engine.cache import create_regime_ml_job, finish_regime_ml_job

REPO_ROOT = Path(__file__).resolve().parent.parent


class JobAlreadyRunningError(Exception):
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"이미 실행 중인 재학습 job이 있습니다: {job_id}")


_lock = threading.Lock()
_active: dict | None = None  # {"job_id": str, "proc": Popen}


def _reader_loop(job_id: str, proc) -> None:
    """서브프로세스 stdout을 끝까지 읽어(콘솔 로그 소비 목적) 마지막 비어있지 않은
    줄만 실패 시 진단용으로 저장한다. grid_search_service._reader_loop와 같은 이유로
    예외가 나도 _active를 반드시 비운다 — 안 그러면 단일 실행 슬롯이 영구히 막힌다."""
    global _active
    last_line: str | None = None
    unexpected_error: str | None = None

    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n").strip()
            if line:
                last_line = line
        proc.wait()
    except Exception as exc:
        unexpected_error = f"학습 진행 중 예외 발생: {exc}"
        try:
            proc.terminate()
        except Exception:
            pass

    try:
        if unexpected_error is not None:
            finish_regime_ml_job(job_id, status="failed", error_message=unexpected_error)
        elif proc.returncode == 0:
            finish_regime_ml_job(job_id, status="completed")
        else:
            message = last_line or f"학습이 종료 코드 {proc.returncode}로 실패했습니다."
            finish_regime_ml_job(job_id, status="failed", error_message=message)
    finally:
        with _lock:
            if _active is not None and _active["job_id"] == job_id:
                _active = None


def start_job() -> str:
    """재학습 job을 시작하고 job_id를 반환한다. 이미 실행 중인 job이 있으면
    JobAlreadyRunningError를 던진다."""
    global _active
    with _lock:
        if _active is not None:
            raise JobAlreadyRunningError(_active["job_id"])

        job_id = uuid.uuid4().hex
        create_regime_ml_job(job_id)

        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        try:
            proc = subprocess.Popen(
                [sys.executable, "scripts/train_regime_ml.py"],
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            finish_regime_ml_job(job_id, status="failed", error_message=f"학습 실행 실패: {exc}")
            raise

        _active = {"job_id": job_id, "proc": proc}

    # thread.start()는 반드시 `with _lock:` 블록 밖에서 호출한다(grid_search_service와
    # 같은 이유 — 테스트의 동기 실행 더블이 .start() 안에서 _reader_loop를 즉시
    # 실행하는데, _reader_loop도 끝에서 같은 락을 잡는다).
    thread = threading.Thread(target=_reader_loop, args=(job_id, proc), daemon=True)
    thread.start()

    return job_id
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_training_service.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add backend/regime_ml_training_service.py tests/test_regime_ml_training_service.py
git commit -m "feat: 장세 ML 재학습 job 오케스트레이션 서비스 추가"
```

---

### Task 3: 학습 관련 엔드포인트 3개 + orphan job 정리

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `start_job() -> str`, `JobAlreadyRunningError`(Task 2),
  `create_regime_ml_job`/`finish_regime_ml_job`/`get_regime_ml_job`/
  `list_regime_ml_jobs`(Task 1)
- Produces: `GET /api/v1/regime/ml-train-enabled` -> `{"enabled": bool}`,
  `POST /api/v1/regime/ml-train` -> job dict(`_regime_ml_job_response` 형태),
  `GET /api/v1/regime/ml-train/jobs` -> job dict 목록. 헬퍼 함수
  `_ml_training_ui_enabled() -> bool`, `_regime_ml_job_response(job: dict) -> dict`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_ml_train_enabled_defaults_to_false(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ML_TRAINING_UI", raising=False)

    resp = client.get("/api/v1/regime/ml-train-enabled")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_ml_train_enabled_true_when_env_set(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    resp = client.get("/api/v1/regime/ml-train-enabled")
    assert resp.json() == {"enabled": True}


def test_start_regime_ml_train_job_rejects_when_flag_disabled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ML_TRAINING_UI", raising=False)

    resp = client.post("/api/v1/regime/ml-train")
    assert resp.status_code == 403


def test_start_regime_ml_train_job_returns_running_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")
    monkeypatch.setattr(backend_module, "start_regime_ml_training_job", lambda: "job-1")

    from engine.cache import create_regime_ml_job
    create_regime_ml_job("job-1")

    resp = client.post("/api/v1/regime/ml-train")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "job-1"
    assert body["status"] == "running"


def test_start_regime_ml_train_job_returns_409_when_already_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    def _raise():
        raise backend_module.RegimeMlJobAlreadyRunningError("job-existing")

    monkeypatch.setattr(backend_module, "start_regime_ml_training_job", _raise)

    resp = client.post("/api/v1/regime/ml-train")
    assert resp.status_code == 409


def test_list_regime_ml_train_jobs_returns_saved_jobs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_regime_ml_job
    create_regime_ml_job("job-1")

    resp = client.get("/api/v1/regime/ml-train/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "job-1"


def test_startup_fails_orphaned_running_regime_ml_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    from engine.cache import create_regime_ml_job, get_regime_ml_job
    create_regime_ml_job("orphan-1")

    backend_module._fail_orphaned_regime_ml_jobs()

    job = get_regime_ml_job("orphan-1")
    assert job["status"] == "failed"
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k regime_ml_train -v`
Expected: FAIL with `AttributeError` (엔드포인트가 아직 없어 404) 또는
`ImportError`

- [ ] **Step 3: 엔드포인트 구현**

`backend/main.py` 상단 import 블록의
```python
from backend.grid_search_service import (
    JobAlreadyRunningError,
    JobNotActiveError,
    cancel_job,
    reset_active_job,
    start_job,
)
from backend.regime_ml_service import predict_current_ml_regime
```
을 아래로 교체:
```python
from backend.grid_search_service import (
    JobAlreadyRunningError,
    JobNotActiveError,
    cancel_job,
    reset_active_job,
    start_job,
)
from backend.regime_ml_service import predict_current_ml_regime
from backend.regime_ml_training_service import (
    JobAlreadyRunningError as RegimeMlJobAlreadyRunningError,
    start_job as start_regime_ml_training_job,
)
```

`engine.cache`의 기존 import 목록(`delete_backtest_run,` 부터 시작하는 블록)에
아래 네 개를 알파벳 순서에 맞춰 추가:
```python
    create_regime_ml_job,
    finish_regime_ml_job,
    get_regime_ml_job,
    list_regime_ml_jobs,
```
(전체 import 블록은 이미 알파벳순으로 정렬돼 있으므로 그 순서를 유지한다.)

`_fail_orphaned_grid_search_jobs()`/`_cleanup_orphaned_grid_search_jobs()` 정의
바로 다음(`INDICATOR_CATALOG: list[dict] = [` 줄 앞)에 추가:

```python
def _ml_training_ui_enabled() -> bool:
    """ENABLE_ML_TRAINING_UI가 로컬 .env에만 true로 설정되어 있어야 한다 — AWS에
    실수로 재학습이 실행되는 사고(과거 grid search가 실제로 겪은 OOM 사고와 같은
    유형)를 막기 위한 게이트. _resolve_allowed_origin()과 같은 이유로 빈 문자열도
    미설정과 동일하게 취급한다."""
    return (os.environ.get("ENABLE_ML_TRAINING_UI") or "").strip().lower() == "true"


def _fail_orphaned_regime_ml_jobs() -> None:
    """백엔드가 재시작되면 서브프로세스 stdout 리더 스레드도 함께 사라진다 —
    재기동 시 남아 있는 running 행은 추적 불가능한 고아이므로 실패로 정리한다."""
    for job in list_regime_ml_jobs():
        if job["status"] == "running":
            finish_regime_ml_job(
                job["id"], status="failed",
                error_message="백엔드가 재시작되어 진행률 추적이 끊겼습니다. 모델 목록에서 결과를 확인하세요.",
            )


@app.on_event("startup")
def _cleanup_orphaned_regime_ml_jobs() -> None:
    _fail_orphaned_regime_ml_jobs()


def _regime_ml_job_response(job: dict) -> dict:
    return {
        **job,
        "started_at": _to_utc_iso(job["started_at"]),
        "finished_at": _to_utc_iso(job["finished_at"]) if job["finished_at"] else None,
    }
```

`@app.get("/api/v1/regime/ml-current-prediction")` 엔드포인트 정의 바로 다음에
추가:

```python
@app.get("/api/v1/regime/ml-train-enabled")
def get_regime_ml_train_enabled_endpoint() -> dict:
    return {"enabled": _ml_training_ui_enabled()}


@app.post("/api/v1/regime/ml-train")
def start_regime_ml_train_job_endpoint() -> dict:
    if not _ml_training_ui_enabled():
        raise HTTPException(status_code=403, detail="이 환경에서는 ML 재학습이 비활성화되어 있습니다.")
    try:
        job_id = start_regime_ml_training_job()
    except RegimeMlJobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = get_regime_ml_job(job_id)
    assert job is not None
    return _regime_ml_job_response(job)


@app.get("/api/v1/regime/ml-train/jobs")
def list_regime_ml_train_jobs_endpoint() -> list[dict]:
    return [_regime_ml_job_response(j) for j in list_regime_ml_jobs()]
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k regime_ml_train -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 전체 백엔드 스위트 회귀 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -v`
Expected: 모두 PASS(기존 테스트 회귀 없음, 특히
`test_no_bare_testclient_bypasses_db_isolation`)

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: ML 재학습 시작/조회 엔드포인트 + 환경변수 게이트 추가"
```

---

### Task 4: `push_regime_ml_model.sh` 특정 모델 지정 배포 지원

**Files:**
- Modify: `scripts/push_regime_ml_model.sh`

**Interfaces:**
- Consumes: 없음(독립 스크립트)
- Produces: `bash scripts/push_regime_ml_model.sh [model_basename]` — 인자 없으면
  기존과 동일(최신 모델), 인자가 있으면 해당 베이스네임(예:
  `regime_ml_20260827T223633Z`)의 `.txt`+`.json` 페어를 사용. 파일이 없으면
  exit 1 + stderr 메시지.

- [ ] **Step 1: 기존 로직을 조건부로 교체**

`scripts/push_regime_ml_model.sh`에서 아래 블록:

```bash
if [ -d "$LOCAL_MODEL_DIR" ]; then
    LOCAL_TXT="$(find "$LOCAL_MODEL_DIR" -maxdepth 1 -name 'regime_ml_*.txt' | sort | tail -n 1)"
else
    LOCAL_TXT=""
fi

if [ -z "$LOCAL_TXT" ]; then
    echo "옮길 ML 모델이 없습니다: $LOCAL_MODEL_DIR" >&2
    echo "먼저 scripts/train_regime_ml.py를 실행해 모델을 학습하세요." >&2
    exit 1
fi
```

을 아래로 교체:

```bash
# 첫 번째 인자로 특정 모델 베이스네임(예: regime_ml_20260827T223633Z)을 지정하면
# 그 모델을 배포한다(재학습 셀프서비스 UI가 과거 학습 이력 중 골라 배포할 때 사용).
# 인자가 없으면 기존과 동일하게 가장 최신 모델을 찾는다.
if [ -n "${1:-}" ]; then
    LOCAL_TXT="$LOCAL_MODEL_DIR/$1.txt"
    if [ ! -f "$LOCAL_TXT" ]; then
        echo "지정한 모델을 찾을 수 없습니다: $LOCAL_TXT" >&2
        exit 1
    fi
else
    if [ -d "$LOCAL_MODEL_DIR" ]; then
        LOCAL_TXT="$(find "$LOCAL_MODEL_DIR" -maxdepth 1 -name 'regime_ml_*.txt' | sort | tail -n 1)"
    else
        LOCAL_TXT=""
    fi

    if [ -z "$LOCAL_TXT" ]; then
        echo "옮길 ML 모델이 없습니다: $LOCAL_MODEL_DIR" >&2
        echo "먼저 scripts/train_regime_ml.py를 실행해 모델을 학습하세요." >&2
        exit 1
    fi
fi
```

파일 상단 주석(2~6번째 줄)의 "가장 최신 .txt+.json 페어를" 설명에 "인자로 특정
모델을 지정할 수도 있다"는 문장을 덧붙인다.

- [ ] **Step 2: 네트워크 없이 인자 해석 로직 수동 검증**

실제 SSH/scp를 타지 않도록 `ssh`/`scp`를 가짜 실행 파일로 치환해 로컬에서
안전하게 전체 스크립트를 실행해본다(실제 AWS 서버에 아무것도 전송되지 않음):

```bash
mkdir -p /tmp/fake_bin
cat > /tmp/fake_bin/ssh <<'EOF'
#!/usr/bin/env bash
echo "[fake ssh] $*"
exit 0
EOF
cat > /tmp/fake_bin/scp <<'EOF'
#!/usr/bin/env bash
echo "[fake scp] $*"
exit 0
EOF
chmod +x /tmp/fake_bin/ssh /tmp/fake_bin/scp
```

이미 로컬에 학습된 모델이 `data/regime_ml_models/`에 여러 개 있어야 한다(없으면
`PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`를 먼저
실행). 존재하는 모델 파일명 중 최신이 아닌 것 하나를 골라(예: `ls data/
regime_ml_models/*.txt`로 확인) `<older_basename>`에 대입:

```bash
PATH="/tmp/fake_bin:$PATH" DEPLOY_SSH_KEY_PATH=/fake/key.pem DEPLOY_SERVER_HOST=fake@host \
  bash scripts/push_regime_ml_model.sh <older_basename>
```

Expected: `[fake ssh] ... mkdir -p ...` 와 `[fake scp] ... <older_basename>.txt
<older_basename>.json fake@host:...` 가 출력되고 "모델 전송 완료:
<older_basename>.txt" 로 끝난다(최신이 아니라 지정한 그 파일이 전송 대상으로
잡혔는지 출력에서 확인).

인자 없이 실행하면 기존과 동일하게 가장 최신 파일이 잡히는지도 확인:
```bash
PATH="/tmp/fake_bin:$PATH" DEPLOY_SSH_KEY_PATH=/fake/key.pem DEPLOY_SERVER_HOST=fake@host \
  bash scripts/push_regime_ml_model.sh
```
Expected: 최신 타임스탬프 파일명이 전송 대상으로 잡힘.

존재하지 않는 베이스네임을 넘겼을 때 에러로 종료하는지도 확인:
```bash
PATH="/tmp/fake_bin:$PATH" DEPLOY_SSH_KEY_PATH=/fake/key.pem DEPLOY_SERVER_HOST=fake@host \
  bash scripts/push_regime_ml_model.sh regime_ml_00000101T000000Z
echo "exit code: $?"
```
Expected: "지정한 모델을 찾을 수 없습니다" stderr 메시지 + `exit code: 1`

- [ ] **Step 3: 커밋**

```bash
git add scripts/push_regime_ml_model.sh
git commit -m "feat: push_regime_ml_model.sh가 특정 모델 배포를 지원하도록 확장"
```

---

### Task 5: 모델 목록 / 마지막 배포 마커 / 배포 실행 함수

**Files:**
- Modify: `backend/regime_ml_service.py`
- Test: `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: `scripts/push_regime_ml_model.sh <model_basename>`(Task 4, subprocess로
  호출)
- Produces: `list_trained_models() -> list[dict]`(각 dict:
  `{"model_timestamp": str, "trained_at": str, "performance": dict | None,
  "is_deployed": bool}`), `deploy_model(model_timestamp: str) -> None`
  (실패 시 `FileNotFoundError` 또는 `RuntimeError`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_service.py` 파일 끝에 추가(파일 상단에 이미 있는
`_train_and_save_tiny_model` 헬퍼를 재사용):

```python
def test_list_trained_models_returns_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path / "does_not_exist")
    assert regime_ml_service.list_trained_models() == []


def test_list_trained_models_orders_newest_first_and_marks_deployed(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z", performance={
        "folds": [], "pooled_correlation": 0.05,
        "pooled_hit_rate": {label: None for label in _LABELS},
    })
    _train_and_save_tiny_model(tmp_path, "20260102T000000Z", performance={
        "folds": [], "pooled_correlation": 0.08,
        "pooled_hit_rate": {label: None for label in _LABELS},
    })
    regime_ml_service.set_last_deployed_marker("regime_ml_20260101T000000Z")

    models = regime_ml_service.list_trained_models()

    assert [m["model_timestamp"] for m in models] == [
        "regime_ml_20260102T000000Z", "regime_ml_20260101T000000Z",
    ]
    assert models[0]["performance"]["pooled_correlation"] == 0.08
    assert models[0]["is_deployed"] is False
    assert models[1]["is_deployed"] is True


def test_list_trained_models_skips_incomplete_pairs(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "regime_ml_20260101T000000Z.json").write_text('{"performance": null}', encoding="utf-8")
    # .txt 짝이 없음 — 불완전한 저장으로 취급해 건너뛴다.

    assert regime_ml_service.list_trained_models() == []


def test_get_last_deployed_marker_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    assert regime_ml_service.get_last_deployed_marker() is None


def test_set_last_deployed_marker_persists_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    regime_ml_service.set_last_deployed_marker("regime_ml_20260101T000000Z")

    marker = regime_ml_service.get_last_deployed_marker()
    assert marker["model_timestamp"] == "regime_ml_20260101T000000Z"
    assert "deployed_at" in marker


def test_deploy_model_raises_file_not_found_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        regime_ml_service.deploy_model("regime_ml_20260101T000000Z")


def test_deploy_model_runs_push_script_and_sets_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z")

    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = "모델 전송 완료"
        stderr = ""

    def _fake_run(args, **kwargs):
        captured["args"] = args
        return _FakeResult()

    monkeypatch.setattr(regime_ml_service.subprocess, "run", _fake_run)

    regime_ml_service.deploy_model("regime_ml_20260101T000000Z")

    assert captured["args"] == [
        "bash", str(regime_ml_service.REPO_ROOT / "scripts" / "push_regime_ml_model.sh"),
        "regime_ml_20260101T000000Z",
    ]
    marker = regime_ml_service.get_last_deployed_marker()
    assert marker["model_timestamp"] == "regime_ml_20260101T000000Z"


def test_deploy_model_raises_runtime_error_when_script_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260101T000000Z")

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "DEPLOY_SSH_KEY_PATH가 설정되어 있지 않습니다."

    monkeypatch.setattr(regime_ml_service.subprocess, "run", lambda args, **kwargs: _FakeResult())

    with pytest.raises(RuntimeError, match="DEPLOY_SSH_KEY_PATH"):
        regime_ml_service.deploy_model("regime_ml_20260101T000000Z")

    assert regime_ml_service.get_last_deployed_marker() is None
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_service.py -k "trained_models or last_deployed or deploy_model" -v`
Expected: FAIL with `AttributeError: module 'backend.regime_ml_service' has no
attribute 'list_trained_models'`

- [ ] **Step 3: 함수 구현**

`backend/regime_ml_service.py` 상단 import에 `subprocess` 추가:

```python
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
```

`MODEL_DIR = Path(__file__).parent.parent / "data" / "regime_ml_models"` 바로
다음 줄에 추가:

```python
REPO_ROOT = Path(__file__).resolve().parent.parent
```

파일 끝(`predict_current_ml_regime` 함수 뒤)에 추가:

```python
def get_last_deployed_marker() -> dict | None:
    """가장 최근에 배포에 성공한 모델의 타임스탬프를 담은 로컬 마커. 참고용
    표시일 뿐 신뢰 소스는 아니다(예: AWS에서 수동으로 모델을 되돌리면 이 마커와
    실제 배포 상태가 어긋날 수 있다 — 그런 동기화까지는 비범위)."""
    marker_path = MODEL_DIR / ".last_deployed.json"
    if not marker_path.exists():
        return None
    return json.loads(marker_path.read_text(encoding="utf-8"))


def set_last_deployed_marker(model_timestamp: str) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = MODEL_DIR / ".last_deployed.json"
    payload = {
        "model_timestamp": model_timestamp,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_trained_models() -> list[dict]:
    """data/regime_ml_models/의 모든 학습 이력을 최신순으로 반환한다. .json
    사이드카가 있는데 .txt 짝이 없는 항목(불완전한 저장, find_latest_model()과
    같은 기준)은 건너뛴다."""
    if not MODEL_DIR.exists():
        return []

    deployed_marker = get_last_deployed_marker()
    deployed_timestamp = deployed_marker["model_timestamp"] if deployed_marker else None

    models: list[dict] = []
    json_files = sorted(MODEL_DIR.glob("regime_ml_*.json"), reverse=True)
    for json_path in json_files:
        txt_path = json_path.with_suffix(".txt")
        if not txt_path.exists():
            continue
        sidecar = json.loads(json_path.read_text(encoding="utf-8"))
        model_timestamp = json_path.stem
        models.append({
            "model_timestamp": model_timestamp,
            "trained_at": _parse_trained_at(txt_path),
            "performance": sidecar.get("performance"),
            "is_deployed": model_timestamp == deployed_timestamp,
        })
    return models


def deploy_model(model_timestamp: str) -> None:
    """model_timestamp(예: "regime_ml_20260827T223633Z")에 해당하는 모델을
    scripts/push_regime_ml_model.sh로 AWS 라이브 서버에 배포한다. 성공하면
    마지막 배포 마커를 갱신한다."""
    txt_path = MODEL_DIR / f"{model_timestamp}.txt"
    json_path = MODEL_DIR / f"{model_timestamp}.json"
    if not txt_path.exists() or not json_path.exists():
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {model_timestamp}")

    script_path = REPO_ROOT / "scripts" / "push_regime_ml_model.sh"
    result = subprocess.run(
        ["bash", str(script_path), model_timestamp],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "배포 스크립트 실행 실패")

    set_last_deployed_marker(model_timestamp)
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_service.py -v`
Expected: 모두 PASS(기존 테스트 포함 회귀 없음)

- [ ] **Step 5: 커밋**

```bash
git add backend/regime_ml_service.py tests/test_regime_ml_service.py
git commit -m "feat: ML 모델 목록/배포 마커/배포 실행 함수 추가"
```

---

### Task 6: 모델 목록 / 배포 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `list_trained_models() -> list[dict]`, `deploy_model(model_timestamp:
  str) -> None`(Task 5), `_ml_training_ui_enabled() -> bool`(Task 3)
- Produces: `GET /api/v1/regime/ml-models` -> list[dict], `POST /api/v1/regime/
  ml-deploy` (body `{"model_timestamp": str}`) -> `{"deployed": true,
  "model_timestamp": str}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가:

```python
def test_list_regime_ml_models_endpoint_returns_models(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        backend_module, "list_trained_models",
        lambda: [{"model_timestamp": "regime_ml_1", "trained_at": "2026-01-01T00:00:00+00:00",
                  "performance": None, "is_deployed": True}],
    )

    resp = client.get("/api/v1/regime/ml-models")
    assert resp.status_code == 200
    assert resp.json()[0]["model_timestamp"] == "regime_ml_1"


def test_deploy_regime_ml_model_rejects_when_flag_disabled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ML_TRAINING_UI", raising=False)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_1"})
    assert resp.status_code == 403


def test_deploy_regime_ml_model_returns_404_when_model_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    def _raise(model_timestamp):
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {model_timestamp}")

    monkeypatch.setattr(backend_module, "deploy_model", _raise)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_missing"})
    assert resp.status_code == 404


def test_deploy_regime_ml_model_returns_500_when_script_fails(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    def _raise(model_timestamp):
        raise RuntimeError("scp 실패")

    monkeypatch.setattr(backend_module, "deploy_model", _raise)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_1"})
    assert resp.status_code == 500
    assert "scp 실패" in resp.json()["detail"]


def test_deploy_regime_ml_model_succeeds(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")
    monkeypatch.setattr(backend_module, "deploy_model", lambda model_timestamp: None)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_1"})
    assert resp.status_code == 200
    assert resp.json() == {"deployed": True, "model_timestamp": "regime_ml_1"}
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k "regime_ml_models or deploy_regime_ml" -v`
Expected: FAIL (엔드포인트 없음, 404)

- [ ] **Step 3: 엔드포인트 구현**

`backend/main.py`의
```python
from backend.regime_ml_service import predict_current_ml_regime
```
을 아래로 교체:
```python
from backend.regime_ml_service import (
    deploy_model,
    list_trained_models,
    predict_current_ml_regime,
)
```

`list_regime_ml_train_jobs_endpoint` 정의 바로 다음에 추가:

```python
@app.get("/api/v1/regime/ml-models")
def list_regime_ml_models_endpoint() -> list[dict]:
    return list_trained_models()


class DeployRegimeMlModelRequest(BaseModel):
    model_timestamp: str


@app.post("/api/v1/regime/ml-deploy")
def deploy_regime_ml_model_endpoint(req: DeployRegimeMlModelRequest) -> dict:
    if not _ml_training_ui_enabled():
        raise HTTPException(status_code=403, detail="이 환경에서는 ML 모델 배포가 비활성화되어 있습니다.")
    try:
        deploy_model(req.model_timestamp)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"deployed": True, "model_timestamp": req.model_timestamp}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_backend.py -k "regime_ml_models or deploy_regime_ml" -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 전체 백엔드 스위트 회귀 확인**

Run: `PYTHONPATH=. python -m pytest tests/ -v`
Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: ML 모델 목록/배포 엔드포인트 추가"
```

---

### Task 7: 환경변수 게이트 문서화

**Files:**
- Modify: `.env.example`
- Modify: `deploy/README.md`

**Interfaces:** 없음(설정/문서 전용, 코드 인터페이스 없음)

- [ ] **Step 1: `.env.example`에 안내 주석 추가**

`.env.example` 파일 끝에 추가:

```
# 로컬 대시보드(/regime 탭)에서 ML 재학습/배포 버튼을 활성화한다. 로컬 개발
# 환경에서만 true로 설정할 것 — AWS 등 배포 환경에서는 절대 켜지 않는다(학습이
# t4g.small 2GB RAM에서 실행되면 daemon/backend와 리소스가 충돌할 위험이 있고,
# grid search가 과거 실제로 이 유형의 OOM 사고를 겪었다). 이 줄은 AWS .env에는
# 추가하지 않는다.
# ENABLE_ML_TRAINING_UI=true
```

- [ ] **Step 2: `deploy/README.md`에 경고 추가**

`deploy/README.md`의 "## 2. SSH 접속 및 배포 스크립트 실행" 섹션에서 `.env`
설정을 다루는 부분(`.env.example`을 복사하는 안내가 있는 지점) 근처에 아래 문단을
추가한다(정확한 삽입 위치는 파일을 열어 `.env` 관련 안내가 나오는 첫 지점 뒤로
정한다):

```markdown
**주의**: `.env`에 `ENABLE_ML_TRAINING_UI=true`를 추가하지 않는다. 이 플래그는
로컬 개발 환경에서 `/regime` 탭의 ML 재학습/배포 버튼을 켜는 용도이며, AWS
서버에서 켜면 재학습(3마켓 walk-forward)이 daemon/backend와 함께 2GB RAM을
두고 경합해 OOM 위험이 있다(grid search가 과거 겪은 것과 같은 유형의 사고,
`docs/regime-ml-backlog.md` 참고). 학습은 항상 로컬 PC에서만 실행하고,
결과 모델만 `scripts/push_regime_ml_model.sh`로 서버에 올린다.
```

- [ ] **Step 3: 커밋**

```bash
git add .env.example deploy/README.md
git commit -m "docs: ENABLE_ML_TRAINING_UI는 로컬 전용이라는 경고 추가"
```

---

### Task 8: 프론트 타입 + API 클라이언트 함수

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Produces: 타입 `RegimeMlJob`, `RegimeMlModelSummary`. 함수
  `getRegimeMlTrainEnabled(): Promise<{ enabled: boolean }>`,
  `startRegimeMlTrainJob(): Promise<RegimeMlJob>`,
  `getRegimeMlTrainJobs(): Promise<RegimeMlJob[]>`,
  `getRegimeMlModels(): Promise<RegimeMlModelSummary[]>`,
  `deployRegimeMlModel(modelTimestamp: string): Promise<{ deployed: boolean;
  model_timestamp: string }>`.

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`에서 `MlCurrentPrediction` 인터페이스 바로 다음에
추가:

```typescript
export interface RegimeMlJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
}

export interface RegimeMlModelSummary {
  model_timestamp: string;
  trained_at: string;
  performance: MlModelPerformance | null;
  is_deployed: boolean;
}
```

- [ ] **Step 2: API 함수 추가**

`frontend/lib/api/eda.ts` 상단 import 블록의 타입 목록에 `RegimeMlJob`,
`RegimeMlModelSummary`를 알파벳순으로 추가:

```typescript
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  GridSearchEstimate,
  GridSearchIndicatorPoolCatalog,
  GridSearchJob,
  GridSearchJobRequest,
  IndicatorCatalogItem,
  IndicatorPool,
  Market,
  MlCurrentPrediction,
  RegimeMlJob,
  RegimeMlModelSummary,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  TrendSegmentAnalysis,
  ValidateBacktestResponse,
} from '@/lib/types/eda';
```

`getRegimeMlCurrentPrediction` 함수 바로 다음에 추가:

```typescript
export function getRegimeMlTrainEnabled(): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>('/api/v1/regime/ml-train-enabled');
}

export function startRegimeMlTrainJob(): Promise<RegimeMlJob> {
  return apiFetch<RegimeMlJob>('/api/v1/regime/ml-train', { method: 'POST' });
}

export function getRegimeMlTrainJobs(): Promise<RegimeMlJob[]> {
  return apiFetch<RegimeMlJob[]>('/api/v1/regime/ml-train/jobs');
}

export function getRegimeMlModels(): Promise<RegimeMlModelSummary[]> {
  return apiFetch<RegimeMlModelSummary[]>('/api/v1/regime/ml-models');
}

export function deployRegimeMlModel(
  modelTimestamp: string,
): Promise<{ deployed: boolean; model_timestamp: string }> {
  return apiFetch('/api/v1/regime/ml-deploy', {
    method: 'POST',
    body: JSON.stringify({ model_timestamp: modelTimestamp }),
  });
}
```

- [ ] **Step 3: 타입체크로 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(exit code 0)

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: ML 재학습/배포 프론트 타입 + API 클라이언트 함수 추가"
```

---

### Task 9: 관리자 패널 컴포넌트 + `/regime` 탭 마운트

**Files:**
- Create: `frontend/components/RegimeMlAdminPanel.tsx`
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `getRegimeMlTrainEnabled`, `startRegimeMlTrainJob`,
  `getRegimeMlTrainJobs`, `getRegimeMlModels`, `deployRegimeMlModel`(Task 8),
  `useVisiblePolling`(기존 훅), `RegimeMlJob`/`RegimeMlModelSummary`(Task 8)
- Produces: `<RegimeMlAdminPanel />` (props 없음, 내부에서 플래그를 조회해
  꺼져 있으면 `null` 렌더링)

- [ ] **Step 1: 컴포넌트 작성**

`frontend/components/RegimeMlAdminPanel.tsx` 신규 생성:

```tsx
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { ApiError } from '@/lib/api/client';
import {
  deployRegimeMlModel,
  getRegimeMlModels,
  getRegimeMlTrainEnabled,
  getRegimeMlTrainJobs,
  startRegimeMlTrainJob,
} from '@/lib/api/eda';
import { formatDateTime } from '@/lib/format';
import { useVisiblePolling } from '@/lib/hooks/useVisiblePolling';
import type { RegimeMlJob, RegimeMlModelSummary } from '@/lib/types/eda';

const POLL_INTERVAL_MS = 3000;

function formatCorrelation(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : value.toFixed(3);
}

export default function RegimeMlAdminPanel() {
  const [enabled, setEnabled] = useState(false);
  const [jobs, setJobs] = useState<RegimeMlJob[]>([]);
  const [models, setModels] = useState<RegimeMlModelSummary[]>([]);
  const [startError, setStartError] = useState<string | null>(null);
  const [deployTarget, setDeployTarget] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  useEffect(() => {
    getRegimeMlTrainEnabled()
      .then((r) => setEnabled(r.enabled))
      .catch(() => setEnabled(false));
  }, []);

  const refreshJobs = useCallback(async () => {
    try {
      const data = await getRegimeMlTrainJobs();
      setJobs(data);
    } catch {
      // 폴링 실패는 조용히 다음 주기에 재시도한다.
    }
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      const data = await getRegimeMlModels();
      setModels(data);
    } catch {
      // 모델 목록 조회 실패는 조용히 다음 새로고침에 재시도한다.
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    refreshJobs();
    refreshModels();
  }, [enabled, refreshJobs, refreshModels]);

  const runningJob = jobs.find((j) => j.status === 'running') ?? null;
  useVisiblePolling(refreshJobs, POLL_INTERVAL_MS, enabled && runningJob !== null);

  const wasRunningRef = useRef(false);
  useEffect(() => {
    if (wasRunningRef.current && runningJob === null) {
      refreshModels();
    }
    wasRunningRef.current = runningJob !== null;
  }, [runningJob, refreshModels]);

  async function handleStart() {
    setStartError(null);
    try {
      const job = await startRegimeMlTrainJob();
      setJobs((prev) => [job, ...prev]);
    } catch (err) {
      setStartError(err instanceof ApiError ? err.message : '학습 시작 중 오류가 발생했습니다.');
    }
  }

  async function handleConfirmDeploy() {
    if (!deployTarget) return;
    setDeploying(true);
    setDeployError(null);
    try {
      await deployRegimeMlModel(deployTarget);
      setDeployTarget(null);
      await refreshModels();
    } catch (err) {
      setDeployError(err instanceof ApiError ? err.message : '배포 중 오류가 발생했습니다.');
    } finally {
      setDeploying(false);
    }
  }

  if (!enabled) return null;

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">ML 재학습 관리자 패널</h2>
      <div className="mb-4 flex items-center gap-3">
        <Button type="button" size="sm" onClick={handleStart} disabled={runningJob !== null}>
          {runningJob ? '학습 중...' : '학습 시작'}
        </Button>
        {startError && <p className="text-xs text-destructive">{startError}</p>}
      </div>
      {models.length === 0 ? (
        <p className="text-sm text-muted-foreground">학습된 모델이 없습니다.</p>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>학습시각</TableHead>
                <TableHead className="text-right">풀링 상관계수</TableHead>
                <TableHead>상태</TableHead>
                <TableHead className="text-right">배포</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model) => (
                <TableRow key={model.model_timestamp}>
                  <TableCell>{formatDateTime(model.trained_at)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCorrelation(model.performance?.pooled_correlation)}
                  </TableCell>
                  <TableCell>
                    {model.is_deployed && <Badge variant="default">현재 배포됨</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => setDeployTarget(model.model_timestamp)}
                      disabled={model.is_deployed}
                    >
                      배포
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <AlertDialog open={deployTarget !== null} onOpenChange={(open) => { if (!open) { setDeployTarget(null); setDeployError(null); } }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>이 모델을 AWS 라이브로 배포하시겠습니까?</AlertDialogTitle>
            <AlertDialogDescription>
              실거래 대시보드가 참조하는 ML 예측 모델이 즉시 교체됩니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deployError && <p className="text-sm text-destructive">{deployError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmDeploy} disabled={deploying}>
              {deploying ? '배포 중...' : '배포'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
```

- [ ] **Step 2: `RegimeDashboard.tsx`에 마운트**

`frontend/components/RegimeDashboard.tsx`의 import 블록에 추가:

```typescript
import RegimeMlAdminPanel from '@/components/RegimeMlAdminPanel';
```

반환문의 `{market && <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />}`
바로 다음 줄에 추가:

```tsx
      <RegimeMlAdminPanel />
```

(전체 반환문은 이제 `<RegimeMlCurrentPrediction />` 다음에 `<RegimeMlAdminPanel
/>`이 오는 형태가 된다.)

- [ ] **Step 3: 로컬 dev 서버로 브라우저 확인**

`.env`에 `ENABLE_ML_TRAINING_UI=true`가 없는 상태에서 먼저 확인:

Run: 프론트(`cd frontend && npm run dev`)와 백엔드(`PYTHONPATH=.
PYTHONIOENCODING=utf-8 uvicorn backend.main:app --reload --port 8000`)를 각각
띄운 뒤 브라우저로 `http://localhost:3000`의 `/regime` 탭 방문.
Expected: 관리자 패널이 렌더링되지 않음(플래그 꺼짐).

`.env`에 `ENABLE_ML_TRAINING_UI=true`를 추가하고 백엔드를 재시작한 뒤 다시 확인.
Expected:
- 관리자 패널이 보이고 "학습 시작" 버튼이 있다.
- 이미 로컬에 학습된 모델이 있으면(이전 세션에서 실행한 실측 학습으로
  `data/regime_ml_models/`에 파일이 있음) 모델 목록 테이블에 뜬다.
- "배포" 버튼 클릭 시 확인 다이얼로그가 뜬다(**실제로 배포를 눌러 AWS 라이브 모델을
  덮어쓰지 않는다** — 다이얼로그가 뜨고 취소로 닫히는 것까지만 확인하고, 실제
  배포는 사용자가 원할 때 직접 판단해서 누르게 남겨둔다).
- "학습 시작"을 누르면 버튼이 "학습 중..."으로 바뀌고 비활성화된다(실제 완료까지
  기다릴 필요는 없음 — 상태 전환만 확인).

확인 후 `.env`의 `ENABLE_ML_TRAINING_UI=true`를 다시 제거하거나 주석 처리해
로컬 기본 상태로 되돌린다(이 값이 실수로 커밋되지 않도록 `.env`는 애초에
`.gitignore` 대상임을 확인 — `git status`로 `.env`가 추적되지 않는지 확인).

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/RegimeMlAdminPanel.tsx frontend/components/RegimeDashboard.tsx
git commit -m "feat: /regime 탭에 ML 재학습 관리자 패널 추가"
```

---

## Self-Review 체크리스트(작성 완료 후 확인)

- [x] 스펙의 모든 섹션(백엔드 job/모델목록/배포/안전장치, 프론트 UI, 에러 처리,
  테스트 계획)이 Task 1~9에 매핑됨.
- [x] 플레이스홀더("TBD"/"추후" 등) 없음 — 전 단계 완전한 코드 포함.
- [x] 타입/함수명 일관성: `RegimeMlJob`/`RegimeMlModelSummary`(Task 8)가
  Task 9에서 그대로 쓰임, `deploy_model`/`list_trained_models`(Task 5)가
  Task 6에서 그대로 import됨, `start_job`(Task 2)이 Task 3에서
  `start_regime_ml_training_job`으로 별칭 import되어 일관되게 쓰임.
