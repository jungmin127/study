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
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0


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


def test_start_job_marks_failed_when_popen_itself_raises(monkeypatch):
    import uuid as uuid_module

    monkeypatch.setattr(gss.uuid, "uuid4", lambda: uuid_module.UUID("11111111-1111-1111-1111-111111111111"))

    def _raise_popen(*args, **kwargs):
        raise OSError("no such file or directory")

    monkeypatch.setattr(gss.subprocess, "Popen", _raise_popen)

    with pytest.raises(OSError):
        gss.start_job(
            market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
            start="2026-06-05", end="2026-08-03", top_n=20,
        )

    job = get_grid_search_job("11111111111111111111111111111111")
    assert job["status"] == "failed"
    assert "grid search 실행 실패" in job["error_message"]
    assert gss._active is None


def test_start_job_spawns_script_with_required_env_and_flags(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    proc = gss._active["proc"]
    assert proc.args[0] == gss.sys.executable
    assert proc.args[1] == "scripts/grid_search.py"
    assert proc.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert proc.kwargs["env"]["PYTHONPATH"] == str(gss.REPO_ROOT)
    assert proc.kwargs["cwd"] == str(gss.REPO_ROOT)
    assert proc.kwargs["creationflags"] == gss.subprocess.CREATE_NEW_PROCESS_GROUP


def test_start_job_passes_pool_and_chaining_args_to_subprocess(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-01-01", end="2026-02-01", top_n=10,
        indicator_pool={"categories": ["추세"], "excluded_indicators": []},
        base_run_id="base-abc",
        combinator="OR",
    )

    proc = gss._active["proc"]
    args = proc.args
    assert "--categories" in args
    assert args[args.index("--categories") + 1] == "추세"
    assert "--base-run-id" in args
    assert args[args.index("--base-run-id") + 1] == "base-abc"
    assert "--combinator" in args
    assert args[args.index("--combinator") + 1] == "OR"

    job = get_grid_search_job(job_id)
    assert job["base_run_id"] == "base-abc"
    assert job["combinator"] == "OR"
    assert job["indicator_pool"] == {"categories": ["추세"], "excluded_indicators": []}


def test_start_job_raises_when_base_run_id_given_without_combinator(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    with pytest.raises(ValueError):
        gss.start_job(
            market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
            start="2026-01-01", end="2026-02-01", top_n=10,
            base_run_id="base-abc",
        )

    # 검증 실패 시 job이 시작되지 않아야 한다 — _active 슬롯이 점유되면 안 됨.
    assert gss._active is None


def test_reader_loop_resets_active_and_marks_failed_on_unexpected_exception(monkeypatch):
    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)

    def _raise_progress(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(gss, "update_grid_search_job_progress", _raise_progress)

    _FakePopen.stdout_lines = ["    완료 1,005/20,700건 (4.9%)\n"]
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    proc = gss._active["proc"]
    canceled_event = gss._active["canceled"]

    gss._reader_loop(job_id, proc, canceled_event)

    assert gss._active is None
    job = get_grid_search_job(job_id)
    assert job["status"] == "failed"
    assert "예외" in job["error_message"]


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


def test_reset_active_job_clears_active_and_sends_terminate_signal(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    proc = gss._active["proc"]

    reset_job_id = gss.reset_active_job()

    assert reset_job_id == job_id
    assert gss._active is None
    assert signal.CTRL_BREAK_EVENT in proc.signals_sent


def test_reset_active_job_returns_none_when_nothing_active():
    assert gss.reset_active_job() is None


def test_reset_active_job_ignores_errors_from_already_dead_process(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    def _raise(sig):
        raise OSError("process already terminated")

    gss._active["proc"].send_signal = _raise

    reset_job_id = gss.reset_active_job()
    assert reset_job_id is not None
    assert gss._active is None


def test_reset_active_job_with_expected_id_ignores_mismatched_active_job(monkeypatch):
    """delete 엔드포인트가 무관한 다른 job이 실행 중일 때 그 job까지 실수로
    취소하지 않도록 하는 안전장치."""
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    reset_job_id = gss.reset_active_job(expected_job_id="different-job-id")

    assert reset_job_id is None
    assert gss._active is not None
    assert gss._active["job_id"] == job_id


def test_reset_active_job_with_matching_expected_id_clears_it(monkeypatch):
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = []
    _FakePopen.returncode = 0

    job_id = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    reset_job_id = gss.reset_active_job(expected_job_id=job_id)

    assert reset_job_id == job_id
    assert gss._active is None


def test_reader_loop_does_not_clobber_newer_active_job_started_after_reset(monkeypatch):
    """job A의 리더 스레드가 뒤늦게(예: 고아 프로세스의 stdout이 한참 뒤에야 닫혀서)
    끝까지 실행됐을 때, 그 사이 사용자가 초기화 후 새로 시작한 job B의 _active
    슬롯을 지워버리면 안 된다."""
    monkeypatch.setattr(gss.threading, "Thread", _NoOpThread)
    _FakePopen.stdout_lines = ["    완료 1/1건 (100.0%)\n"]
    _FakePopen.returncode = 0

    job_id_a = gss.start_job(
        market="KRW-SOL", timeframe="minutes60", capital=1_000_000,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    proc_a = gss._active["proc"]
    canceled_a = gss._active["canceled"]

    fake_active_b = {"job_id": "job-b", "proc": object(), "canceled": None}
    gss._active = fake_active_b

    gss._reader_loop(job_id_a, proc_a, canceled_a)

    assert gss._active is fake_active_b


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
