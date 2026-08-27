#!/usr/bin/env bash
set -euo pipefail

# 로컬에서 scripts/train_regime_ml.py로 학습한 ML 장세판별 모델(가장 최신
# .txt+.json 페어, 또는 인자로 특정 모델을 지정할 수도 있다)을 AWS 서버로 복사한다.
# 모델은 병합이 필요 없다 — 항상 find_latest_model()이 파일명 타임스탬프 기준
# 가장 최신 것을 고르므로, 그냥 최신 파일 두 개를 서버 같은 경로에 올려두면 된다.
# 설정 방법은 deploy/UPDATE.md 참고.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_APP_DIR="/opt/study/260711-upbit-v1"
LOCAL_MODEL_DIR="$REPO_ROOT/data/regime_ml_models"
REMOTE_MODEL_DIR="$REMOTE_APP_DIR/data/regime_ml_models"

# 첫 번째 인자로 특정 모델 베이스네임(예: regime_ml_20260827T223633Z)을 지정하면
# 그 모델을 배포한다(재학습 셀프서비스 UI가 과거 학습 이력 중 골라 배포할 때 사용).
# 인자가 없으면 기존과 동일하게 가장 최신 모델을 찾는다.
if [ -n "${1:-}" ]; then
    LOCAL_TXT="$LOCAL_MODEL_DIR/$1.txt"
    if [ ! -f "$LOCAL_TXT" ]; then
        echo "지정한 모델을 찾을 수 없습니다: $LOCAL_TXT" >&2
        exit 1
    fi
else
    if [ -d "$LOCAL_MODEL_DIR" ]; then
        LOCAL_TXT="$(find "$LOCAL_MODEL_DIR" -maxdepth 1 -name 'regime_ml_*.txt' | sort | tail -n 1)"
    else
        LOCAL_TXT=""
    fi

    if [ -z "$LOCAL_TXT" ]; then
        echo "옮길 ML 모델이 없습니다: $LOCAL_MODEL_DIR" >&2
        echo "먼저 scripts/train_regime_ml.py를 실행해 모델을 학습하세요." >&2
        exit 1
    fi
fi

LOCAL_JSON="${LOCAL_TXT%.txt}.json"

if [ ! -f "$LOCAL_JSON" ]; then
    echo "모델 .json 사이드카가 없습니다: $LOCAL_JSON" >&2
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
    echo "설정 방법은 deploy/UPDATE.md의 '로컬에서 학습한 ML 장세판별 모델을 서버로 가져오기' 절을 참고하세요." >&2
    exit 1
fi

MODEL_NAME="$(basename "$LOCAL_TXT")"

echo "=== 1/2: 원격 모델 디렉터리 준비 ==="
ssh -i "$DEPLOY_SSH_KEY_PATH" "$DEPLOY_SERVER_HOST" "mkdir -p $REMOTE_MODEL_DIR"

echo "=== 2/2: 모델 파일 전송 ==="
scp -i "$DEPLOY_SSH_KEY_PATH" "$LOCAL_TXT" "$LOCAL_JSON" "$DEPLOY_SERVER_HOST:$REMOTE_MODEL_DIR/"

echo "모델 전송 완료: $MODEL_NAME (.txt + .json)"
