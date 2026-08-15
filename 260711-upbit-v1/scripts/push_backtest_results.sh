#!/usr/bin/env bash
set -euo pipefail

# 로컬에서 만든 백테스트 결과(data/backtest_results.db)를 AWS 서버 DB에 병합한다.
# run_id가 내용 기반 해시라 이미 서버에 있는 결과는 자동으로 건너뛰고, 로컬에서
# "최신 데이터로 갱신"해 더 최신이 된 같은 run_id는 서버 것을 교체한다. grid search를
# 새로 돌릴 때마다 반복 실행해도 안전하다. 설정 방법은 deploy/UPDATE.md 참고.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_APP_DIR="/opt/study/260711-upbit-v1"
LOCAL_DB="$REPO_ROOT/data/backtest_results.db"
REMOTE_INCOMING="data/_incoming_backtest_results.db"

if [ ! -f "$LOCAL_DB" ]; then
    echo "옮길 백테스트 결과가 없습니다: $LOCAL_DB" >&2
    exit 1
fi

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source <(tr -d '\r' < "$REPO_ROOT/.env")
    set +a
fi

if [ -z "${DEPLOY_SSH_KEY_PATH:-}" ] || [ -z "${DEPLOY_SERVER_HOST:-}" ]; then
    echo "DEPLOY_SSH_KEY_PATH / DEPLOY_SERVER_HOST가 .env에 설정되어 있지 않습니다." >&2
    echo "설정 방법은 deploy/UPDATE.md의 '로컬 백테스트 결과 가져오기' 절을 참고하세요." >&2
    exit 1
fi

echo "=== 1/2: 로컬 백테스트 결과를 서버로 전송 ==="
scp -i "$DEPLOY_SSH_KEY_PATH" "$LOCAL_DB" "$DEPLOY_SERVER_HOST:$REMOTE_APP_DIR/$REMOTE_INCOMING"

echo "=== 2/2: 서버에서 병합 실행 ==="
ssh -i "$DEPLOY_SSH_KEY_PATH" "$DEPLOY_SERVER_HOST" \
    "cd $REMOTE_APP_DIR && PYTHONPATH=. .venv/bin/python scripts/import_backtest_results.py $REMOTE_INCOMING"
