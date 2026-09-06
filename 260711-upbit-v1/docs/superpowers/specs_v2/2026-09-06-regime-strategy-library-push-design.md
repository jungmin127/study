# 전략 라이브러리 로컬→AWS 푸시 스크립트 — 설계 스펙

## 배경

[[upbit-v1-regime-strategy-pivot-adx-autoswap]]에서 확정한 4단계 피벗 계획 중
3단계(전략 라이브러리 UI, `docs/superpowers/specs_v2/2026-09-06-strategy-library-design.md`)는
완료됐다. 사용자가 그 다음 방향을 다음과 같이 정리했다:

1. 로컬에서 `/strategy-library`로 코인별 하락/횡보/상승/기본 매핑 설정(완료)
2. **이 매핑을 AWS 서버의 실거래 DB로 그대로 복사**(이 스펙의 범위)
3. AWS에서 daemon이 현재 장세에 따라 저장된 매핑 중 하나로 라이브 전략을 자동
   변경(4단계 본체 — 별도 세션에서 브레인스토밍)

2번과 3번은 서로 다른 리스크 수준을 가진 독립 작업이라 브레인스토밍에서
분리하기로 결정했다: 2번(이 스펙)은 기존 `scripts/push_backtest_results.sh`
패턴을 재사용하는 작고 기계적인 작업이고, 3번(daemon 자동 스왑 루프 —
끈백질 방지, 오픈 포지션 대기, 자동/수동 스위치)은 실거래에 직접 영향을 주는
가장 리스크 높은 작업이라 별도 세션에서 독립적으로 다룬다.

## 목표

로컬 `trading.db`의 `regime_strategy_library` 테이블 내용을 AWS 서버의
`trading.db`(`live_strategies` 등 실거래 데이터가 들어있는 그 파일)에 있는
같은 테이블로 **완전 거울 동기화**하는 스크립트를 만든다: 로컬에 있는
(market, regime) 조합은 upsert되고, 로컬에 없는 조합은 서버에서도 삭제된다
(사용자 확정 요구사항 — "로컬이 지우면 원격도 지움"). 기존
`bash scripts/push_backtest_results.sh` 실행 관례와 동일하게
`bash scripts/push_regime_strategy_library.sh`로 실행한다.

## 비범위

- 4단계 daemon 자동 스왑 루프 본체 — 별도 세션
- 자동/주기적 푸시(cron 등) — 사용자가 필요할 때 수동 실행하는 기존 관례를
  그대로 따름
- 로컬 `trading.db` 파일 전체를 옮기는 방식 — 아래 "설계" 참고, 안전상 채택
  안 함
- AWS→로컬 역방향 동기화(pull) — 기존 다른 push 스크립트들도 전부 단방향
- `regime_strategy_library` 외 다른 테이블(`live_strategies` 등)의 동기화

## 설계

### 왜 `trading.db` 파일 전체를 옮기지 않는가

`push_backtest_results.sh`는 로컬 `backtest_results.db`(백테스트 결과
전용, 실거래와 무관) 파일 전체를 그대로 서버로 보낸다. 하지만
`trading.db`는 로컬에도 개발/테스트용 `live_strategies`/`positions`/
`orders` 등이 들어있어서, 파일째로 옮기면 로컬의 무관한 상태가 서버의
실거래 데이터와 뒤섞일 위험이 있다. 대신 로컬에서 **`regime_strategy_library`
테이블 행만 뽑아낸 별도의 작은 sqlite 파일**을 새로 만들어 그것만 전송한다.
이 테이블은 `live_strategies`와 외래키 관계가 전혀 없으므로(3단계 스펙에서
이미 확인), 이 푸시는 서버의 daemon/백엔드 재시작이 필요 없고 오픈 포지션
여부와도 무관하게 항상 안전하다 — `[[upbit-v1-deploy-check-open-positions-first]]`가
요구하는 사전 확인이 이 스크립트에는 적용되지 않는다.

### 1. `scripts/export_regime_strategy_library.py` (신규, 로컬에서 실행)

`trading/db.py`의 `_connect()`를 재사용해 로컬 `trading.db`에 접속하고,
지정된 출력 경로에 `regime_strategy_library` 테이블만 담긴 새 sqlite
파일을 만든다(`ATTACH DATABASE ... AS export`로 새 파일을 열고, 그 안에
동일한 `CREATE TABLE`을 실행한 뒤 `INSERT INTO export.regime_strategy_library
SELECT * FROM regime_strategy_library`로 복사) — `import_backtest_results.py`가
이미 쓰는 `ATTACH DATABASE` 방식과 동일한 스타일.

