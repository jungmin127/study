# 로컬 → AWS 백테스트 결과 이전 스크립트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬에서 돌린 grid search 결과(`data/backtest_results.db`)를, 명령어 하나로
AWS 서버의 같은 DB에 안전하게(중복 없이, 반복 실행 가능하게) 병합하는 스크립트를
만든다.

**Architecture:** 로컬 셸 스크립트(`scripts/push_backtest_results.sh`)가 `scp`로 DB
파일을 서버에 올리고 `ssh`로 서버 쪽 파이썬 병합 스크립트
(`scripts/import_backtest_results.py`)를 실행시킨다. 병합은 `run_id`(내용 기반
해시) 기준 `INSERT OR IGNORE`라 이미 서버에 있는 결과는 자동으로 건너뛴다.

**Tech Stack:** Python 3.11(`sqlite3` 표준 라이브러리, `engine.cache._connect()`
재사용), Bash(Git Bash on Windows), OpenSSH(`scp`/`ssh`).

## Global Constraints

- 병합 대상 테이블은 `backtest_runs`/`backtest_results` 두 개뿐(스펙 범위 — 자세한
  근거는 design spec 참고).
- `sqlite3` CLI를 서버에 새로 설치하지 않는다 — 서버 venv의 파이썬으로만 병합한다.
- 원격 앱 경로는 `/opt/study/260711-upbit-v1`로 고정(`deploy/update.sh`와 동일,
  설정으로 빼지 않음).
- SSH 키 경로/서버 주소는 로컬 `.env`에서 읽는다(신규 키:
  `DEPLOY_SSH_KEY_PATH`, `DEPLOY_SERVER_HOST`).
- 실패 시 임시 파일은 지우지 않는다(성공 시에만 삭제) — design spec 결정4/에러
  처리 절 참고.

Design spec: `docs/superpowers/specs_v1/2026-08-15-local-to-aws-backtest-results-sync-design.md`

---

### Task 1: `merge_databases()` — 병합 핵심 로직

**Files:**
- Create: `scripts/import_backtest_results.py`
- Test: `tests/test_import_backtest_results.py`

**Interfaces:**
- Consumes: `engine.cache._connect()`(연결 반환, `DB_PATH.parent.mkdir` +
  스키마 생성까지 처리), `engine.cache.DB_PATH`(모듈 전역, 테스트에서
  monkeypatch 대상), `engine.cache.save_result(run_id, strategy_name,
  strategy_params, market, timeframe, start, end, risk_config, result,
  title=None, description=None)`(테스트 DB 시딩용).
- Produces: `merge_databases(incoming_path: pathlib.Path) -> dict[str, int]`
  (키: `runs_inserted`, `runs_skipped`, `results_inserted`,
  `results_skipped`) — Task 2가 이 반환값을 그대로 사용한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_import_backtest_results.py` 파일을 새로 만든다:

```python
from datetime import datetime, timezone

import engine.cache as cache_module
from engine.cache import save_result
from scripts.import_backtest_results import merge_databases


def _seed(monkeypatch, db_path, run_id: str, final_value: float) -> None:
    """cache_module.DB_PATH를 db_path로 잠깐 바꿔 save_result()로 한 건을 저장한다.
    monkeypatch를 통해서만 바꿔야 각 테스트 종료 시 원래 값으로 정확히 복원된다."""
    monkeypatch.setattr(cache_module, "DB_PATH", db_path)
    save_result(
        run_id=run_id,
        strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": final_value, "sharpe": 1.0, "max_drawdown": 2.0,
            "equity_curve": [], "trades": [],
        },
    )


def test_merge_databases_inserts_new_runs_and_results(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)
    _seed(monkeypatch, incoming_db, "run-b", 12000.0)

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 1, "runs_skipped": 0,
        "results_inserted": 1, "results_skipped": 0,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 11000.0
    assert load_result("run-b")["final_value"] == 12000.0


def test_merge_databases_skips_existing_run_id_without_overwriting(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)
    _seed(monkeypatch, incoming_db, "run-a", 99999.0)  # 같은 run_id, 다른 값 — 서버 값이 우선해야 함

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 0, "runs_skipped": 1,
        "results_inserted": 0, "results_skipped": 1,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 11000.0  # 서버 쪽 값 그대로


