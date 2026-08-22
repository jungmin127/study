"""
backend/grid_search_service.py

scripts/grid_search.py를 서브프로세스로 실행하고 진행률/결과를 engine.cache의
grid_search_jobs 테이블에 기록하는 오케스트레이션 레이어. 스크립트 자체는 수정하지
않고, 이미 stdout에 찍고 있는 진행률 로그와 RESULT_JSON을 그대로 파싱 대상으로 삼는다.
"""
from __future__ import annotations

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

_logger = logging.getLogger(__name__)


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
    global _active
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
            # _active가 이미 다른(더 최신) job으로 교체된 뒤라면 건드리지 않는다 — 이
            # 리더 스레드가 고아 프로세스의 stdout이 뒤늦게 닫혀 한참 후에야 여기 도달한
            # 경우, reset_active_job()으로 슬롯이 비워지고 그 사이 새 job이 이미
            # 시작됐을 수 있다. job_id가 일치할 때만 비운다.
            if _active is not None and _active["job_id"] == job_id:
                _active = None


def reset_active_job(expected_job_id: str | None = None) -> str | None:
    """`_active`를 강제로 비운다. 정리된 job_id를 반환하고, 비울 게 없었으면 None.

    expected_job_id가 주어지면 현재 `_active`의 job_id가 그것과 일치할 때만 비운다
    (delete 엔드포인트가 이 job을 지울 때, 마침 실행 중인 *다른* 무관한 job까지 실수로
    취소하지 않도록). None이면 무조건 비운다 — 사용자가 명시적으로 누르는 'job 초기화'
    버튼용 안전판이다: 백엔드가 재시작됐다고 믿었는데 실제로는 이전 프로세스가
    (Windows에서 터미널 패널을 강제로 닫아 uvicorn --reload 워커가 고아로 남는 경우 등)
    살아남아 `_active`가 stale 상태로 계속 새 grid search를 막고 있을 때 쓴다.

    추적 중이던 프로세스가 있으면 최선을 다해 종료를 시도하되, 이미 죽었거나
    응답하지 않아도 무시하고 슬롯은 반드시 비운다."""
    global _active
    with _lock:
        if _active is None:
            return None
        if expected_job_id is not None and _active["job_id"] != expected_job_id:
            return None
        job_id = _active["job_id"]
        proc = _active["proc"]
        _active["canceled"].set()
        _active = None

    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception:
        pass
    return job_id


def start_job(
    market: str, timeframe: str, capital: float, start: str, end: str, top_n: int,
    indicator_pool: dict | None = None,
    base_run_id: str | None = None,
    combinator: str | None = None,
) -> str:
    """grid search job을 시작하고 job_id를 반환한다. 이미 실행 중인 job이 있으면
    JobAlreadyRunningError를 던진다."""
    if base_run_id and not combinator:
        raise ValueError("base_run_id를 지정하면 combinator(AND 또는 OR)도 함께 지정해야 합니다.")

    global _active
    with _lock:
        if _active is not None:
            raise JobAlreadyRunningError(_active["job_id"])

        job_id = uuid.uuid4().hex
        create_grid_search_job(
            job_id=job_id, market=market, timeframe=timeframe, capital=capital,
            start=start, end=end, top_n=top_n,
            indicator_pool_json=json.dumps(indicator_pool) if indicator_pool else None,
            base_run_id=base_run_id, combinator=combinator,
        )

        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        cli_args = [
            sys.executable, "scripts/grid_search.py",
            "--market", market, "--timeframe", timeframe,
            "--capital", str(capital), "--start", start, "--end", end,
            "--top-n", str(top_n),
        ]
        if indicator_pool:
            cli_args += ["--categories", ",".join(indicator_pool.get("categories") or [])]
            excluded = indicator_pool.get("excluded_indicators") or []
            if excluded:
                cli_args += ["--exclude-indicators", ",".join(excluded)]
        if base_run_id:
            cli_args += ["--base-run-id", base_run_id, "--combinator", combinator]
        try:
            proc = subprocess.Popen(
                cli_args,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            finish_grid_search_job(job_id, status="failed", error_message=f"grid search 실행 실패: {exc}")
            raise
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
