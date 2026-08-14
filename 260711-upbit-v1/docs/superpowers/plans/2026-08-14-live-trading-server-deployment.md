# 4단계 — 상시 서버 배포 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `trading.daemon`이 사용자 PC와 무관하게 24/7 돌아가도록, Oracle Cloud VM에
daemon/backend/frontend를 systemd 서비스로 배포하는 스크립트·설정 파일 일체를 저장소에
만든다.

**Architecture:** Docker 없이 systemd 유닛 3개(daemon/backend/frontend)로 프로세스를
관리한다. 배포는 재사용 가능한 셸 스크립트(`deploy/setup.sh` 최초 설치,
`deploy/update.sh` 갱신)로 자동화해 다른 클라우드로도 이식 가능하게 한다. 원격 접속은
Tailscale 사설망을 통해서만 허용하고, 공용 인터넷엔 SSH(22)만 연다. CORS origin은
환경변수(`ALLOWED_ORIGIN`)로 바뀌어 로컬 개발과 서버 배포 양쪽에서 코드 변경 없이
동작한다.

**Tech Stack:** systemd, bash, ufw, Tailscale, FastAPI(CORSMiddleware), Next.js
(production build), pytest(설정 파일 검증용).

**이 플랜의 범위 밖:** 실제 Oracle VM 생성·SSH 접속·스크립트 실행은 이 저장소 밖의
수동 작업이다(사용자가 Oracle 계정을 만들고 VM을 프로비저닝해야 실행 가능 — 지금은
그 이전 단계). 이 플랜은 그 실행에 필요한 **파일 일체를 작성하고 로컬에서 검증 가능한
범위까지 테스트**하는 것으로 끝난다. 각 태스크의 테스트는 (a) pytest로 파일 내용/문법
검증, (b) `bash -n`으로 셸 스크립트 문법 검증까지이며, 실제 `apt-get`/`systemctl`
명령 실행 결과 검증은 포함하지 않는다.

## Global Constraints

- 클라우드: Oracle Cloud 영구무료 티어, Ampere A1(ARM), Ubuntu — 스펙 결정1
- 프로세스 관리: systemd만 사용, Docker/PM2 사용 안 함 — 스펙 결정2
- frontend는 프로덕션 빌드로 구동(`npm run build && npm run start`), `npm run dev`
  사용 안 함 — 스펙 결정2
- 원격 접속: Tailscale 사설망을 통해서만, 공용 인터넷엔 SSH(22)만 개방,
  backend는 `127.0.0.1`에만 바인딩 — 스펙 결정3
- `ALLOWED_ORIGIN` 환경변수 미설정 시 기존 기본값 `http://localhost:3000` 유지(회귀
  없음) — 스펙 결정4
- 앱 배치 경로: `/opt/study/260711-upbit-v1`(저장소 원격 URL:
  `https://github.com/jungmin127/study.git`, 이 프로젝트는 그 모노레포의 하위
  디렉토리) — 이 플랜에서 확정
- 서버는 새 빈 DB로 시작, 기존 `data/trading.db` 마이그레이션 자동화 안 함 — 스펙
  결정6
- systemd 재시작 정책: `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=60`,
  `StartLimitBurst=10`(전부 `[Unit]` 섹션의 `StartLimitIntervalSec=`/
  `StartLimitBurst=`) — 스펙 결정7

---

## File Structure

- Modify: `backend/main.py` — CORS origin을 `ALLOWED_ORIGIN` 환경변수로 변경
- Modify: `.env.example` — `ALLOWED_ORIGIN` 항목 추가
- Create: `deploy/systemd/daemon.service`
- Create: `deploy/systemd/backend.service`
- Create: `deploy/systemd/frontend.service`
- Create: `deploy/setup.sh` — 최초 설치 스크립트
- Create: `deploy/update.sh` — 코드 갱신 스크립트
- Create: `deploy/README.md` — Oracle VM 생성부터 완료까지 전체 런북
- Create: `tests/test_deploy_config.py` — systemd 유닛/셸 스크립트 내용·문법 검증
- Modify: `tests/test_backend.py` — `ALLOWED_ORIGIN` 환경변수 동작 테스트 추가

