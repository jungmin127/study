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
