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
