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
