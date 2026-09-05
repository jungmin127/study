# Grid Search 워커 풀 (메모리 누적 크래시 방지 + 병렬화) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scripts/grid_search.py`가 20,700개 조합을 순차 실행하며 `backtrader.Cerebro()` 반복
인스턴스화로 메모리를 선형 누적하다 크래시하는 문제를, `multiprocessing.Pool(maxtasksperchild=K)`
기반 워커 4개 병렬 풀로 교체해 해결한다. 동시에 캔들 워밍업 부족 크래시도 사전 체크로 막는다.

**Architecture:** 순수 로직(`_run_one_combo`, `_check_candle_warmup`, `_watchdog_expired`)은 계속
단독 함수로 분리해 유닛 테스트하고, `multiprocessing.Pool` 오케스트레이션(`compute_grid_results_parallel`)은
이 저장소 관례대로 수동 스모크 테스트로 검증한다. 기존 `compute_grid_results`(순차 버전)는
그대로 남겨 기존 테스트를 건드리지 않는다 — `main()`만 병렬 버전을 쓰도록 바꾼다.

**Tech Stack:** Python 3.11.9, `multiprocessing`(표준 라이브러리, 새 의존성 없음), 기존
`engine.condition_tree.max_required_period` 재사용.

## Global Constraints

- 스펙: `docs/superpowers/specs_v1/2026-08-02-grid-search-worker-pool-design.md` (커밋 `2b92bea`).
- Python 3.11.9. 새 pip 의존성 추가 금지 — 캘리브레이션 스크립트의 메모리 측정은 `ctypes` +
  Windows API(`GetProcessMemoryInfo`)로 한다(psutil 미설치 확인됨).
- Windows는 `multiprocessing`이 `spawn` 방식이라, 워커에 전달되는 함수는 반드시 모듈
  최상위에 정의해야 pickle 가능하다(중첩 함수/람다 금지). `main()`은 이미
  `if __name__ == "__main__":` 가드 안에서 호출되므로 `Pool` 생성도 그 안에서만 이뤄져야 한다.
- 워커 수(`WORKER_COUNT=4`), 워치독 타임아웃(`WATCHDOG_TIMEOUT_SEC=300`)은 CLI 플래그로 노출하지
  않고 모듈 상수로 고정한다(스펙에서 승인됨, YAGNI).
- `engine/runner.py`, `engine/condition_tree.py`는 수정하지 않는다(순수 재사용).
- 캘리브레이션 스크립트는 저장소에 커밋하지 않는다 — 측정 후 삭제.
- 기존 `tests/test_grid_search.py`의 모든 테스트는 이 플랜 완료 후에도 수정 없이 그대로
  통과해야 한다(`compute_grid_results`/`dedup_top_results`/`build_condition_grid` 동작 불변).

---

### Task 1: `_run_one_combo` 추출

**Files:**
- Modify: `scripts/grid_search.py:95-136` (`compute_grid_results` 내부 로직 추출)
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Produces: `_run_one_combo(df: pd.DataFrame, risk_config: dict, buy_block: dict, sell_block: dict) -> dict`
  — 반환값 `{"return_pct": float, "buy_block": dict, "sell_block": dict, "trades": list[dict], "final_value": float}`.
  Task 4(캘리브레이션 스크립트)와 Task 5(`_run_one_combo_worker`)가 이 함수를 그대로 재사용한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py` 맨 위 import에 `_run_one_combo` 추가:

```python
from scripts.grid_search import (
    build_condition_grid,
    compute_grid_results,
    dedup_top_results,
    _run_one_combo,
)
```

파일 끝에 추가:

```python
def test_run_one_combo_returns_expected_shape():
    df = make_oscillating_df(n=200)
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": 1_000_000}
    buy_block = {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}
    sell_block = {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}

    result = _run_one_combo(df, risk_config, buy_block, sell_block)

    assert set(result.keys()) == {"return_pct", "buy_block", "sell_block", "trades", "final_value"}
    assert result["buy_block"] == buy_block
    assert result["sell_block"] == sell_block
    assert isinstance(result["trades"], list)
    assert isinstance(result["return_pct"], float)
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py::test_run_one_combo_returns_expected_shape -v`
Expected: FAIL with `ImportError: cannot import name '_run_one_combo'`

- [x] **Step 3: 최소 구현**

`scripts/grid_search.py`의 `compute_grid_results` 함수(현재 95-136행) 전체를 아래로 교체한다:

```python
def _run_one_combo(df, risk_config: dict, buy_block: dict, sell_block: dict) -> dict:
    """조합 하나(매수 블록 1개 x 매도 블록 1개)에 대해 run_backtest를 1회 호출한다.

    순차 실행(compute_grid_results)과 병렬 워커(compute_grid_results_parallel) 양쪽에서
    공유하는 단일 진입점 — 조합당 실제로 무엇을 계산하는지는 여기 한 곳에만 있다.
    """
    buy_group = {"type": "AND", "conditions": [buy_block]}
    sell_group = {"type": "AND", "conditions": [sell_block]}
    result = run_backtest(
        df,
        ConditionTreeStrategy,
        risk_config,
        {"buy_conditions": buy_group, "sell_conditions": sell_group},
    )
    initial_capital = float(risk_config.get("initial_capital", 10000))
    return_pct = (result["final_value"] - initial_capital) / initial_capital * 100
    return {
        "return_pct": return_pct,
        "buy_block": buy_block,
        "sell_block": sell_block,
        "trades": result["trades"],
        "final_value": result["final_value"],
    }


