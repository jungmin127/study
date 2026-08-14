from pathlib import Path

DEPLOY_DIR = Path(__file__).parent.parent / "deploy"
SYSTEMD_DIR = DEPLOY_DIR / "systemd"
APP_DIR = "/opt/study/260711-upbit-v1"


def test_daemon_service_has_required_directives():
    content = (SYSTEMD_DIR / "daemon.service").read_text()
    assert f"WorkingDirectory={APP_DIR}" in content
    assert f"EnvironmentFile={APP_DIR}/.env" in content
    assert f"ExecStart={APP_DIR}/.venv/bin/python -m trading.daemon" in content
    assert "Restart=always" in content
    assert "RestartSec=5" in content
    assert "StartLimitIntervalSec=60" in content
    assert "StartLimitBurst=10" in content
    assert "WantedBy=multi-user.target" in content


def test_backend_service_binds_localhost_only():
    content = (SYSTEMD_DIR / "backend.service").read_text()
    assert f"WorkingDirectory={APP_DIR}" in content
    assert f"EnvironmentFile={APP_DIR}/.env" in content
    assert "uvicorn backend.main:app --host 127.0.0.1 --port 8000" in content
    assert "Restart=always" in content
    assert "StartLimitBurst=10" in content


def test_frontend_service_runs_production_start_not_dev():
    content = (SYSTEMD_DIR / "frontend.service").read_text()
    assert f"WorkingDirectory={APP_DIR}/frontend" in content
    assert "npm run start" in content
    assert "npm run dev" not in content
    assert "Restart=always" in content
    assert "StartLimitBurst=10" in content


def test_all_service_files_are_installed_in_install_section():
    for name in ("daemon.service", "backend.service", "frontend.service"):
        content = (SYSTEMD_DIR / name).read_text()
        assert "[Install]" in content
        assert "WantedBy=multi-user.target" in content
