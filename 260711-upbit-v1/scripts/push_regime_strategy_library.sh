#!/usr/bin/env bash
set -euo pipefail

# 로컬 전략 라이브러리(regime_strategy_library)를 AWS 서버 trading.db에
# 완전 거울 동기화한다 — 로컬에 없는 (market,regime)은 서버에서도 삭제된다.
# live_strategies와 무관한 별도 테이블이라 서버 daemon/백엔드 재시작이
# 필요 없고 오픈 포지션 여부와도 무관하게 항상 안전하다. 설계 문서:
# docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_APP_DIR="/opt/study/260711-upbit-v1"
LOCAL_EXPORT="$REPO_ROOT/data/_export_regime_strategy_library.db"
REMOTE_INCOMING="data/_incoming_regime_strategy_library.db"

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source <(tr -d '\r' < "$REPO_ROOT/.env")
    set +a
fi

if [ -z "${DEPLOY_SSH_KEY_PATH:-}" ] || [ -z "${DEPLOY_SERVER_HOST:-}" ]; then
    echo "DEPLOY_SSH_KEY_PATH / DEPLOY_SERVER_HOST가 .env에 설정되어 있지 않습니다." >&2
    echo "설정 방법은 deploy/UPDATE.md의 '로컬 전략 라이브러리를 서버로 동기화하기' 절을 참고하세요." >&2
    exit 1
fi

echo "=== 1/3: 로컬 전략 라이브러리 export ==="
cd "$REPO_ROOT" && PYTHONPATH=. python scripts/export_regime_strategy_library.py "$LOCAL_EXPORT"

echo "=== 2/3: 서버로 전송 ==="
scp -i "$DEPLOY_SSH_KEY_PATH" "$LOCAL_EXPORT" "$DEPLOY_SERVER_HOST:$REMOTE_APP_DIR/$REMOTE_INCOMING"

echo "=== 3/3: 서버에서 거울 동기화 실행 ==="
ssh -i "$DEPLOY_SSH_KEY_PATH" "$DEPLOY_SERVER_HOST" \
    "cd $REMOTE_APP_DIR && PYTHONPATH=. .venv/bin/python scripts/import_regime_strategy_library.py $REMOTE_INCOMING"

rm -f "$LOCAL_EXPORT"
echo "완료."