def compute_grid_results(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 순차로 계산한다(테스트/소규모 실행용).

    대규모 실행(main())은 compute_grid_results_parallel을 쓴다.

    Returns:
        각 조합의 결과 딕셔너리 리스트: _run_one_combo와 동일한 shape.
    """
    results: list[dict] = []
    total = len(buy_conditions) * len(sell_conditions)

    for i, buy_block in enumerate(buy_conditions):
        for sell_block in sell_conditions:
            results.append(_run_one_combo(df, risk_config, buy_block, sell_block))
        if (i + 1) % 5 == 0 or (i + 1) == len(buy_conditions):
            done = (i + 1) * len(sell_conditions)
            print(f"    매수조건 {i + 1}/{len(buy_conditions)} 완료 ({done}/{total}건)", flush=True)

    return results
```

- [x] **Step 4: 통과 확인 (신규 + 기존 전체)**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py -v`
Expected: 전부 PASS (기존 12개 + 신규 1개 = 13개) — `compute_grid_results`를 쓰는 기존 테스트
2개(`test_compute_grid_results_runs_every_combo`, `test_compute_grid_results_pairs_every_buy_with_every_sell`)도
동작 변화 없이 그대로 통과해야 한다.

- [x] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "refactor: extract _run_one_combo from compute_grid_results"
```

---

### Task 2: 캔들 워밍업 사전 체크

**Files:**
- Modify: `scripts/grid_search.py` (import 추가, 새 함수, `main()` 통합)
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: `engine.condition_tree.max_required_period(group: dict) -> int` (기존 함수, 변경 없음)
- Produces: `_check_candle_warmup(df, buy_conditions: list[dict], sell_conditions: list[dict]) -> None`
  — 캔들 수가 부족하면 `SystemExit`를 던진다. `main()`이 그리드 생성 직후, 계산 시작 전에 호출한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py` 맨 위에 `import pytest` 추가(현재 없음), import 목록에
`_check_candle_warmup` 추가. 파일 끝에 추가:

```python
def test_check_candle_warmup_raises_when_insufficient():
    df = make_oscillating_df(n=10)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    with pytest.raises(SystemExit):
        _check_candle_warmup(df, buy_conditions, sell_conditions)


def test_check_candle_warmup_passes_when_sufficient():
    df = make_oscillating_df(n=200)
    buy_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]
    sell_conditions = [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]
    _check_candle_warmup(df, buy_conditions, sell_conditions)
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py::test_check_candle_warmup_raises_when_insufficient -v`
Expected: FAIL with `ImportError: cannot import name '_check_candle_warmup'`

- [x] **Step 3: 최소 구현**

`scripts/grid_search.py`의 import 섹션(11-22행)에 추가:

```python
from engine.condition_tree import max_required_period
```

`_run_one_combo` 함수 바로 위에 새 함수 추가:

```python
def _check_candle_warmup(df, buy_conditions: list[dict], sell_conditions: list[dict]) -> None:
    """그리드에 등장하는 파라미터의 최대 워밍업 봉 수보다 캔들이 적으면 명확한 에러로 중단한다.

    사전 체크 없이 계산을 시작하면 backtrader 내부에서 IndexError로 불명확하게 죽는다."""
    all_buy_group = {"type": "AND", "conditions": buy_conditions}
    all_sell_group = {"type": "AND", "conditions": sell_conditions}
    required_bars = max(max_required_period(all_buy_group), max_required_period(all_sell_group))
    if len(df) < required_bars:
        raise SystemExit(
            f"선택된 그리드가 최소 {required_bars}개의 봉을 필요로 하지만, "
            f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리세요."
        )
```

`main()`에서 `build_condition_grid()` 호출 및 조합 수 출력 직후, `compute_grid_results` 호출
전에 추가:

```python
    buy_conditions, sell_conditions = build_condition_grid()
    total_combos = len(buy_conditions) * len(sell_conditions)
    print(
        f"[2] 매수 조건 {len(buy_conditions)}개 x 매도 조건 {len(sell_conditions)}개 = 총 {total_combos:,}개 조합",
        flush=True,
    )
    _check_candle_warmup(df, buy_conditions, sell_conditions)
```

- [x] **Step 4: 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py -v`
Expected: 전부 PASS (15개)

- [x] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: reject grid search when candle count is below required warmup"
```

---

### Task 3: 워치독 타임아웃 판정 함수

**Files:**
- Modify: `scripts/grid_search.py`
- Test: `tests/test_grid_search.py`

**Interfaces:**
- Produces: `_watchdog_expired(last_progress_time: float, now: float, timeout_sec: float) -> bool`.
  Task 5의 `compute_grid_results_parallel`이 폴링 루프에서 이 함수로 "워커 응답 없음"을 판정한다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_grid_search.py` import에 `_watchdog_expired` 추가. 파일 끝에 추가:

```python
def test_watchdog_expired_when_timeout_exceeded():
    assert _watchdog_expired(last_progress_time=0.0, now=301.0, timeout_sec=300) is True


def test_watchdog_not_expired_within_timeout():
    assert _watchdog_expired(last_progress_time=0.0, now=299.0, timeout_sec=300) is False


def test_watchdog_not_expired_exactly_at_timeout():
    assert _watchdog_expired(last_progress_time=0.0, now=300.0, timeout_sec=300) is False
```

- [x] **Step 2: 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py::test_watchdog_expired_when_timeout_exceeded -v`
Expected: FAIL with `ImportError: cannot import name '_watchdog_expired'`

- [x] **Step 3: 최소 구현**

`_check_candle_warmup` 함수 바로 아래에 추가:

```python
def _watchdog_expired(last_progress_time: float, now: float, timeout_sec: float) -> bool:
    """마지막 진행(워커 결과 완료) 이후 timeout_sec를 초과했으면 True.

    워커가 죽어서 응답이 없는 상황을 감지하기 위한 순수 판정 함수."""
    return (now - last_progress_time) > timeout_sec
```

- [x] **Step 4: 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py -v`
Expected: 전부 PASS (18개)

- [x] **Step 5: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: add pure watchdog timeout predicate for parallel grid search"
```

---

### Task 4: K(재시작 주기) 캘리브레이션

**Files:**
- Create (임시, 커밋 안 함): `_calibrate_grid_search_memory.py` (저장소 루트)
- Modify: `scripts/grid_search.py` (상수 4개 추가)

**Interfaces:**
- Consumes: `_run_one_combo`(Task 1), `build_condition_grid`(기존)
- Produces: `WORKER_COUNT`, `MAX_TASKS_PER_CHILD`, `WATCHDOG_TIMEOUT_SEC`, `PROGRESS_LOG_INTERVAL`
  모듈 상수. Task 5의 `compute_grid_results_parallel`이 기본값으로 사용한다.

- [x] **Step 1: 캘리브레이션 스크립트 작성**

저장소 루트에 `_calibrate_grid_search_memory.py`를 만든다(psutil 미설치 확인됨 — 새 의존성
추가하지 않고 Windows API를 `ctypes`로 직접 호출):

```python
"""
_calibrate_grid_search_memory.py

scripts/grid_search.py의 MAX_TASKS_PER_CHILD(K) 값을 정하기 위한 1회성 실측 스크립트.
9-오실레이터 그리드(build_condition_grid()) 조합을 실제로 반복 호출하며 워킹셋(RSS 근사치)
증가량을 측정한다. 저장소에 커밋하지 않는다 — 측정 후 삭제할 것.

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python _calibrate_grid_search_memory.py
"""
from __future__ import annotations

import ctypes
import time
from datetime import datetime, timezone

from engine.sweep import DEFAULT_RISK_CONFIG
from scripts.grid_search import _run_one_combo, build_condition_grid
from upbit_data_service import get_candles

N_CALLS = 3000


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _working_set_bytes() -> int:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize


def main() -> None:
    df = get_candles(
        "KRW-ETH",
        "minutes60",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    print(f"캔들 수: {len(df)}")

    buy_conditions, sell_conditions = build_condition_grid()
    combos = [(b, s) for b in buy_conditions for s in sell_conditions][:N_CALLS]
    print(f"측정 호출 수: {len(combos)}")

    start_bytes = _working_set_bytes()
    print(f"시작 워킹셋: {start_bytes / 1024 ** 2:.1f} MB")
    t0 = time.perf_counter()

    for i, (buy_block, sell_block) in enumerate(combos, start=1):
        _run_one_combo(df, DEFAULT_RISK_CONFIG, buy_block, sell_block)
        if i % 500 == 0:
            now_bytes = _working_set_bytes()
            elapsed = time.perf_counter() - t0
            print(
                f"{i}회 호출, 워킹셋 {now_bytes / 1024 ** 2:.1f} MB "
                f"(+{(now_bytes - start_bytes) / 1024 ** 2:.1f} MB), {elapsed:.1f}초 경과"
            )

    end_bytes = _working_set_bytes()
    per_call_kb = (end_bytes - start_bytes) / 1024 / len(combos)
    print(f"\n완료: {len(combos)}회, 총 증가 {(end_bytes - start_bytes) / 1024 ** 2:.1f} MB")
    print(f"호출당 평균 증가량: {per_call_kb:.2f} KB")


if __name__ == "__main__":
    main()
```

참고: 세션 중 실제 9-오실레이터 그리드(솔라나 일봉)를 라이브로 돌렸을 때 12,750회 호출 시점
누적 워킹셋이 약 2.2GB(호출당 평균 ~177KB)로, 예전 5-오실레이터/1시간봉 측정치(9,000회당
+183MB, 호출당 ~20KB)보다 훨씬 높게 나온 바 있다 — 이 스크립트의 측정치도 비슷한 자릿수로
나올 가능성이 높다.

- [x] **Step 2: 스크립트 실행**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python _calibrate_grid_search_memory.py`

출력 마지막 줄의 "호출당 평균 증가량: X.XX KB"를 기록해둔다.

- [x] **Step 3: K 계산**

아래 식으로 계산한다(16GB RAM, 워커 4개, 백엔드+프론트엔드 개발서버 상시 구동을 전제로 한
보수적 예산 배분):

```
여유 RAM(MB)        = 16384 - 6144(OS/개발서버/IDE 등 고정 오버헤드) - 512(grid_search.py 메인 프로세스)
                     = 9728
안전마진 적용(MB)    = 9728 * 0.5   (추가 50% 안전마진)
                     = 4864
워커당 예산(MB)      = 4864 / 4     (워커 4개가 분할)
                     = 1216
워커당 허용 누적치(MB) = 1216 - 300(워커 baseline: 인터프리터+pandas/backtrader import+df)
                     = 916
K = int(916 * 1024 / <Step 2에서 측정한 "호출당 평균 증가량(KB)">)
```

예: 측정치가 177KB/call이면 `K = int(916 * 1024 / 177) = 5,297`.

- [x] **Step 4: 상수 반영**

`scripts/grid_search.py`의 `PERIOD_GRID = [10, 14, 20]` 바로 위에 추가:

```python
WORKER_COUNT = 4
MAX_TASKS_PER_CHILD = <Step 3에서 계산한 정수값>
WATCHDOG_TIMEOUT_SEC = 300
PROGRESS_LOG_INTERVAL = 1000
```

- [x] **Step 5: 임시 스크립트 삭제**

```bash
rm _calibrate_grid_search_memory.py
```

- [x] **Step 6: 커밋 (상수만)**

```bash
git add scripts/grid_search.py
git commit -m "feat: add worker pool constants calibrated from measured memory growth"
```

---

### Task 5: 워커 풀 오케스트레이터 (`compute_grid_results_parallel`)

**Files:**
- Modify: `scripts/grid_search.py` (import 추가, 워커 글로벌/함수, `compute_grid_results_parallel`,
  `main()`이 병렬 버전을 쓰도록 교체)

**Interfaces:**
- Consumes: `_run_one_combo`(Task 1), `_watchdog_expired`(Task 3), `WORKER_COUNT`/
  `MAX_TASKS_PER_CHILD`/`WATCHDOG_TIMEOUT_SEC`/`PROGRESS_LOG_INTERVAL`(Task 4)
- Produces: `compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config, processes=WORKER_COUNT, max_tasks_per_child=MAX_TASKS_PER_CHILD, watchdog_timeout=WATCHDOG_TIMEOUT_SEC) -> list[dict]`
  — `compute_grid_results`와 동일한 반환 shape(순서는 보장 안 함). `main()`이 이걸 쓴다.

이 태스크는 `multiprocessing.Pool` 실제 동작을 다루므로 자동 유닛 테스트 대신 수동 스모크
테스트로 검증한다(이 저장소 기존 관례 — Pool 내부를 mock하는 건 깨지기 쉬움).

- [x] **Step 1: import 및 워커 글로벌/함수 추가**

`scripts/grid_search.py`의 import 섹션에 추가:

```python
import multiprocessing
```

`_run_one_combo` 함수 바로 아래에 추가:

```python
_worker_df = None
_worker_risk_config: dict | None = None


def _init_worker(df, risk_config: dict) -> None:
    """Pool 워커 프로세스가 (재)시작될 때마다 호출 — df/risk_config를 워커 전역에 저장해
    태스크마다 재직렬화하지 않는다."""
    global _worker_df, _worker_risk_config
    _worker_df = df
    _worker_risk_config = risk_config


def _run_one_combo_worker(buy_block: dict, sell_block: dict) -> dict:
    """Pool.apply_async에 전달되는 워커 측 진입점. 모듈 최상위 함수여야 Windows spawn에서
    pickle 가능하다."""
    return _run_one_combo(_worker_df, _worker_risk_config, buy_block, sell_block)
```

- [x] **Step 2: `compute_grid_results_parallel` 구현**

`compute_grid_results` 함수 바로 아래(`_watchdog_expired` 아래)에 추가:

```python
def compute_grid_results_parallel(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
    processes: int = WORKER_COUNT,
    max_tasks_per_child: int = MAX_TASKS_PER_CHILD,
    watchdog_timeout: float = WATCHDOG_TIMEOUT_SEC,
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 워커 풀로 병렬 계산한다(대규모 실행용).

    워커는 max_tasks_per_child번 처리하면 자동 재시작되어 backtrader의 반복 인스턴스화
    메모리 누적을 방지한다. 마지막 진행 이후 watchdog_timeout초간 응답이 없으면 워커가
    죽어서 멈춘 것으로 보고 중단한다.
    """
    combos = [(b, s) for b in buy_conditions for s in sell_conditions]
    total = len(combos)

    pool = multiprocessing.Pool(
        processes=processes,
        maxtasksperchild=max_tasks_per_child,
        initializer=_init_worker,
        initargs=(df, risk_config),
    )
    try:
        async_results = [pool.apply_async(_run_one_combo_worker, (b, s)) for b, s in combos]
        completed = [False] * total
        results: list[dict | None] = [None] * total
        done_count = 0
        last_logged = 0
        last_progress = time.monotonic()

        while done_count < total:
            progressed = False
            for i, ar in enumerate(async_results):
                if not completed[i] and ar.ready():
                    results[i] = ar.get()
                    completed[i] = True
                    done_count += 1
                    progressed = True

            if progressed:
                last_progress = time.monotonic()
                if done_count - last_logged >= PROGRESS_LOG_INTERVAL or done_count == total:
                    pct = done_count / total * 100
                    print(f"    완료 {done_count:,}/{total:,}건 ({pct:.1f}%)", flush=True)
                    last_logged = done_count
            elif _watchdog_expired(last_progress, time.monotonic(), watchdog_timeout):
                raise RuntimeError(
                    f"워커 응답 없음 — {watchdog_timeout:.0f}초간 진행 없어 중단합니다. "
                    "일부 워커가 예기치 않게 종료됐을 수 있습니다."
                )

            if done_count < total:
                time.sleep(1)

        return results
    finally:
        pool.terminate()
        pool.join()
```

- [x] **Step 3: `main()`이 병렬 버전을 쓰도록 교체**

`main()`에서 (Task 2에서 추가한 `_check_candle_warmup(...)` 호출 다음 줄) 아래 부분을:

```python
    t0 = time.perf_counter()
    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)
    elapsed = time.perf_counter() - t0