def test_merge_databases_handles_run_without_matching_result(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)

    # incoming.db에 backtest_runs 행만 있고 backtest_results가 없는 비정상 상태를
    # 직접 만든다(save_result()는 항상 둘 다 넣으므로 raw SQL로 흉내낸다).
    monkeypatch.setattr(cache_module, "DB_PATH", incoming_db)
    conn = cache_module._connect()
    conn.execute(
        "INSERT INTO backtest_runs "
        "(id, strategy_name, params_json, market, timeframe, start, end, "
        " risk_config_json, created_at) "
        "VALUES ('run-orphan', 'ConditionTreeStrategy', '{}', 'KRW-BTC', 'days', "
        "        '2026-01-01T00:00:00+00:00', '2026-01-10T00:00:00+00:00', '{}', "
        "        datetime('now'))"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts["runs_inserted"] == 1  # run-orphan
    assert counts["results_inserted"] == 0  # 짝이 없으니 결과는 삽입될 게 없음
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_import_backtest_results.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.import_backtest_results'`

- [ ] **Step 3: `merge_databases()` 구현**

`scripts/import_backtest_results.py`를 새로 만든다:

```python
"""
scripts/import_backtest_results.py

로컬에서 만든 백테스트 결과 DB(예: data/_incoming_backtest_results.db)를 서버의
data/backtest_results.db(engine.cache.DB_PATH)에 병합한다. run_id(compute_cache_key의
내용 기반 해시)가 이미 있으면 건너뛰므로, 같은 조건으로 로컬에서 grid search를 다시
돌려 여러 번 실행해도 안전하다.
Run: .venv/bin/python scripts/import_backtest_results.py data/_incoming_backtest_results.db
"""
from __future__ import annotations

import argparse
from pathlib import Path

import engine.cache as cache_module


def merge_databases(incoming_path: Path) -> dict[str, int]:
    """incoming_path의 backtest_runs/backtest_results를 engine.cache.DB_PATH로 병합한다.

    Returns:
        {"runs_inserted", "runs_skipped", "results_inserted", "results_skipped"}
    """
    conn = cache_module._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS incoming", (str(incoming_path),))
        try:
            before_runs = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            incoming_runs = conn.execute("SELECT COUNT(*) FROM incoming.backtest_runs").fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO backtest_runs SELECT * FROM incoming.backtest_runs")
            after_runs = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            runs_inserted = after_runs - before_runs

            before_results = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
            incoming_results = conn.execute("SELECT COUNT(*) FROM incoming.backtest_results").fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO backtest_results SELECT * FROM incoming.backtest_results")
            after_results = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
            results_inserted = after_results - before_results

            conn.commit()
        finally:
            conn.execute("DETACH DATABASE incoming")
    finally:
        conn.close()

    return {
        "runs_inserted": runs_inserted,
        "runs_skipped": incoming_runs - runs_inserted,
        "results_inserted": results_inserted,
        "results_skipped": incoming_results - results_inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 백테스트 결과 DB를 서버 DB에 병합")
    parser.add_argument("incoming_path", help="병합할 sqlite 파일 경로")
    args = parser.parse_args()
    incoming_path = Path(args.incoming_path)

    if not incoming_path.exists():
        raise SystemExit(f"입력 파일이 없습니다: {incoming_path}")

    counts = merge_databases(incoming_path)
    print(
        f"백테스트 결과 병합 완료: 신규 {counts['runs_inserted']}건 추가, "
        f"기존 {counts['runs_skipped']}건 건너뜀"
    )

    incoming_path.unlink()
    print(f"임시 파일 삭제: {incoming_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_import_backtest_results.py -v`
Expected: `3 passed`

- [ ] **Step 5: 커밋**

```bash
git add scripts/import_backtest_results.py tests/test_import_backtest_results.py
git commit -m "feat: 로컬 백테스트 결과 DB를 서버 DB에 병합하는 merge_databases() 추가"
```

---

### Task 2: `main()` CLI 동작 — 파일 없음 에러 / 성공 시 삭제

**Files:**
- Modify: `tests/test_import_backtest_results.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 1의 `merge_databases()`, `scripts.import_backtest_results.main()`
  (Task 1에서 이미 구현됨 — 이 태스크는 그 동작을 검증만 한다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_import_backtest_results.py` 끝에 추가:

```python
import sys

import pytest

from scripts.import_backtest_results import main


def test_main_raises_when_incoming_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "server.db")
    monkeypatch.setattr(sys, "argv", ["import_backtest_results.py", str(tmp_path / "nope.db")])

    with pytest.raises(SystemExit, match="입력 파일이 없습니다"):
        main()


def test_main_deletes_incoming_file_after_successful_merge(tmp_path, monkeypatch, capsys):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"
    _seed(monkeypatch, incoming_db, "run-c", 13000.0)

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    monkeypatch.setattr(sys, "argv", ["import_backtest_results.py", str(incoming_db)])

    main()

    assert not incoming_db.exists()
    output = capsys.readouterr().out
    assert "신규 1건 추가" in output
    assert "임시 파일 삭제" in output
```

`_seed()`도 내부적으로 `monkeypatch.setattr(cache_module, "DB_PATH", ...)`를 쓰므로,
`main()` 호출 시점에 마지막으로 설정된 값(`server_db`)이 유효하다 — `_seed` 호출을
먼저 하고, 그다음 `monkeypatch.setattr(cache_module, "DB_PATH", server_db)`로
덮어써야 `merge_databases()`가 정확히 `server_db`를 대상으로 동작한다. 순서를
바꾸면 `incoming_db`를 대상으로 병합을 시도하게 되어 테스트가 의도와 다르게
동작하니 주의한다.

- [ ] **Step 2: 테스트 실행 → 확인**

Run: `python -m pytest tests/test_import_backtest_results.py -v`
Expected: 이미 Task 1에서 `main()`을 구현해뒀으므로 `5 passed`로 바로 통과해야 한다.
만약 실패한다면(예: 에러 메시지 문구 불일치) Step 3에서 `scripts/import_backtest_results.py`의
`main()` 메시지를 테스트와 맞춘다.

- [ ] **Step 3: (필요 시) `main()` 수정**

테스트가 실패했다면 `raise SystemExit(f"입력 파일이 없습니다: {incoming_path}")` 문구나
`print()` 문구를 테스트 기대값에 맞춰 조정한다. Task 1의 구현이 이미 이 문구를 쓰고
있으므로 보통 수정 없이 통과한다.

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_import_backtest_results.py -v`
Expected: `5 passed`

- [ ] **Step 5: 커밋**

```bash
git add tests/test_import_backtest_results.py
git commit -m "test: import_backtest_results.py의 main() CLI 동작(파일없음/성공시 삭제) 검증"
```

---

### Task 3: 로컬 오케스트레이션 스크립트 `push_backtest_results.sh`

**Files:**
- Create: `scripts/push_backtest_results.sh`

**Interfaces:**
- Consumes: Task 1/2에서 만든 `scripts/import_backtest_results.py`(서버에서
  `.venv/bin/python`으로 실행됨), 로컬 `.env`의 `DEPLOY_SSH_KEY_PATH`/
  `DEPLOY_SERVER_HOST`.
- Produces: 실행 가능한 셸 스크립트 `scripts/push_backtest_results.sh`(다음
  태스크의 문서화 대상).

- [ ] **Step 1: 스크립트 작성**

`scripts/push_backtest_results.sh`를 새로 만든다:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 로컬에서 만든 백테스트 결과(data/backtest_results.db)를 AWS 서버 DB에 병합한다.
# run_id가 내용 기반 해시라 이미 서버에 있는 결과는 자동으로 건너뛰므로, grid search를
# 새로 돌릴 때마다 반복 실행해도 안전하다. 설정 방법은 deploy/UPDATE.md 참고.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

REMOTE_APP_DIR="/opt/study/260711-upbit-v1"
LOCAL_DB="$REPO_ROOT/data/backtest_results.db"
REMOTE_INCOMING="data/_incoming_backtest_results.db"

if [ ! -f "$LOCAL_DB" ]; then
    echo "옮길 백테스트 결과가 없습니다: $LOCAL_DB" >&2
    exit 1
fi

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
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
    "cd $REMOTE_APP_DIR && .venv/bin/python scripts/import_backtest_results.py $REMOTE_INCOMING"
```

- [ ] **Step 2: 실행 권한 부여**

Run: `chmod +x scripts/push_backtest_results.sh`

- [ ] **Step 3: 구문 검증(네트워크 없이)**

Run: `bash -n scripts/push_backtest_results.sh`
Expected: 아무 출력 없이 종료 코드 0 (구문 에러가 없다는 뜻).

- [ ] **Step 4: `.env` 없이 실행했을 때 에러 메시지 확인(로컬 DB가 있는 경우에만 이 단계까지 도달)**

이 단계는 실제 `data/backtest_results.db`가 로컬에 있어야 다음 검증까지 갈 수 있다.
없다면 건너뛰고 Task 4에서 실제 서버로 수동 검증할 때 함께 확인한다. 있다면:

Run: `DEPLOY_SSH_KEY_PATH= DEPLOY_SERVER_HOST= bash scripts/push_backtest_results.sh`
(단, `.env`에 이미 값이 있다면 `source`가 그 값으로 덮어쓰므로 이 커맨드라인
오버라이드는 `.env`가 아직 없을 때만 유효한 확인 방법이다.)
Expected: `DEPLOY_SSH_KEY_PATH / DEPLOY_SERVER_HOST가 .env에 설정되어 있지 않습니다.`
가 stderr로 출력되고 종료 코드 1.

- [ ] **Step 5: 커밋**

```bash
git add scripts/push_backtest_results.sh
git commit -m "feat: 로컬 백테스트 결과를 AWS 서버로 전송하는 push_backtest_results.sh 추가"
```

---

### Task 4: 문서화 — `.env.example` / `deploy/UPDATE.md`

**Files:**
- Modify: `.env.example`
- Modify: `deploy/UPDATE.md`

**Interfaces:**
- Consumes: Task 3에서 만든 `scripts/push_backtest_results.sh`, 그 안에서 참조하는
  `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST` 키 이름.

- [ ] **Step 1: `.env.example`에 새 키 예시 추가**

`.env.example` 끝에 추가:

```
# 로컬 → AWS 백테스트 결과 이전(scripts/push_backtest_results.sh)에 쓰는 SSH 접속 정보.
# deploy/README.md 2절에서 만든 .pem 키 경로와 서버 주소를 그대로 채우면 된다.
# 설정 방법은 deploy/UPDATE.md의 "로컬 백테스트 결과 가져오기" 절 참고.
# DEPLOY_SSH_KEY_PATH=/c/Users/jungm/Downloads/upbit-server-key.pem
# DEPLOY_SERVER_HOST=ubuntu@upbit-server.tailXXXX.ts.net
```

- [ ] **Step 2: `deploy/UPDATE.md`에 새 섹션 추가**

`deploy/UPDATE.md` 끝(`## 4. 서버 세션 종료` 섹션 뒤)에 추가:

```markdown

## 5. 로컬 백테스트 결과를 서버로 가져오기

무거운 grid search(9-오실레이터 전 교차, 20,700개 조합)를 AWS 서버에서 직접 돌리면
`t4g.small`의 CPU 크레딧을 상당히 소모한다. 대신 로컬 PC에서 grid search를 돌리고,
그 결과(`data/backtest_results.db`)만 서버로 보내 "실거래 전환"에 쓸 수 있다.

### 최초 1회 설정

로컬 저장소 루트의 `.env`에 다음 두 줄을 추가한다(1절의 SSH 접속에 쓴 값과 동일):

```
DEPLOY_SSH_KEY_PATH=<다운로드한-키파일>.pem의 절대 경로
DEPLOY_SERVER_HOST=ubuntu@<탄력적 IP 또는 Tailscale MagicDNS 주소>
```

### 실행

로컬 PC(Git Bash)에서 저장소 루트로 이동한 뒤:

```bash
bash scripts/push_backtest_results.sh
```

이 한 줄이 `data/backtest_results.db`를 서버로 전송하고, 서버에서 자동으로 병합까지
실행한다. `run_id`가 백테스트 조건의 내용 기반 해시라 이미 서버에 있는 결과는 자동
건너뛰므로, 로컬에서 grid search를 새로 돌릴 때마다 이 명령을 반복 실행해도 안전하다.

실행이 끝나면 "백테스트 결과 병합 완료: 신규 N건 추가, 기존 M건 건너뜀"이 출력된다.
이후 서버 프론트엔드의 백테스트 목록에서 새로 옮겨진 결과를 확인하고, 그 결과
상세 페이지의 "실거래 전환" 버튼으로 라이브 전략을 만들 수 있다.
```

- [ ] **Step 3: 최종 확인**

Run: `python -m pytest tests/test_import_backtest_results.py -v`
Expected: `5 passed` (문서 변경만 했으므로 회귀 없어야 함)

Run: `bash -n scripts/push_backtest_results.sh`
Expected: 종료 코드 0

- [ ] **Step 4: 커밋**

```bash
git add .env.example deploy/UPDATE.md
git commit -m "docs: 로컬 백테스트 결과를 AWS 서버로 가져오는 절차 문서화"
```

---

## 구현 후 수동 검증 (자동화 테스트 범위 밖)

Task 4까지 끝난 뒤, 실제 AWS 서버가 켜져 있을 때 1회 수동으로 확인한다:

1. 로컬에서 grid search를 하나 돌리거나, 기존 `data/backtest_results.db`를 그대로 둔다.
2. `bash scripts/push_backtest_results.sh` 실행 — "신규 N건 추가" 메시지 확인.
3. 서버 SSH 접속 후 `sqlite3` 없이도 확인 가능하도록, AWS 프론트엔드
   (`http://<Tailscale 주소>:3000`)의 백테스트 목록에 방금 옮긴 결과가 보이는지
   확인.
4. 같은 명령을 한 번 더 실행 — "신규 0건 추가, 기존 N건 건너뜀"이 나오는지 확인
   (멱등성 확인).