```python
"""
scripts/export_regime_strategy_library.py

로컬 trading.db의 regime_strategy_library 테이블 행만 별도의 작은 sqlite
파일로 뽑아낸다. trading.db 파일 전체를 옮기지 않는 이유는
docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md 참고
(로컬 trading.db에는 실거래와 무관한 개발용 live_strategies 등이 섞여있음).
Run: PYTHONPATH=. python scripts/export_regime_strategy_library.py data/_export_regime_strategy_library.db
"""
from __future__ import annotations

import argparse
from pathlib import Path

import trading.db as trading_db

_TABLE_SCHEMA = """
CREATE TABLE regime_strategy_library (
    market                TEXT NOT NULL,
    regime                TEXT NOT NULL CHECK (regime IN ('하락', '횡보', '상승', '기본')),
    source_run_id         TEXT NOT NULL,
    timeframe             TEXT NOT NULL,
    buy_conditions_json   TEXT NOT NULL,
    sell_conditions_json  TEXT NOT NULL,
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (market, regime)
);
"""


def export_regime_strategy_library(output_path: Path) -> int:
    """regime_strategy_library 전체 행을 output_path의 새 sqlite 파일로 복사한다.
    output_path가 이미 있으면 지우고 새로 만든다. 반환값은 복사한 행 수."""
    if output_path.exists():
        output_path.unlink()

    conn = trading_db._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS export", (str(output_path),))
        try:
            conn.execute(f"CREATE TABLE export.regime_strategy_library ({_TABLE_SCHEMA.split('(', 1)[1]}")
            conn.execute(
                "INSERT INTO export.regime_strategy_library "
                "SELECT * FROM regime_strategy_library"
            )
            count = conn.execute("SELECT COUNT(*) FROM export.regime_strategy_library").fetchone()[0]
            conn.commit()
            return count
        finally:
            conn.execute("DETACH DATABASE export")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 전략 라이브러리를 별도 sqlite 파일로 export")
    parser.add_argument("output_path", help="생성할 sqlite 파일 경로")
    args = parser.parse_args()

    count = export_regime_strategy_library(Path(args.output_path))
    print(f"전략 라이브러리 {count}건을 {args.output_path}에 export했습니다.")


if __name__ == "__main__":
    main()
```

(실제 구현 시 `_TABLE_SCHEMA` 문자열 조작은 가독성이 떨어지므로, 계획
단계에서 `CREATE TABLE export.regime_strategy_library (시작부터 끝까지
전체 SQL을 그대로)` 형태의 완전한 리터럴로 다시 쓴다 — 위 스니펫은 설계
의도만 보여주는 것이고 `trading/db.py`의 `_SCHEMA`에 있는 해당 테이블
정의를 그대로 복사해서 쓴다.)

### 2. `scripts/import_regime_strategy_library.py` (신규, 원격에서 실행)

`import_backtest_results.py`와 동일한 `ATTACH DATABASE` 스타일이지만
병합 규칙이 다르다: backtest 결과는 "더 최신이면 교체, 아니면 건너뜀"(누적형)
이지만, 이 테이블은 **완전 거울**(로컬에 없는 행은 삭제)이다.

```python
"""
scripts/import_regime_strategy_library.py

로컬에서 export한 전략 라이브러리 sqlite 파일(예:
data/_incoming_regime_strategy_library.db)을 서버의 trading.db에 완전
거울 동기화한다 — incoming에 있는 (market, regime)은 upsert하고,
서버에만 있고 incoming에는 없는 (market, regime)은 삭제한다. 로컬
/strategy-library 화면이 "진실의 원천"이 되도록 하기 위한 설계 결정
(docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md).
Run: PYTHONPATH=. .venv/bin/python scripts/import_regime_strategy_library.py data/_incoming_regime_strategy_library.db
"""
from __future__ import annotations

import argparse
from pathlib import Path

import trading.db as trading_db


def mirror_regime_strategy_library(incoming_path: Path) -> dict[str, int]:
    """incoming_path의 regime_strategy_library를 trading_db.DB_PATH의 같은 테이블로
    완전 거울 동기화한다. Returns {"upserted", "deleted"}."""
    conn = trading_db._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS incoming", (str(incoming_path),))
        try:
            incoming_keys = set(
                conn.execute("SELECT market, regime FROM incoming.regime_strategy_library").fetchall()
            )
            existing_keys = set(
                conn.execute("SELECT market, regime FROM regime_strategy_library").fetchall()
            )

            to_delete = existing_keys - incoming_keys
            for market, regime in to_delete:
                conn.execute(
                    "DELETE FROM regime_strategy_library WHERE market = ? AND regime = ?",
                    (market, regime),
                )

            conn.execute(
                "INSERT INTO regime_strategy_library "
                "SELECT * FROM incoming.regime_strategy_library "
                "WHERE true "
                "ON CONFLICT(market, regime) DO UPDATE SET "
                "source_run_id=excluded.source_run_id, timeframe=excluded.timeframe, "
                "buy_conditions_json=excluded.buy_conditions_json, "
                "sell_conditions_json=excluded.sell_conditions_json, updated_at=excluded.updated_at"
            )
            conn.commit()
            return {"upserted": len(incoming_keys), "deleted": len(to_delete)}
        finally:
            conn.execute("DETACH DATABASE incoming")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="전략 라이브러리를 서버 DB에 완전 거울 동기화")
    parser.add_argument("incoming_path", help="병합할 sqlite 파일 경로")
    args = parser.parse_args()
    incoming_path = Path(args.incoming_path)

    if not incoming_path.exists():
        raise SystemExit(f"입력 파일이 없습니다: {incoming_path}")

    if incoming_path.stat().st_size == 0:
        print("경고: 입력 파일이 비어 있습니다.")

    counts = mirror_regime_strategy_library(incoming_path)
    if counts["upserted"] == 0:
        print(
            "경고: 로컬 라이브러리가 비어 있어 서버의 모든 매핑을 삭제했습니다 "
            f"({counts['deleted']}건). 의도한 게 맞는지 확인하세요."
        )
    else:
        print(f"전략 라이브러리 동기화 완료: {counts['upserted']}건 반영, {counts['deleted']}건 삭제")

    incoming_path.unlink()
    print(f"임시 파일 삭제: {incoming_path}")


if __name__ == "__main__":
    main()
```