```

아래로 교체:

```python
    t0 = time.perf_counter()
    results = compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config)
    elapsed = time.perf_counter() - t0
```

- [x] **Step 4: 기존 테스트 회귀 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_grid_search.py -v`
Expected: 전부 PASS (18개, 변경 없음 — `compute_grid_results_parallel`은 자동 테스트 대상이
아니므로 이 단계에서 개수는 그대로다)

- [x] **Step 5: 소규모 수동 스모크 테스트**

작은 실제 그리드로 병렬 경로가 실제로 동작하는지 먼저 확인한다(전체 20,700개 규모는 Task 6에서):

Run:
```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-BTC --timeframe days \
  --capital 1000000 --start 2026-07-01 --end 2026-08-02 --top-n 5
```

Expected: 크래시 없이 끝까지 실행되고, 진행률 로그가 "완료 N/전체건 (X.X%)" 형식으로 찍히고,
마지막 줄에 `RESULT_JSON: {...}`가 출력된다. `saved` 리스트에 최대 5개 항목이 있어야 한다.

- [x] **Step 6: 커밋**

```bash
git add scripts/grid_search.py
git commit -m "feat: run grid search combos through a recycling worker pool with a hang watchdog"
```

---

### Task 6: 전체 규모 통합 검증

**Files:** 없음(코드 변경 없음, 검증만)