---

### Task 1: CORS origin을 `ALLOWED_ORIGIN` 환경변수로 변경

**Files:**
- Modify: `backend/main.py:1-16`(import 추가), `backend/main.py:81-91`(CORS 미들웨어)
- Modify: `.env.example`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: 없음(독립 태스크)
- Produces: `backend/main.py`가 환경변수 `ALLOWED_ORIGIN`(문자열, 미설정 시
  `"http://localhost:3000"`)을 읽어 CORS `allow_origins`에 사용 — Task 3(setup.sh)이
  `.env`에 이 값을 채우도록 안내하는 근거가 됨

- [ ] **Step 1: 실패하는 테스트 작성**

이 태스크는 CORS 헤더 자체(Starlette `CORSMiddleware`가 이미 검증된 라이브러리 코드로
올바르게 처리함)가 아니라, **origin 값을 무엇으로 결정하는지**(우리 코드의 로직)만
테스트한다 — 라이브러리 동작을 다시 검증하지 않고, 우리가 짤 로직만 좁게 겨냥한다.

`tests/test_backend.py`에 다음 두 테스트를 `test_health_check` 함수 근처(파일 상단부)에
추가한다:

```python
def test_resolve_allowed_origin_defaults_to_localhost_when_env_unset(monkeypatch):
    """ALLOWED_ORIGIN 환경변수가 없으면 기존 기본값(localhost:3000)을 그대로 써야
    한다 — 로컬 개발 흐름에 회귀가 없어야 한다(배포 스펙 결정4)."""
    monkeypatch.delenv("ALLOWED_ORIGIN", raising=False)

    assert backend_module._resolve_allowed_origin() == "http://localhost:3000"


def test_resolve_allowed_origin_uses_env_var_when_set(monkeypatch):
    """ALLOWED_ORIGIN이 설정되면(예: 서버 배포 시 Tailscale 주소) 그 값을 그대로
    써야 한다."""
    monkeypatch.setenv("ALLOWED_ORIGIN", "http://oracle-server.tailnet.ts.net:3000")

    assert (
        backend_module._resolve_allowed_origin()
        == "http://oracle-server.tailnet.ts.net:3000"
    )
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_backend.py -k resolve_allowed_origin -v`
Expected: FAIL (`AttributeError: module 'backend.main' has no attribute
'_resolve_allowed_origin'`)

- [ ] **Step 3: 최소 구현**

`backend/main.py` 상단 import에 `os` 추가(`backend/main.py:9` 근처):

```python
import json
import os
import threading
```

`backend/main.py:81-91`의 기존 CORS 미들웨어 등록을 다음으로 교체한다. 값을 결정하는
로직만 함수로 분리해 단위테스트하고, CORS 헤더 처리 자체는 검증된 `CORSMiddleware`에
그대로 맡긴다(직접 재구현하지 않음 — 재구현은 CORS처럼 보안에 민감한 영역에서 미묘한
버그를 만들기 쉽다):

```python
def _resolve_allowed_origin() -> str:
    """프론트엔드가 배포된 origin. 로컬 개발은 기본값(localhost:3000), 서버 배포
    (Oracle 등)는 ALLOWED_ORIGIN 환경변수로 Tailscale 주소를 지정한다(2026-08-14
    배포 스펙 결정4). 프로세스 시작 시 한 번 결정되면 충분하다 — systemd
    EnvironmentFile로 주입되는 값이라 실행 중 바뀌지 않는다. `.env`에 빈 값으로
    남아있는 경우(`ALLOWED_ORIGIN=`)도 python-dotenv가 os.environ에 빈 문자열로
    심으므로(미설정과 다름) `or`로 폴백해 빈 문자열도 미설정과 동일하게 취급한다."""
    return os.environ.get("ALLOWED_ORIGIN") or "http://localhost:3000"


# 라이브 전략 승인/일시정지/재개/중지 API가 실거래(자금 이동에 준하는 조작)와 붙어 있어,
# 와이드오픈 CORS("*")는 실제 계좌에 대한 위험이다(Fix 4, 최종 리뷰 Important).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_resolve_allowed_origin()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`from fastapi.middleware.cors import CORSMiddleware` import는 그대로 유지한다(삭제
안 함).

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_backend.py -k resolve_allowed_origin -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 전체 백엔드 회귀 테스트**

Run: `python -m pytest tests/test_backend.py -q`
Expected: 기존 테스트 전부 PASS(회귀 없음) — `app.add_middleware` 호출 시점(모듈
임포트 시)에 `_resolve_allowed_origin()`이 한 번 평가되므로, 이미 그 시점에 실제
프로세스 환경(`ALLOWED_ORIGIN` 미설정)대로 `http://localhost:3000`이 쓰인다. 기존
CORS 관련 동작 전부 그대로 유지된다.

