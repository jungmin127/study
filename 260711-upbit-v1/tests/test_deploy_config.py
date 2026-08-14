import shutil
import subprocess
from pathlib import Path

DEPLOY_DIR = Path(__file__).parent.parent / "deploy"
SYSTEMD_DIR = DEPLOY_DIR / "systemd"
APP_DIR = "/opt/study/260711-upbit-v1"
# Windows에서 인자 없이 "bash"를 실행하면 CreateProcess 검색 순서상 PATH의 Git Bash보다
# System32의 WSL 스텁이 먼저 걸릴 수 있다(WSL 미설치 시 에러) — shutil.which로 PATH를
# 먼저 뒤져 실제 Git Bash(또는 리눅스의 /usr/bin/bash)를 명시적으로 고른다.
BASH = shutil.which("bash") or "bash"


def test_daemon_service_has_required_directives():
    content = (SYSTEMD_DIR / "daemon.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={APP_DIR}" in content
    assert f"EnvironmentFile={APP_DIR}/.env" in content
    assert f"ExecStart={APP_DIR}/.venv/bin/python -m trading.daemon" in content
    assert "Restart=always" in content
    assert "RestartSec=5" in content
    assert "StartLimitIntervalSec=60" in content
    assert "StartLimitBurst=10" in content
    assert "WantedBy=multi-user.target" in content


def test_backend_service_binds_localhost_only():
    content = (SYSTEMD_DIR / "backend.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={APP_DIR}" in content
    assert f"EnvironmentFile={APP_DIR}/.env" in content
    assert "uvicorn backend.main:app --host 127.0.0.1 --port 8000" in content
    assert "Restart=always" in content
    assert "StartLimitBurst=10" in content


def test_frontend_service_runs_production_start_not_dev():
    content = (SYSTEMD_DIR / "frontend.service").read_text(encoding="utf-8")
    assert f"WorkingDirectory={APP_DIR}/frontend" in content
    assert "npm run start" in content
    assert "npm run dev" not in content
    assert "Restart=always" in content
    assert "StartLimitBurst=10" in content


def test_all_service_files_are_installed_in_install_section():
    for name in ("daemon.service", "backend.service", "frontend.service"):
        content = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
        assert "[Install]" in content
        assert "WantedBy=multi-user.target" in content


def test_setup_script_has_valid_bash_syntax():
    result = subprocess.run(
        [BASH, "-n", str(DEPLOY_DIR / "setup.sh")], capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def test_setup_script_covers_required_install_steps():
    content = (DEPLOY_DIR / "setup.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in content
    assert "apt-get install" in content
    assert "python3.11 -m venv" in content
    assert "pip install -r requirements.txt" in content
    assert "npm install" in content
    assert "npm run build" in content
    assert ".env.example" in content
    assert "tailscale" in content.lower()
    assert "cp deploy/systemd/" in content
    assert "systemctl daemon-reload" in content
    assert "systemctl enable" in content
    assert "systemctl start" in content
    assert "ufw allow OpenSSH" in content
    assert "ufw --force enable" in content
    assert "ifconfig.me" in content


def test_update_script_has_valid_bash_syntax():
    result = subprocess.run(
        [BASH, "-n", str(DEPLOY_DIR / "update.sh")], capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def test_update_script_covers_required_steps():
    content = (DEPLOY_DIR / "update.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in content
    assert "git pull" in content
    assert "pip install -r requirements.txt" in content
    assert "npm run build" in content
    assert "systemctl restart daemon backend frontend" in content