`INSERT INTO ... SELECT ... ON CONFLICT DO UPDATE`는 SQLite 3.24+에서
지원되는 표준 UPSERT 문법이며(이미 `trading_db.upsert_regime_strategy_mapping`가
같은 문법을 씀), `SELECT ... WHERE true`의 `WHERE true`는 SQLite 파서가
`INSERT ... SELECT ... ON CONFLICT`를 `INSERT ... VALUES ... ON CONFLICT`와
구분해서 파싱하도록 하는 데 필요한 관용구다(공식 문서 명시 사항 — 실제
구현 시 SQLite 버전에서 필요 없다고 확인되면 생략 가능, 계획 단계에서
간단한 수동 확인으로 검증).

### 3. `scripts/push_regime_strategy_library.sh` (신규, 로컬에서 실행)

`push_backtest_results.sh`와 동일한 `.env`/scp/ssh 골격:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 로컬 전략 라이브러리(regime_strategy_library)를 AWS 서버 trading.db에
# 완전 거울 동기화한다 — 로컬에 없는 (market,regime)은 서버에서도 삭제된다.
# live_strategies와 무관한 별도 테이블이라 서버 daemon/백엔드 재시작이
# 필요 없고 오픈 포지션 여부와도 무관하게 항상 안전하다.

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
```

## 테스트 전략

- **`tests/test_export_regime_strategy_library.py`**: `trading_db.DB_PATH`를
  `tmp_path`로 monkeypatch, 몇 개의 매핑을 `upsert_regime_strategy_mapping`으로
  넣은 뒤 `export_regime_strategy_library(output_path)` 호출 → 반환된 카운트와
  `output_path`를 직접 열어 행 내용이 원본과 일치하는지 확인. 빈 라이브러리
  export(0건)도 확인.
- **`tests/test_import_regime_strategy_library.py`**: `trading_db.DB_PATH`를
  `tmp_path`로 monkeypatch해 "서버" 역할, 별도 `tmp_path`에 "incoming" sqlite
  파일을 직접 만들어(export 스크립트로 만들거나 raw sqlite3로 스키마+행 삽입)
  `mirror_regime_strategy_library()` 호출 검증:
  1. 서버에 없는 incoming 행 → upsert(신규 삽입) 확인
  2. 서버에 있고 incoming에도 있지만 값이 다른 행 → incoming 값으로 갱신 확인
  3. 서버에는 있지만 incoming에는 없는 행 → 삭제 확인
  4. incoming이 완전히 비어있으면 서버의 모든 행이 삭제되는지 확인(의도된
     동작이지만 회귀 방지를 위해 명시적으로 테스트)
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체 통과

## 완료 기준

- 로컬에서 `bash scripts/push_regime_strategy_library.sh` 실행 시 (.env
  설정이 돼 있다는 가정하에) 로컬 `regime_strategy_library` 내용이 AWS
  서버의 `trading.db`에 완전 거울 동기화된다
- 로컬에서 슬롯을 제거한 뒤 다시 푸시하면 서버의 해당 슬롯도 삭제된다
- 신규 유닛 테스트 통과, 기존 테스트 스위트 회귀 없음
- 이 스크립트는 서버 daemon/백엔드 재시작을 하지 않는다(코드 리뷰로 확인 —
  `deploy/update.sh`를 호출하지 않음)
