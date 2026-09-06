# 전략 라이브러리 로컬→AWS 푸시 스크립트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬 `trading.db`의 `regime_strategy_library` 테이블 내용을 AWS 서버의 같은 테이블로 완전 거울 동기화하는 스크립트(`bash scripts/push_regime_strategy_library.sh`)를 만든다.

**Architecture:** 로컬에서 `regime_strategy_library` 테이블만 담긴 별도의 작은 sqlite 파일을 새로 만들고(export), scp로 서버에 전송한 뒤, 서버에서 그 파일을 자신의 `trading.db`와 완전 거울 동기화(import/merge — 로컬에 없는 (market,regime)은 서버에서도 삭제)한다. 기존 `scripts/push_backtest_results.sh` / `scripts/import_backtest_results.py`의 `.env`/scp/ssh/`ATTACH DATABASE` 패턴을 그대로 따른다.

**Tech Stack:** Python(sqlite3, `trading.db`), Bash(scp/ssh), pytest

## Global Constraints

- 로컬 `trading.db` 파일 전체를 옮기지 않는다 — `regime_strategy_library` 테이블만 뽑은 별도 sqlite 파일을 만들어 옮긴다(로컬 `trading.db`에는 실거래와 무관한 개발용 `live_strategies` 등이 섞여있기 때문)
- 동기화는 **완전 거울(mirror)**이다 — 로컬에 있는 (market, regime)은 upsert하고, 로컬에 없는 (market, regime)은 서버에서도 DELETE한다(사용자 확정 요구사항)
- `regime_strategy_library`는 `live_strategies`와 외래키 관계가 없으므로, 이 푸시는 서버 daemon/백엔드 재시작이 필요 없고 오픈 포지션 여부와도 무관하게 항상 안전하다 — `deploy/update.sh`를 호출하지 않는다
- 실행 방법은 기존 관례와 동일: `bash scripts/push_regime_strategy_library.sh`(저장소 루트, Git Bash)
- SQLite에서 `INSERT INTO t SELECT ... FROM other ON CONFLICT(...) DO UPDATE ...` 형태는 `SELECT` 뒤에 `WHERE true`가 없으면 파서가 "near DO: syntax error"로 실패한다(직접 검증 완료) — 반드시 `WHERE true`를 포함한다
- `CREATE TABLE export.regime_strategy_library AS SELECT * FROM regime_strategy_library WHERE 0`로 export 쪽 테이블을 만든다(직접 검증 완료로 동작 확인) — `trading/db.py`의 `_SCHEMA` DDL을 손으로 다시 옮겨적지 않아 스키마가 나중에 바뀌어도 이 스크립트가 자동으로 따라간다
- 이 프로젝트는 스크립트를 `python scripts/xxx.py`로 직접 실행하는 것도 지원해야 한다(pytest의 `pythonpath=.` 설정에 가려지는 `PYTHONPATH` 누락 버그가 과거 4차례 태스크 리뷰를 통과한 전례가 있음, `tests/test_import_backtest_results.py`의 `test_script_runs_as_real_subprocess_entry_point`/`test_push_script_sets_pythonpath_for_remote_invocation` 참고) — 이 플랜의 신규 스크립트들도 동일한 서브프로세스 진입점 테스트를 갖는다
- 설계 스펙: `docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md` (이 플랜의 모든 세부사항은 이 스펙에서 파생됨)

---

### Task 1: export 스크립트