- [ ] **Step 6: `.env.example`에 항목 추가**

`.env.example` 파일 전체를 다음으로 교체한다:

```
UPBIT_ACCESS_KEY=your_upbit_access_key_here
UPBIT_SECRET_KEY=your_upbit_secret_key_here

# 프론트엔드가 배포된 origin. 로컬 개발은 이 줄을 주석 처리한 채로 두면
# 기본값(http://localhost:3000)이 쓰입니다. 서버 배포 시에는 아래 주석을 해제하고
# Tailscale MagicDNS 주소로 지정하세요, 예:
# ALLOWED_ORIGIN=http://oracle-server.tailXXXX.ts.net:3000
```

- [ ] **Step 7: 커밋**

```bash
git add backend/main.py .env.example tests/test_backend.py
git commit -m "feat: CORS origin을 ALLOWED_ORIGIN 환경변수로 변경(배포 이식성)"
```

---

### Task 2: systemd 유닛 파일 3개 작성

**Files:**
- Create: `deploy/systemd/daemon.service`
- Create: `deploy/systemd/backend.service`
- Create: `deploy/systemd/frontend.service`
- Test: `tests/test_deploy_config.py`(신규)

**Interfaces:**
- Consumes: 없음
- Produces: `WorkingDirectory=/opt/study/260711-upbit-v1`(daemon/backend),
  `/opt/study/260711-upbit-v1/frontend`(frontend) — Task 3(setup.sh)이 이 경로에
  저장소를 배치해야 함. `EnvironmentFile=/opt/study/260711-upbit-v1/.env` — Task 3이
  이 경로에 `.env`를 생성해야 함.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_deploy_config.py` 신규 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_deploy_config.py -v`
Expected: FAIL(`FileNotFoundError` — `deploy/systemd/*.service` 파일이 아직 없음)

- [ ] **Step 3: `deploy/systemd/daemon.service` 작성**

```ini
[Unit]
Description=Upbit Live Trading Daemon
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/study/260711-upbit-v1
EnvironmentFile=/opt/study/260711-upbit-v1/.env
ExecStart=/opt/study/260711-upbit-v1/.venv/bin/python -m trading.daemon
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: `deploy/systemd/backend.service` 작성**

```ini
[Unit]
Description=Upbit Strategy Backend API
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/study/260711-upbit-v1
EnvironmentFile=/opt/study/260711-upbit-v1/.env
ExecStart=/opt/study/260711-upbit-v1/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: `deploy/systemd/frontend.service` 작성**