**Interfaces:** 없음

이전 세션에서 크래시가 재현됐던 것과 동일한 조건(1시간봉, 다개월치, 9-오실레이터 20,700개
조합 전체)으로 실제 실행해 이번 수정이 그 크래시를 막는지 확인한다.

- [x] **Step 1: 전체 규모 실행**

Run (백그라운드 권장, 수 분~수십 분 소요):
```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
  --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
```

- [x] **Step 2: 확인 항목**

- 크래시 없이 `RESULT_JSON`까지 도달하는지 (특히 지난번 크래시 구간이던 36~40% 지점을
  무사히 통과하는지)
- 진행률 로그가 "완료 N/20,700건 (X.X%)" 형식으로 찍히는지
- 총 소요 시간(`elapsed_sec`)을 기록 — Task 7에서 SKILL.md 문구에 반영
- (선택) 작업 관리자/`tasklist`로 워커 프로세스가 4개 떠 있는지, 재시작이 실제로 일어나는지
  확인

- [x] **Step 3: pytest 전체 스위트 최종 확인**

Run: `PYTHONPATH=. python -m pytest -v`
Expected: 전부 PASS (기존 전체 스위트 + 이 플랜에서 추가한 6개)

---

### Task 7: `SKILL.md` 문서 업데이트

**Files:**
- Modify: `.claude/skills/grid-search/SKILL.md`

