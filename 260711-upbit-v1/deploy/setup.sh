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
sudo apt-get install -y python3.11 python3.11-venv python3-pip nodejs git ufw curl

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