```ini
[Unit]
Description=Upbit Strategy Frontend
After=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/study/260711-upbit-v1/frontend
Environment=PORT=3000
ExecStart=/usr/bin/npm run start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/test_deploy_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: 커밋**

```bash
git add deploy/systemd/daemon.service deploy/systemd/backend.service deploy/systemd/frontend.service tests/test_deploy_config.py
git commit -m "feat: daemon/backend/frontend systemd 유닛 파일 추가"
```

---

### Task 3: `deploy/setup.sh` 최초 설치 스크립트 작성

**Files:**
- Create: `deploy/setup.sh`
- Test: `tests/test_deploy_config.py`(Task 2에서 만든 파일에 추가)

**Interfaces:**
- Consumes: Task 2의 `deploy/systemd/*.service`(이 스크립트가 `/etc/systemd/system/`로
  복사함), Task 1의 `.env.example`(존재하지 않는 `.env`를 만들 때 복사 원본)
- Produces: 없음(터미널 스크립트, 다른 태스크가 이 스크립트의 함수를 호출하지 않음)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_deploy_config.py`에 추가:

```python
import subprocess


def test_setup_script_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_DIR / "setup.sh")], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_setup_script_covers_required_install_steps():
    content = (DEPLOY_DIR / "setup.sh").read_text()
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_deploy_config.py -k setup_script -v`
Expected: FAIL(`FileNotFoundError` — `deploy/setup.sh` 없음)

- [ ] **Step 3: `deploy/setup.sh` 작성**

```bash
#!/usr/bin/env bash
set -euo pipefail

# 4단계 배포 스펙(docs/superpowers/specs/2026-08-14-live-trading-server-deployment-design.md)
# 결정5 — Oracle Cloud든 다른 우분투 VPS든 이 스크립트를 그대로 실행하면 동일하게
# 동작하도록, 클라우드 제공자 고유 API를 전혀 쓰지 않는다.

REPO_URL="https://github.com/jungmin127/study.git"
CLONE_ROOT="/opt/study"
APP_DIR="/opt/study/260711-upbit-v1"

echo "=== 1/9: 시스템 패키지 설치 ==="
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip nodejs npm git ufw curl

echo "=== 2/9: 저장소 배치 ==="
if [ ! -d "$CLONE_ROOT" ]; then
    sudo mkdir -p "$CLONE_ROOT"
    sudo chown "$USER":"$USER" "$CLONE_ROOT"
    git clone "$REPO_URL" "$CLONE_ROOT"
fi
cd "$APP_DIR"

echo "=== 3/9: 파이썬 가상환경 + 의존성 설치 ==="
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "=== 4/9: .env 확인 ==="
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ">>> .env 파일을 만들었습니다. 지금 직접 열어서"
    echo ">>>   UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY / ALLOWED_ORIGIN"
    echo ">>> 을 채운 뒤, 이 스크립트를 다시 실행하세요."
    exit 1
fi

echo "=== 5/9: 프론트엔드 프로덕션 빌드 ==="
if [ ! -f "frontend/.env.production" ]; then
    echo ">>> frontend/.env.production이 없습니다. 아래처럼 만든 뒤 다시 실행하세요:"
    echo ">>>   echo 'NEXT_PUBLIC_API_URL=http://<서버-tailscale-주소>:8000' > frontend/.env.production"
    exit 1
fi
(cd frontend && npm install && npm run build)

echo "=== 6/9: Tailscale 설치 ==="
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
echo ">>> 아직 로그인 전이면 다음을 실행하고 화면에 뜨는 링크를 눌러 로그인하세요:"
echo ">>>   sudo tailscale up"

echo "=== 7/9: systemd 서비스 등록 ==="
sudo cp deploy/systemd/daemon.service deploy/systemd/backend.service deploy/systemd/frontend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable daemon backend frontend
sudo systemctl start daemon backend frontend

echo "=== 8/9: 방화벽 설정 ==="
sudo ufw allow OpenSSH
sudo ufw --force enable
sudo ufw status

echo "=== 9/9: 완료 ==="
PUBLIC_IP=$(curl -s ifconfig.me)
echo ">>> 서버 공인 IP: $PUBLIC_IP"
echo ">>> 이 IP를 업비트 API 키 관리 페이지의 IP 화이트리스트에 등록하세요."
echo ">>> 'systemctl status daemon backend frontend' 로 세 서비스가 모두 active인지 확인하세요."
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_deploy_config.py -k setup_script -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 실행 권한 부여**

Run: `chmod +x deploy/setup.sh`

- [ ] **Step 6: 커밋**

```bash
git add deploy/setup.sh
git commit -m "feat: Oracle VM 최초 설치 스크립트(deploy/setup.sh) 추가"
```

---

### Task 4: `deploy/update.sh` 갱신 스크립트 작성

**Files:**
- Create: `deploy/update.sh`
- Test: `tests/test_deploy_config.py`(Task 2에서 만든 파일에 추가)

**Interfaces:**
- Consumes: Task 3이 만든 `/opt/study/260711-upbit-v1` 배치 구조, Task 2의
  `daemon`/`backend`/`frontend` systemd 서비스 이름
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_deploy_config.py`에 추가:

```python
def test_update_script_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_DIR / "update.sh")], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_update_script_covers_required_steps():
    content = (DEPLOY_DIR / "update.sh").read_text()
    assert "set -euo pipefail" in content
    assert "git pull" in content
    assert "pip install -r requirements.txt" in content
    assert "npm run build" in content
    assert "systemctl restart daemon backend frontend" in content
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_deploy_config.py -k update_script -v`
Expected: FAIL(`FileNotFoundError` — `deploy/update.sh` 없음)

- [ ] **Step 3: `deploy/update.sh` 작성**

```bash
#!/usr/bin/env bash
set -euo pipefail

# 배포 스펙 결정5(deploy/setup.sh 대응 갱신 스크립트) — 매번 의존성 재설치/재빌드를
# 하는 건 diff 감지 로직보다 단순하고, 이 프로젝트 규모에서 몇 초 더 걸리는 비용이
# 크지 않다(YAGNI).

APP_DIR="/opt/study/260711-upbit-v1"
cd "$APP_DIR"

echo "=== 1/4: 최신 코드 가져오기 ==="
git pull

echo "=== 2/4: 파이썬 의존성 갱신 ==="
.venv/bin/pip install -r requirements.txt

echo "=== 3/4: 프론트엔드 재빌드 ==="
(cd frontend && npm install && npm run build)

echo "=== 4/4: 서비스 재시작 ==="
echo ">>> 주의: daemon 재시작 중 몇 초간 실시간 손절/익절 감시가 끊깁니다."
echo ">>> 포지션이 없거나, 직접 지켜보고 있을 때 실행하는 걸 권장합니다."
sudo systemctl restart daemon backend frontend
sudo systemctl status daemon backend frontend --no-pager
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_deploy_config.py -k update_script -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 실행 권한 부여**

Run: `chmod +x deploy/update.sh`

- [ ] **Step 6: 커밋**

```bash
git add deploy/update.sh
git commit -m "feat: 코드 갱신 스크립트(deploy/update.sh) 추가"
```

---

### Task 5: `deploy/README.md` 런북 작성

**Files:**
- Create: `deploy/README.md`

**Interfaces:**
- Consumes: Task 1~4의 모든 파일(런북이 이들을 순서대로 안내)
- Produces: 없음(최종 사용자 문서)

- [ ] **Step 1: 런북 작성**

`deploy/README.md` 생성:

```markdown
# 상시 서버 배포 런북

이 문서는 Oracle Cloud VM 하나를 만들어 daemon/backend/frontend를 24/7 가동시키는
전체 순서다. 설계 배경은
`docs/superpowers/specs/2026-08-14-live-trading-server-deployment-design.md` 참고.

## 1. Oracle Cloud VM 생성

1. https://cloud.oracle.com 에서 계정을 만든다(신용카드 등록 필요, 무료 티어라도
   본인확인 목적).
2. 콘솔에서 Compute > Instances > Create Instance.
3. Image: **Ubuntu 22.04** (또는 최신 LTS) 선택.
4. Shape: **VM.Standard.A1.Flex**(Ampere, ARM) 선택, OCPU 2 / RAM 12GB 정도로 시작
   (Always Free 한도 내에서 조절 가능, 최대 4 OCPU/24GB까지 무료).
5. SSH 키를 생성/업로드한다(콘솔이 안내하는 대로 — 다운로드한 개인키 파일을 잘
   보관한다).
6. 생성 후 인스턴스의 **공인 IP**를 기록해둔다.

## 2. SSH 접속 및 배포 스크립트 실행

```bash
ssh -i <다운로드한-키파일> ubuntu@<공인IP>
```

접속 후:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/jungmin127/study.git /opt/study
sudo chown -R $USER:$USER /opt/study
cd /opt/study/260711-upbit-v1
bash deploy/setup.sh
```

`.env`와 `frontend/.env.production`이 없으면 스크립트가 중간에 멈추고 만드는 법을
알려준다 — 안내대로 채운 뒤 `bash deploy/setup.sh`를 다시 실행한다.

## 3. Tailscale 연결

`deploy/setup.sh`가 Tailscale을 설치한다. 로그인이 아직이면:

```bash
sudo tailscale up
```

화면에 뜨는 링크를 눌러 브라우저에서 로그인(사용자의 Tailscale 계정으로).

핸드폰/노트북에도 각각 Tailscale 앱을 설치하고 **같은 계정으로 로그인**한다. 이후
Tailscale 관리 콘솔(https://login.tailscale.com/admin/machines)에서 서버의
MagicDNS 이름(예: `oracle-server.tailXXXX.ts.net`)을 확인할 수 있다.

## 4. 업비트 API IP 화이트리스트 등록

`deploy/setup.sh` 마지막에 출력되는 서버 공인 IP를, 업비트 웹사이트의 API 키 관리
페이지에서 해당 키의 IP 화이트리스트에 추가한다.

## 5. 확인

```bash
systemctl status daemon backend frontend
journalctl -u daemon -f     # 실시간 로그, Ctrl+C로 종료
curl http://127.0.0.1:8000/health
```

핸드폰에서 Tailscale 앱 로그인 후 브라우저로
`http://oracle-server.tailXXXX.ts.net:3000` 접속 — 라이브 전략 목록이 보이면 완료.

**보안 확인(중요):** Tailscale을 끄고 핸드폰 LTE로 `http://<서버-공인IP>:8000`,
`http://<서버-공인IP>:3000`에 직접 접속을 시도해서 응답이 없는지(타임아웃) 확인한다
— 방화벽이 실제로 막고 있는지 검증하는 단계다.

## 6. 이후 코드 업데이트할 때

로컬 PC에서 평소처럼 개발 → `git push` 한 뒤, 서버에 SSH 접속해서:

```bash
cd /opt/study/260711-upbit-v1
bash deploy/update.sh
```

**주의:** daemon 재시작 중 몇 초간 실시간 손절/익절 감시가 끊긴다. 포지션이 없을 때,
또는 직접 지켜보고 있을 때 실행하는 걸 권장한다.

## 7. Oracle이 안 되면: 다른 클라우드로

이 저장소는 Docker를 쓰지 않아 특정 클라우드에 종속되지 않는다. 우분투 VM을 아무
데서나(AWS, 다른 VPS 등) 새로 만들고 위 2~5단계를 그대로 반복하면 된다. 달라지는 건
`.env`/`frontend/.env.production`을 새로 채우는 것과, 업비트 IP 화이트리스트를 새
IP로 바꾸는 것뿐이다.
```

- [ ] **Step 2: 런북과 스크립트가 서로 어긋나지 않는지 자체 확인**

`deploy/README.md`에서 언급하는 명령(`bash deploy/setup.sh`, `bash deploy/update.sh`,
`sudo tailscale up`, `systemctl status daemon backend frontend`)이 실제
`deploy/setup.sh`/`deploy/update.sh` 안의 안내 메시지·서비스 이름과 일치하는지 두
파일을 나란히 열어 대조한다. 불일치가 있으면 런북을 수정한다.

- [ ] **Step 3: 커밋**

```bash
git add deploy/README.md
git commit -m "docs: 상시 서버 배포 런북(deploy/README.md) 추가"
```

---

## 최종 검증

- [ ] **전체 테스트 스위트 실행**

Run: `python -m pytest -q`
Expected: 전부 PASS(신규 테스트 포함, 기존 826개+신규 테스트에 회귀 없음)

- [ ] **`deploy/` 디렉토리 최종 확인**

Run: `ls -la deploy/ deploy/systemd/`
Expected: `setup.sh`(실행 가능), `update.sh`(실행 가능), `README.md`,
`systemd/daemon.service`, `systemd/backend.service`, `systemd/frontend.service` 존재

이 시점부터 사용자가 `deploy/README.md`를 따라 실제 Oracle VM에서 배포를 진행할 수
있다. 실제 VM에서의 실행/검증은 이 플랜 범위 밖(사용자가 별도 세션에서 진행, 필요하면
그 과정에서 함께 트러블슈팅).