**Interfaces:** 없음(문서만)

- [x] **Step 1: 소요 시간/병렬화 안내 반영**

`## 실행 절차`의 2번 항목(파싱 결과 표 + 예상 소요 시간 안내 문구)을 Task 6에서 실측한
`elapsed_sec` 기준으로 갱신한다. 예시(실측치로 교체):

```
2. 파싱 결과를 표로 정리해 사용자에게 보여주고 확인을 받는다. 이 표에는 반드시
   마켓코드/timeframe 코드/운용자금(원 단위 숫자)/시작일/종료일/상위N개가 포함되어야 한다.
   예상 소요 시간(워커 4개 병렬 실행 기준, 1시간봉 20,700개 조합에서 실측 약 <N>분. 일봉처럼
   캔들 수가 적은 timeframe은 조합당 고정 오버헤드가 있어 캔들 수만큼 비례해서 빨라지지는
   않는다)도 함께 안내한다.
```

`## 주의 사항` 아래에 추가:

```
- 워커 4개로 병렬 실행되며, 5분간 진행이 없으면(워커가 예기치 않게 종료된 것으로 판단)
  자동으로 중단되고 에러 메시지가 출력된다. 실행이 실패하면 에러 메시지를 그대로 사용자에게
  전달하라.
```

- [x] **Step 2: 커밋**

```bash
git add .claude/skills/grid-search/SKILL.md
git commit -m "docs: update grid-search SKILL.md for the parallel worker pool"
```