**Files:**
- Create: `scripts/export_regime_strategy_library.py`
- Test: `tests/test_export_regime_strategy_library.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존), `trading.db.upsert_regime_strategy_mapping()`(기존, 테스트 데이터 준비용)
- Produces: `export_regime_strategy_library(output_path: Path) -> int`(복사한 행 수 반환) — Task 2/3에서 사용하지 않지만(Task 2는 Task 1이 만든 파일 형식을 소비), `main()`이 CLI 진입점으로 Task 3의 `.sh`에서 호출됨

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_export_regime_strategy_library.py`:
```python
"""
tests/test_export_regime_strategy_library.py

scripts.export_regime_strategy_library의 export_regime_strategy_library()와
main()을 검증한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import trading.db as trading_db
from scripts.export_regime_strategy_library import export_regime_strategy_library, main

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_export_copies_all_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    trading_db.upsert_regime_strategy_mapping(
        "KRW-ETH", "하락", source_run_id="run-2", timeframe="minutes30",
        buy_conditions_json='{"a":1}', sell_conditions_json='{"b":2}',
    )
    output_path = tmp_path / "export.db"

    count = export_regime_strategy_library(output_path)

    assert count == 2
    assert output_path.exists()
    import sqlite3
    conn = sqlite3.connect(output_path)
    conn.row_factory = sqlite3.Row
    rows = {(r["market"], r["regime"]): dict(r) for r in conn.execute("SELECT * FROM regime_strategy_library")}
    conn.close()
    assert rows[("KRW-BTC", "상승")]["source_run_id"] == "run-1"
    assert rows[("KRW-ETH", "하락")]["buy_conditions_json"] == '{"a":1}'


def test_export_returns_zero_for_empty_library(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    output_path = tmp_path / "export.db"

    count = export_regime_strategy_library(output_path)

    assert count == 0
    assert output_path.exists()


def test_export_overwrites_existing_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    output_path = tmp_path / "export.db"
    output_path.write_text("stale content that is not a valid sqlite file")

    count = export_regime_strategy_library(output_path)

    assert count == 0  # 라이브러리가 비어있으므로 0건, 하지만 유효한 sqlite 파일로 덮어써짐
    import sqlite3
    conn = sqlite3.connect(output_path)
    conn.execute("SELECT * FROM regime_strategy_library")  # 예외 없이 통과해야 함(유효한 스키마)
    conn.close()


def test_main_prints_export_count(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    output_path = tmp_path / "export.db"
    monkeypatch.setattr(sys, "argv", ["export_regime_strategy_library.py", str(output_path)])

    main()

    output = capsys.readouterr().out
    assert "1건" in output
    assert str(output_path) in output


def test_script_runs_as_real_subprocess_entry_point(tmp_path):
    """`python scripts/export_regime_strategy_library.py <path>`를 실제 서브프로세스로
    직접 실행해 trading.db import가 실패하지 않는지 검증한다(PYTHONPATH 누락 버그는
    pytest의 pythonpath=. 설정에 가려져 인프로세스 테스트로는 못 잡는다 —
    tests/test_import_backtest_results.py의 동일 이름 테스트와 같은 이유)."""
    output_path = tmp_path / "export.db"
    result = subprocess.run(
        [sys.executable, "scripts/export_regime_strategy_library.py", str(output_path)],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_export_regime_strategy_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.export_regime_strategy_library'`

- [ ] **Step 3: 최소 구현 작성**

`scripts/export_regime_strategy_library.py`:
```python
"""
scripts/export_regime_strategy_library.py

로컬 trading.db의 regime_strategy_library 테이블 행만 별도의 작은 sqlite
파일로 뽑아낸다. trading.db 파일 전체를 옮기지 않는 이유는
docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md
참고(로컬 trading.db에는 실거래와 무관한 개발용 live_strategies 등이 섞여있음).
"CREATE TABLE ... AS SELECT ... WHERE 0"로 export 쪽 테이블을 만들어서,
trading/db.py의 스키마가 나중에 바뀌어도 이 스크립트가 자동으로 따라간다.
Run: PYTHONPATH=. python scripts/export_regime_strategy_library.py data/_export_regime_strategy_library.db
"""
from __future__ import annotations

import argparse
from pathlib import Path

import trading.db as trading_db


def export_regime_strategy_library(output_path: Path) -> int:
    """regime_strategy_library 전체 행을 output_path의 새 sqlite 파일로 복사한다.
    output_path가 이미 있으면 지우고 새로 만든다. 반환값은 복사한 행 수."""
    if output_path.exists():
        output_path.unlink()

    conn = trading_db._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS export", (str(output_path),))
        try:
            conn.execute(
                "CREATE TABLE export.regime_strategy_library AS "
                "SELECT * FROM regime_strategy_library WHERE 0"
            )
            conn.execute(
                "INSERT INTO export.regime_strategy_library SELECT * FROM regime_strategy_library"
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM export.regime_strategy_library"
            ).fetchone()[0]
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

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_export_regime_strategy_library.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add scripts/export_regime_strategy_library.py tests/test_export_regime_strategy_library.py
git commit -m "feat: 전략 라이브러리 export 스크립트 추가"
```

---

### Task 2: import(거울 동기화) 스크립트

**Files:**
- Create: `scripts/import_regime_strategy_library.py`
- Test: `tests/test_import_regime_strategy_library.py`

**Interfaces:**
- Consumes: `trading.db._connect()`(기존), `trading.db.upsert_regime_strategy_mapping()`(기존, 테스트 데이터 준비용), `scripts.export_regime_strategy_library.export_regime_strategy_library()`(Task 1 — incoming 파일을 만드는 데 재사용)
- Produces: `mirror_regime_strategy_library(incoming_path: Path) -> dict[str, int]`(`{"upserted", "deleted"}`) — Task 3의 `.sh`가 CLI로 호출

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_import_regime_strategy_library.py`:
```python
"""
tests/test_import_regime_strategy_library.py

scripts.import_regime_strategy_library의 mirror_regime_strategy_library()와
main()을 검증한다. incoming 파일은 Task 1의 export_regime_strategy_library()로
만든다(실제 파이프라인과 동일한 파일 형식 보장).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import trading.db as trading_db
from scripts.export_regime_strategy_library import export_regime_strategy_library
from scripts.import_regime_strategy_library import main, mirror_regime_strategy_library

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_incoming(tmp_path: Path, name: str, mappings: list[tuple[str, str, str]]) -> Path:
    """mappings의 각 (market, regime, source_run_id)를 별도 "로컬" DB_PATH에 저장한 뒤
    export하여 incoming 파일을 만든다. 이 함수 호출 동안 trading_db.DB_PATH를 건드리고
    반드시 호출부가 그 뒤에 원하는 DB_PATH로 다시 monkeypatch해야 한다(픽스처 격리는
    monkeypatch가 테스트 종료 시 자동으로 원복하므로 호출 순서만 주의하면 된다)."""
    from pytest import MonkeyPatch
    mp = MonkeyPatch()
    try:
        source_db = tmp_path / f"{name}_source.db"
        mp.setattr(trading_db, "DB_PATH", source_db)
        for market, regime, source_run_id in mappings:
            trading_db.upsert_regime_strategy_mapping(
                market, regime, source_run_id=source_run_id, timeframe="minutes60",
                buy_conditions_json="{}", sell_conditions_json="{}",
            )
        incoming_path = tmp_path / f"{name}.db"
        export_regime_strategy_library(incoming_path)
        return incoming_path
    finally:
        mp.undo()


def test_mirror_inserts_new_rows_from_incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "run-1")])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 1, "deleted": 0}
    rows = trading_db.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["source_run_id"] == "run-1"


def test_mirror_updates_existing_row_when_value_differs(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="old-run", timeframe="minutes30",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "new-run")])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 1, "deleted": 0}
    rows = trading_db.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["source_run_id"] == "new-run"


def test_mirror_deletes_rows_missing_from_incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    trading_db.upsert_regime_strategy_mapping(
        "KRW-ETH", "하락", source_run_id="run-2", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "run-1")])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 1, "deleted": 1}
    rows = trading_db.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["market"] == "KRW-BTC"


def test_mirror_deletes_all_rows_when_incoming_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 0, "deleted": 1}
    assert trading_db.list_regime_strategy_mappings() == []


def test_main_raises_when_incoming_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    monkeypatch.setattr(sys, "argv", ["import_regime_strategy_library.py", str(tmp_path / "nope.db")])

    with pytest.raises(SystemExit, match="입력 파일이 없습니다"):
        main()


def test_main_deletes_incoming_file_after_successful_merge(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "run-1")])
    monkeypatch.setattr(sys, "argv", ["import_regime_strategy_library.py", str(incoming)])

    main()

    assert not incoming.exists()
    output = capsys.readouterr().out
    assert "1건 반영" in output
    assert "임시 파일 삭제" in output


def test_main_warns_when_incoming_is_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [])
    monkeypatch.setattr(sys, "argv", ["import_regime_strategy_library.py", str(incoming)])

    main()

    output = capsys.readouterr().out
    assert "경고" in output
    assert "삭제했습니다" in output


def test_script_runs_as_real_subprocess_entry_point():
    """python scripts/import_regime_strategy_library.py <path>를 실제 서브프로세스로
    직접 실행해 trading.db import가 실패하지 않는지 검증한다(PYTHONPATH 누락 버그는
    pytest의 pythonpath=. 설정에 가려져 인프로세스 테스트로는 못 잡는다)."""
    result = subprocess.run(
        [sys.executable, "scripts/import_regime_strategy_library.py", "/nonexistent/nope.db"],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "입력 파일이 없습니다" in (result.stdout + result.stderr)
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_import_regime_strategy_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.import_regime_strategy_library'`

- [ ] **Step 3: 최소 구현 작성**

`scripts/import_regime_strategy_library.py`:
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

            if incoming_keys:
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

주의: `incoming_keys`가 비어있을 때(라이브러리가 완전히 빈 상태로 export된 경우)
`INSERT INTO ... SELECT * FROM incoming.regime_strategy_library WHERE true ON CONFLICT...`
를 실행해도 SELECT 결과가 0행이라 안전하게 아무 일도 안 하지만, 위 구현처럼
`if incoming_keys:`로 아예 건너뛰어 불필요한 문장 실행을 피한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_import_regime_strategy_library.py -v`
Expected: PASS (8 passed)

전체 회귀 확인도 같이 실행:
Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전부 통과(알려진 무관 flake 1건 제외)

- [ ] **Step 5: 커밋**

```bash
git add scripts/import_regime_strategy_library.py tests/test_import_regime_strategy_library.py
git commit -m "feat: 전략 라이브러리 거울 동기화(import) 스크립트 추가"
```

---

### Task 3: 푸시 오케스트레이션 스크립트 + 문서

**Files:**
- Create: `scripts/push_regime_strategy_library.sh`
- Modify: `deploy/UPDATE.md` (섹션 5 "로컬 백테스트 결과를 서버로 가져오기" 다음에 신규 섹션 6 추가)
- Test: `tests/test_import_regime_strategy_library.py`(Task 2에서 만든 파일에 이어서 추가)

**Interfaces:**
- Consumes: `scripts/export_regime_strategy_library.py`(Task 1, CLI로 호출), `scripts/import_regime_strategy_library.py`(Task 2, 서버에서 CLI로 호출), 저장소 루트 `.env`의 `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST`(기존, `push_backtest_results.sh`가 이미 사용 중)
- Produces: `bash scripts/push_regime_strategy_library.sh` — 최종 사용자 진입점, 이후 태스크 없음

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_import_regime_strategy_library.py` 파일 끝에 추가:
```python
def test_push_script_sets_pythonpath_for_local_and_remote_invocation():
    """push_regime_strategy_library.sh가 로컬 export 실행과 서버 ssh 실행 양쪽에
    PYTHONPATH=.를 빠뜨리면 trading.db import가 실패한다(위 서브프로세스 테스트가
    검증하는 바로 그 문제) — 이 테스트는 그 방지책이 스크립트 안에 실제로
    남아있는지 직접 확인한다(push_backtest_results.sh의 동일 이름 테스트와 같은
    이유, tests/test_import_backtest_results.py 참고)."""
    script = (REPO_ROOT / "scripts" / "push_regime_strategy_library.sh").read_text(encoding="utf-8")
    assert script.count("PYTHONPATH=.") >= 2  # 로컬 export 1회 + 원격 ssh 1회


def test_push_script_references_both_helper_scripts():
    script = (REPO_ROOT / "scripts" / "push_regime_strategy_library.sh").read_text(encoding="utf-8")
    assert "export_regime_strategy_library.py" in script
    assert "import_regime_strategy_library.py" in script


def test_push_script_never_restarts_server_daemon():
    """이 푸시는 regime_strategy_library 단일 테이블만 건드리는 데이터 동기화이지,
    코드 배포가 아니다 — update.sh/systemctl을 호출하면 daemon이 재시작되어
    실시간 손절/익절 감시가 끊기므로(project convention), 이 스크립트에는 그런
    호출이 있으면 안 된다."""
    script = (REPO_ROOT / "scripts" / "push_regime_strategy_library.sh").read_text(encoding="utf-8")
    assert "update.sh" not in script
    assert "systemctl" not in script
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_import_regime_strategy_library.py -v -k push_script`
Expected: FAIL with `FileNotFoundError`(스크립트가 아직 없음)

- [ ] **Step 3: 최소 구현 작성**

`scripts/push_regime_strategy_library.sh` (신규, 실행 권한 필요 — Step 5에서 `chmod +x` 확인):
```bash
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
```

`deploy/UPDATE.md`의 "## 5. 로컬 백테스트 결과를 서버로 가져오기" 섹션이
끝나는 지점(그 섹션 마지막 문단 "...라이브 전략을 만들 수 있다." 바로 다음)에
새 섹션 추가:

```markdown

## 6. 로컬 전략 라이브러리를 서버로 동기화하기

`/strategy-library` 화면(코인별 하락/횡보/상승/기본 전략 매핑)에서 로컬에
설정한 내용을 서버의 실거래 DB로 그대로 반영한다. 5절과 같은 `.env`
설정(`DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST`)을 그대로 쓰므로 별도
설정은 필요 없다.

**이 동작은 완전 거울(mirror) 동기화다** — 로컬에서 슬롯을 삭제한 뒤
실행하면 서버 쪽 해당 슬롯도 함께 삭제된다. 로컬 `/strategy-library`
화면이 "진실의 원천"이라고 생각하고 쓴다.

(이 절을 처음 실행하기 전에는 1~2절로 서버 코드를 최신으로 갱신해둬야 한다 —
`scripts/import_regime_strategy_library.py`가 서버에 있어야 이 명령이 동작한다.)

로컬 PC(Git Bash)에서 저장소 루트로 이동한 뒤:

```bash
bash scripts/push_regime_strategy_library.sh
```

이 한 줄이 로컬 `regime_strategy_library` 테이블만 뽑아 서버로 전송하고,
서버에서 자동으로 거울 동기화까지 실행한다(`live_strategies` 등 다른
테이블에는 전혀 영향을 주지 않으며, 서버 daemon/백엔드 재시작도 하지
않는다). 로컬에서 라이브러리를 편집할 때마다 반복 실행해도 안전하다.

실행이 끝나면 "전략 라이브러리 동기화 완료: N건 반영, K건 삭제"가
출력된다. 로컬 라이브러리가 완전히 비어있는 상태로 실행하면 서버의
모든 매핑이 삭제되었다는 경고가 대신 출력된다.
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_import_regime_strategy_library.py -v -k push_script`
Expected: PASS (3 passed)

- [ ] **Step 5: 실행 권한 부여 및 전체 회귀 확인**

```bash
chmod +x scripts/push_regime_strategy_library.sh
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
```
Expected: 전부 통과(알려진 무관 flake 1건 제외)

이 스크립트 자체는 실제 AWS 서버가 있어야 end-to-end로 실행해볼 수 있으므로
(`.env`의 `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST` 필요), 이 태스크에서는
자동화 테스트(스크립트 내용 검증)와 문법 확인만 하고, 실제 서버로의 전송은
사용자가 필요할 때 직접 실행한다.

Run: `bash -n scripts/push_regime_strategy_library.sh`
Expected: 문법 오류 없이 조용히 종료(exit 0)

- [ ] **Step 6: 커밋**

```bash
git add scripts/push_regime_strategy_library.sh deploy/UPDATE.md tests/test_import_regime_strategy_library.py
git commit -m "feat: 전략 라이브러리 AWS 푸시 오케스트레이션 스크립트 + 문서 추가"
```

## 완료 기준

- `bash scripts/push_regime_strategy_library.sh` 실행 시 (.env 설정이 돼
  있다는 가정하에) 로컬 `regime_strategy_library` 내용이 AWS 서버의
  `trading.db`에 완전 거울 동기화된다
- 로컬에서 슬롯을 제거한 뒤 다시 푸시하면 서버의 해당 슬롯도 삭제된다
  (`test_mirror_deletes_rows_missing_from_incoming`으로 검증)
- 신규 유닛 테스트 전부 통과, 기존 테스트 스위트 회귀 없음
- `deploy/UPDATE.md`에 실행 방법이 문서화됨
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체 통과
