# 코인별 장세 전략 자동 발굴 파이프라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코인 하나를 지정하면 하락/횡보/상승 장세 각각에 대해 최근 구간 탐지 →
기간 자동조정 → grid search(6개 카테고리) → 거래횟수 기준 후보선정 → TP8%/SL5%
OR조건 부착 → `regime_strategy_library` 매핑까지 자동으로 수행하는 CLI 스크립트를
만든다.

**Architecture:** 단일 파일 `scripts/regime_strategy_pipeline.py`에 순수 함수(기간
조정/거래횟수 기준/TP-SL 증강)와 기존 모듈(`backend.regime_adx_service`,
`scripts.grid_search`, `engine.runner`, `engine.cache`, `trading.db`) 재사용 래퍼
함수를 쌓아 올리고, 마지막에 이들을 엮는 `run_pipeline()`/`main()`을 붙인다.
`scripts/grid_search.py`, `scripts/augment_search.py`와 동일하게 FastAPI 서버 없이
in-process로 직접 실행되는 독립 스크립트다.

**Tech Stack:** Python(FastAPI 프로젝트 백엔드 재사용), pytest, pandas/backtrader
기반 기존 백테스트 엔진, sqlite(`trading.db`)

## Global Constraints

- 모든 python/pytest 실행은 `PYTHONPATH=. PYTHONIOENCODING=utf-8`를 앞에 붙인다
  (Windows 콘솔 인코딩/모듈 경로 요구사항, 기존 스크립트 전부 동일)
- 코드 주석/식별자는 기존 관례대로 한글 설명 + 영어 식별자 혼용, 새 함수의
  docstring도 프로젝트 기존 스타일(한글, 근거/설계 문서 링크)을 따른다
- 스킬 파일(`.claude/skills/**/*.md`)은 한국어로 작성한다(사용자 전역 CLAUDE.md 규칙)
- 각 태스크 완료 후 커밋 메시지 끝에 다음 트레일러를 반드시 포함한다:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
  ```
- 이 프로젝트는 항상 `main`에서 직접 작업하고, 전체 구현이 끝나면 병합 방식을
  묻지 않고 커밋+`git push`까지 진행한다(프로젝트 CLAUDE.md 규칙) — 단, 이번
  실행은 다음 세션에서 태스크 단위로 진행되므로 각 태스크 종료 시 개별 커밋만
  하고, 전체 푸시는 마지막 태스크(9번) 완료 시 수행한다
- 원본 스펙: `docs/superpowers/specs_v2/2026-09-06-regime-strategy-auto-pipeline-design.md`
  (모든 함수 시그니처/상수값은 이 문서와 일치해야 한다)

---

## File Structure

- **Create: `scripts/regime_strategy_pipeline.py`** — 파이프라인 전체 로직(세그먼트
  탐지, 기간조정, grid search 실행 래퍼, 후보선정, TP/SL 증강, 저장+라이브러리
  매핑, CLI 진입점). `scripts/grid_search.py`/`scripts/augment_search.py`와 같은
  "단일 파일 스크립트" 관례를 따른다 — 여러 모듈로 쪼개지 않는다.
- **Create: `tests/test_regime_strategy_pipeline.py`** — 위 스크립트의 순수 함수는
  직접, 외부 의존(백테스트 엔진/DB/grid search)이 있는 함수는 monkeypatch로
  격리해 테스트한다.
- **Create: `.claude/skills/regime-strategy-pipeline/SKILL.md`** — "장세전략
  파이프라인 <코인>" 같은 요청을 스크립트 실행으로 연결하는 얇은 스킬.

---

### Task 1: 기간 조정 + 최소 거래횟수 순수 함수

**Files:**
- Create: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Produces: `min_trades_for_days(period_days: float) -> int`,
  `adjust_window(seg: dict, min_days: int, history_start: datetime) -> tuple[datetime, datetime]`
  (`seg`는 `{"start": ISO문자열, "end": ISO문자열, ...}` 형태 — 뒤 태스크가 그대로 재사용)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_strategy_pipeline.py` 새로 생성:

```python
from datetime import datetime, timezone

from scripts.regime_strategy_pipeline import adjust_window, min_trades_for_days


def test_min_trades_for_days_boundary():
    assert min_trades_for_days(1) == 3
    assert min_trades_for_days(5) == 3
    assert min_trades_for_days(15) == 3
    assert min_trades_for_days(20) == 4


def test_adjust_window_keeps_already_long_enough_segment():
    seg = {"start": "2026-06-01T00:00:00+00:00", "end": "2026-06-20T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, tzinfo=timezone.utc)


def test_adjust_window_widens_short_segment_by_pulling_start_back():
    seg = {"start": "2026-06-15T00:00:00+00:00", "end": "2026-06-20T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, tzinfo=timezone.utc)


def test_adjust_window_clamps_to_history_start():
    seg = {"start": "2026-01-05T00:00:00+00:00", "end": "2026-01-08T00:00:00+00:00"}
    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    start, end = adjust_window(seg, min_days=10, history_start=history_start)

    assert start == history_start
    assert end == datetime(2026, 1, 8, tzinfo=timezone.utc)
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError` 또는 `ImportError: cannot import name 'adjust_window'`

- [ ] **Step 3: 최소 구현 작성**

`scripts/regime_strategy_pipeline.py` 새로 생성:

```python
"""
scripts/regime_strategy_pipeline.py

코인 하나를 지정하면 하락/횡보/상승 장세 각각에 대해 최근 구간을 찾아 grid
search로 전략을 발굴하고, 손절/익절 OR조건을 부착한 뒤 regime_strategy_library에
매핑까지 자동으로 수행한다. 라이브 전략 생성/자동스왑 토글은 범위 밖 — 사용자가
/strategy-library에서 최종 확인 후 수동으로 진행한다. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-regime-strategy-auto-pipeline-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
     --market KRW-ETH --history-start 2026-01-01
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

TIMEFRAME = "minutes60"


def min_trades_for_days(period_days: float) -> int:
    """기간이 길수록 요구 거래횟수도 늘어난다(5일당 1회, 최소 3회)."""
    return max(3, math.ceil(period_days / 5))


def adjust_window(seg: dict, min_days: int, history_start: datetime) -> tuple[datetime, datetime]:
    """세그먼트 길이가 min_days 미만이면 start를 당겨 채운다. end는 항상 세그먼트의
    end 그대로 두고(사용자가 지정한 장세가 "끝난 시점"은 그대로 유지), history_start
    보다 앞으로는 당기지 않는다(요청한 데이터 범위 밖으로 나가지 않기 위함)."""
    end = datetime.fromisoformat(seg["end"])
    start = datetime.fromisoformat(seg["start"])
    min_start = end - timedelta(days=min_days)
    if start > min_start:
        start = max(min_start, history_start)
    return start, end
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (4개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - 기간조정/최소거래횟수 순수함수

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 2: 장세 세그먼트 선택

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Consumes: `backend.regime_adx_service.compute_adx_regime_history(market: str, timeframe: str) -> dict`
  (기존 함수, `{"market", "timeframe", "bars", "segments"}` 반환. `segments`의 각
  원소는 `{"start": ISO문자열, "end": ISO문자열, "label": "상승"|"하락"|"횡보", "bar_count": int}`)
- Produces: `select_target_segments(market: str, history_start: datetime) -> dict[str, dict | None]`
  (키는 `"하락"`/`"횡보"`/`"상승"` 고정 3개, 값은 세그먼트 dict 또는 `None`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_strategy_pipeline.py`에 추가:

```python
from scripts.regime_strategy_pipeline import select_target_segments


def test_select_target_segments_picks_latest_per_label_and_filters_by_history_start(monkeypatch):
    fake_history = {
        "segments": [
            {"start": "2026-02-01T00:00:00+00:00", "end": "2026-02-10T00:00:00+00:00", "label": "상승", "bar_count": 240},
            {"start": "2026-03-01T00:00:00+00:00", "end": "2026-03-15T00:00:00+00:00", "label": "상승", "bar_count": 360},
            {"start": "2025-12-01T00:00:00+00:00", "end": "2025-12-20T00:00:00+00:00", "label": "하락", "bar_count": 480},
        ]
    }
    monkeypatch.setattr(
        "scripts.regime_strategy_pipeline.compute_adx_regime_history",
        lambda market, timeframe: fake_history,
    )

    result = select_target_segments("KRW-ETH", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert result["상승"]["start"] == "2026-03-01T00:00:00+00:00"  # 두 개 중 더 최근 것
    assert result["하락"] is None  # history_start 이전이라 제외
    assert result["횡보"] is None  # 세그먼트 자체가 없음
```

파일 상단 import에 `from datetime import datetime, timezone` 이미 있으면 `timezone`만
추가.

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'select_target_segments'`

- [ ] **Step 3: 최소 구현 작성**

`scripts/regime_strategy_pipeline.py`에 추가(import는 파일 상단에):

```python
from backend.regime_adx_service import compute_adx_regime_history
```

```python
def select_target_segments(market: str, history_start: datetime) -> dict[str, dict | None]:
    """라벨(하락/횡보/상승)별 history_start 이후 시작하는 가장 최근 세그먼트를
    고른다. 해당 라벨의 세그먼트가 없으면 None."""
    history = compute_adx_regime_history(market, TIMEFRAME)
    by_label: dict[str, dict] = {}
    for seg in history["segments"]:
        if datetime.fromisoformat(seg["start"]) < history_start:
            continue
        label = seg["label"]
        if label not in by_label or seg["end"] > by_label[label]["end"]:
            by_label[label] = seg
    return {label: by_label.get(label) for label in ("하락", "횡보", "상승")}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (5개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - 라벨별 최근 세그먼트 선택

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 3: TP/SL OR조건 증강

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Produces: `augment_with_tp_sl(sell_group: dict, stop_loss_pct: float, take_profit_pct: float) -> dict`
  (`sell_group`은 기존 ConditionGroup 형태 `{"type": "AND"|"OR", "conditions": [...]}`.
  반환값도 동일 형태, `type`은 항상 `"OR"`)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from scripts.regime_strategy_pipeline import augment_with_tp_sl


def test_augment_with_tp_sl_preserves_base_and_adds_or_blocks():
    base_sell = {
        "type": "AND",
        "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
    }

    result = augment_with_tp_sl(base_sell, stop_loss_pct=-5, take_profit_pct=8)

    assert result["type"] == "OR"
    assert result["conditions"][0] == base_sell
    assert result["conditions"][1] == {
        "indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": -5,
    }
    assert result["conditions"][2] == {
        "indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": 8,
    }
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'augment_with_tp_sl'`

- [ ] **Step 3: 최소 구현 작성**

```python
def augment_with_tp_sl(sell_group: dict, stop_loss_pct: float, take_profit_pct: float) -> dict:
    """원래 매도조건에 손절/익절 OR조건을 얹는다. STOP_LOSS_PCT/TAKE_PROFIT_PCT는
    포지션 진입가 대비 수익률로 평가되는 기존 조건트리 지표(engine/condition_tree.py의
    POSITION_RELATIVE_INDICATORS)라 그대로 재사용한다."""
    return {
        "type": "OR",
        "conditions": [
            sell_group,
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": stop_loss_pct},
            {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": take_profit_pct},
        ],
    }
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (6개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - TP/SL OR조건 증강 함수

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 4: 거래횟수 필터 + 후보 선정

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Consumes: `scripts.grid_search.dedup_top_results(results: list[dict], top_n: int) -> list[dict]`
  (기존 함수 — 동일 거래시퀀스 dedup 후 수익률 내림차순 상위 top_n개, 거래 0건은
  이미 내부에서 제외)
- Produces: `top_candidates(results: list[dict], min_trades: int, pool_size: int) -> list[dict]`
  (`results`의 각 원소는 `compute_grid_results*`가 반환하는 형태:
  `{"return_pct", "buy_block", "sell_block", "trades", "final_value"}`)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from scripts.regime_strategy_pipeline import top_candidates


def _grid_result(return_pct: float, n_trades: int) -> dict:
    return {
        "return_pct": return_pct,
        "buy_block": {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        "trades": [{"entryTime": f"t{i}", "exitTime": f"t{i}x"} for i in range(n_trades)],
        "final_value": 1_000_000 * (1 + return_pct / 100),
    }


def test_top_candidates_filters_by_min_trades_then_sorts_by_return():
    results = [_grid_result(10.0, 2), _grid_result(5.0, 5), _grid_result(20.0, 1)]

    candidates = top_candidates(results, min_trades=3, pool_size=10)

    assert len(candidates) == 1
    assert candidates[0]["return_pct"] == 5.0
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'top_candidates'`

- [ ] **Step 3: 최소 구현 작성**

파일 상단 import 추가:

```python
from scripts.grid_search import dedup_top_results
```

```python
def top_candidates(results: list[dict], min_trades: int, pool_size: int) -> list[dict]:
    """거래횟수 미달 결과를 먼저 버리고, 남은 것에 기존 dedup_top_results를 적용해
    수익률 내림차순 상위 pool_size개를 돌려준다."""
    filtered = [r for r in results if len(r["trades"]) >= min_trades]
    return dedup_top_results(filtered, pool_size)
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (7개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - 거래횟수 필터+후보 선정

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 5: Grid search 실행 래퍼

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Consumes: `scripts.grid_search.build_condition_grid(pool, market)`,
  `backend.main._fetch_backtest_dataframe(market, timeframe, start, end, buy_group, sell_group)`,
  `scripts.grid_search._check_candle_warmup(df, buy_conditions, sell_conditions)`,
  `scripts.grid_search.compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config)`,
  `engine.sweep.DEFAULT_RISK_CONFIG`(전부 기존 함수/상수)
- Produces: `run_grid_for_window(market: str, start: datetime, end: datetime, capital: float) -> dict`
  반환값 `{"df": DataFrame, "risk_config": dict, "results": list[dict]}` — 뒤 태스크
  (6, 7)가 `df`/`risk_config`를 재사용해 캔들을 두 번 조회하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_run_grid_for_window_uses_all_six_categories_and_given_capital(monkeypatch):
    calls = {}

    def fake_build_condition_grid(pool, market=None):
        calls["pool"] = pool
        calls["market"] = market
        return (
            [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
            [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
        )

    fake_df = object()

    def fake_fetch(market, timeframe, start, end, buy_group, sell_group):
        calls["fetch_args"] = (market, timeframe, start, end)
        return fake_df

    def fake_check_warmup(df, buy_conditions, sell_conditions):
        calls["warmup_checked"] = True

    def fake_compute_parallel(df, buy_conditions, sell_conditions, risk_config):
        calls["risk_config"] = risk_config
        return [{"return_pct": 1.0, "trades": []}]

    monkeypatch.setattr("scripts.regime_strategy_pipeline.build_condition_grid", fake_build_condition_grid)
    monkeypatch.setattr("scripts.regime_strategy_pipeline._fetch_backtest_dataframe", fake_fetch)
    monkeypatch.setattr("scripts.regime_strategy_pipeline._check_candle_warmup", fake_check_warmup)
    monkeypatch.setattr("scripts.regime_strategy_pipeline.compute_grid_results_parallel", fake_compute_parallel)

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 20, tzinfo=timezone.utc)

    grid = run_grid_for_window("KRW-ETH", start, end, capital=10_000_000)

    assert calls["pool"]["categories"] == ["오실레이터", "추세", "가격대", "거래량", "거래대금", "시장 심리"]
    assert calls["market"] == "KRW-ETH"
    assert calls["warmup_checked"] is True
    assert calls["risk_config"]["initial_capital"] == 10_000_000
    assert grid["df"] is fake_df
    assert grid["results"] == [{"return_pct": 1.0, "trades": []}]
```

`from scripts.regime_strategy_pipeline import run_grid_for_window` 를 파일 상단
import 목록에 추가.

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_grid_for_window'`

- [ ] **Step 3: 최소 구현 작성**

파일 상단 import 추가:

```python
from scripts.grid_search import build_condition_grid, compute_grid_results_parallel, _check_candle_warmup
from backend.main import _fetch_backtest_dataframe
from engine.sweep import DEFAULT_RISK_CONFIG

ALL_CATEGORIES = ["오실레이터", "추세", "가격대", "거래량", "거래대금", "시장 심리"]
```

```python
def run_grid_for_window(market: str, start: datetime, end: datetime, capital: float) -> dict:
    """grid search를 실행하고, 이후 단계(후보 재검증/최종 저장)가 그대로 재사용할
    df/risk_config까지 함께 반환한다(같은 df로 캔들을 두 번 조회하지 않기 위함)."""
    pool = {"categories": ALL_CATEGORIES, "excluded_indicators": []}
    buy_conditions, sell_conditions = build_condition_grid(pool, market=market)
    df = _fetch_backtest_dataframe(
        market, TIMEFRAME, start, end,
        {"type": "AND", "conditions": buy_conditions},
        {"type": "AND", "conditions": sell_conditions},
    )
    _check_candle_warmup(df, buy_conditions, sell_conditions)
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": capital}
    results = compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config)
    return {"df": df, "risk_config": risk_config, "results": results}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (8개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - grid search 실행 래퍼(6개 카테고리)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 6: 최종 전략 선정(TP/SL 증강 후 재검증)

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Consumes: `scripts.grid_search._wrap_condition(block, base_group, combinator) -> dict`(기존),
  `engine.runner.run_backtest(df, strategy_cls, risk_config, strategy_params) -> dict`(기존,
  `{"trades": list, "final_value": float, ...}` 반환), `engine.condition_strategy.ConditionTreeStrategy`(기존),
  `augment_with_tp_sl`(Task 3)
- Produces: `pick_final_strategy(df, candidates: list[dict], risk_config: dict, min_trades: int, stop_loss_pct: float, take_profit_pct: float) -> dict | None`
  반환값(성공 시) `{"buy_conditions", "sell_conditions", "return_pct", "trades", "raw_return_pct", "raw_trade_count"}`
  — Task 7이 그대로 소비한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from scripts.regime_strategy_pipeline import pick_final_strategy


def _candidate(return_pct: float, n_trades: int) -> dict:
    return {
        "buy_block": {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
        "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        "return_pct": return_pct,
        "trades": [{"entryTime": f"t{i}", "exitTime": f"t{i}x"} for i in range(n_trades)],
    }


def test_pick_final_strategy_accepts_first_passing_candidate(monkeypatch):
    call_count = {"n": 0}

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        call_count["n"] += 1
        return {"trades": [{"pnl": 1.0}] * 5, "final_value": 1_100_000}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4), _candidate(8.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert call_count["n"] == 1
    assert final["raw_return_pct"] == 10.0
    assert final["raw_trade_count"] == 4
    assert final["return_pct"] == 10.0
    assert final["sell_conditions"]["type"] == "OR"


def test_pick_final_strategy_falls_through_when_first_fails_trade_count(monkeypatch):
    responses = [
        {"trades": [{"pnl": 1.0}], "final_value": 1_010_000},
        {"trades": [{"pnl": 1.0}] * 5, "final_value": 1_080_000},
    ]

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        return responses.pop(0)

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4), _candidate(8.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert final["raw_return_pct"] == 8.0


def test_pick_final_strategy_returns_none_when_all_candidates_fail(monkeypatch):
    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params):
        return {"trades": [{"pnl": 1.0}], "final_value": 1_010_000}

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest", fake_run_backtest)
    candidates = [_candidate(10.0, 4)]
    risk_config = {"initial_capital": 1_000_000}

    final = pick_final_strategy(None, candidates, risk_config, min_trades=3, stop_loss_pct=-5, take_profit_pct=8)

    assert final is None
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'pick_final_strategy'`

- [ ] **Step 3: 최소 구현 작성**

파일 상단 import 추가:

```python
from scripts.grid_search import _wrap_condition
from engine.runner import run_backtest
from engine.condition_strategy import ConditionTreeStrategy
```

```python
def pick_final_strategy(
    df, candidates: list[dict], risk_config: dict, min_trades: int,
    stop_loss_pct: float, take_profit_pct: float,
) -> dict | None:
    """후보를 수익률 내림차순으로 순회하며 TP/SL 증강 후에도 거래횟수를 만족하는
    첫 번째를 채택한다. 전부 실패하면 None."""
    for cand in candidates:
        buy_group = _wrap_condition(cand["buy_block"], None, "AND")
        base_sell_group = _wrap_condition(cand["sell_block"], None, "AND")
        augmented_sell = augment_with_tp_sl(base_sell_group, stop_loss_pct, take_profit_pct)
        result = run_backtest(
            df, ConditionTreeStrategy, risk_config,
            {"buy_conditions": buy_group, "sell_conditions": augmented_sell},
        )
        if len(result["trades"]) >= min_trades:
            return_pct = (
                (result["final_value"] - risk_config["initial_capital"])
                / risk_config["initial_capital"] * 100
            )
            return {
                "buy_conditions": buy_group, "sell_conditions": augmented_sell,
                "return_pct": return_pct, "trades": result["trades"],
                "raw_return_pct": cand["return_pct"], "raw_trade_count": len(cand["trades"]),
            }
    return None
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (11개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - TP/SL 증강 후 최종 전략 선정

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 7: 저장 + 전략 라이브러리 매핑

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Consumes: `engine.cache.run_backtest_cached(df, strategy_cls, risk_config, market, timeframe, start, end, strategy_params, title, description) -> dict`
  (기존, `{"run_id": str, ...}` 반환), `trading.db.upsert_regime_strategy_mapping(market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json) -> None`(기존, 3단계 구현)
- Produces: `save_and_map(market, regime, start, end, final: dict, df, risk_config, stop_loss_pct, take_profit_pct) -> str`(저장된 `run_id`)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from scripts.regime_strategy_pipeline import save_and_map


def test_save_and_map_builds_title_and_maps_to_library(monkeypatch):
    captured = {}

    def fake_run_backtest_cached(**kwargs):
        captured["cached_kwargs"] = kwargs
        return {"run_id": "abc123"}

    def fake_upsert(market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json):
        captured["upsert_args"] = {
            "market": market, "regime": regime, "source_run_id": source_run_id,
            "timeframe": timeframe,
        }

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_backtest_cached", fake_run_backtest_cached)
    monkeypatch.setattr("trading.db.upsert_regime_strategy_mapping", fake_upsert)

    final = {
        "buy_conditions": {"type": "AND", "conditions": []},
        "sell_conditions": {"type": "OR", "conditions": []},
        "return_pct": 12.5, "trades": [{}] * 5,
        "raw_return_pct": 10.0, "raw_trade_count": 4,
    }
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 20, tzinfo=timezone.utc)
    risk_config = {"initial_capital": 1_000_000}

    run_id = save_and_map(
        "KRW-ETH", "상승", start, end, final,
        df=None, risk_config=risk_config, stop_loss_pct=-5, take_profit_pct=8,
    )

    assert run_id == "abc123"
    assert captured["cached_kwargs"]["title"] == "[상승] KRW-ETH 2026-06-01~2026-06-20 그리드+TP8%/SL5%"
    assert "10.00%(4건)" in captured["cached_kwargs"]["description"]
    assert "12.50%(5건)" in captured["cached_kwargs"]["description"]
    assert captured["upsert_args"]["market"] == "KRW-ETH"
    assert captured["upsert_args"]["regime"] == "상승"
    assert captured["upsert_args"]["source_run_id"] == "abc123"
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'save_and_map'`

- [ ] **Step 3: 최소 구현 작성**

파일 상단 import 추가:

```python
import json

import trading.db as trading_db
from engine.cache import run_backtest_cached
```

```python
def save_and_map(
    market: str, regime: str, start: datetime, end: datetime, final: dict,
    df, risk_config: dict, stop_loss_pct: float, take_profit_pct: float,
) -> str:
    title = (
        f"[{regime}] {market} {start.date()}~{end.date()} "
        f"그리드+TP{take_profit_pct}%/SL{abs(stop_loss_pct)}%"
    )
    description = (
        f"regime_strategy_pipeline - {market}/{TIMEFRAME}/{start.date()}~{end.date()}, "
        f"원본 수익률 {final['raw_return_pct']:+.2f}%({final['raw_trade_count']}건) -> "
        f"TP/SL 부착 후 {final['return_pct']:+.2f}%({len(final['trades'])}건)"
    )
    saved = run_backtest_cached(
        df=df, strategy_cls=ConditionTreeStrategy, risk_config=risk_config,
        market=market, timeframe=TIMEFRAME, start=start, end=end,
        strategy_params={
            "buy_conditions": final["buy_conditions"],
            "sell_conditions": final["sell_conditions"],
        },
        title=title, description=description,
    )
    trading_db.upsert_regime_strategy_mapping(
        market, regime, source_run_id=saved["run_id"], timeframe=TIMEFRAME,
        buy_conditions_json=json.dumps(final["buy_conditions"]),
        sell_conditions_json=json.dumps(final["sell_conditions"]),
    )
    return saved["run_id"]
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (12개 테스트 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - 결과 저장+전략 라이브러리 매핑

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 8: CLI + 오케스트레이션(`run_pipeline`/`main`)

**Files:**
- Modify: `scripts/regime_strategy_pipeline.py`
- Test: `tests/test_regime_strategy_pipeline.py`

**Interfaces:**
- Consumes: Task 1~7의 모든 함수, `engine.regime_adx_constants.MAJOR_MARKETS`(기존),
  `fastapi.HTTPException`(기존, `backend.main._fetch_backtest_dataframe`가 던짐)
- Produces: `parse_args(argv=None) -> argparse.Namespace`,
  `_ensure_supported_market(market: str) -> None`(미지원 마켓이면 `SystemExit`),
  `run_pipeline(market, history_start, capital, min_days, stop_loss_pct, take_profit_pct, candidate_pool) -> list[dict]`
  (라벨별 요약 dict 리스트, 각 원소는 `{"regime", "status": "mapped"|"skipped"|"failed", ...}`),
  `print_summary_table(summary: list[dict]) -> None`, `main() -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from scripts.regime_strategy_pipeline import (
    _ensure_supported_market, parse_args, print_summary_table, run_pipeline,
)


def test_ensure_supported_market_rejects_unknown_market():
    with pytest.raises(SystemExit):
        _ensure_supported_market("KRW-NOTREAL")


def test_ensure_supported_market_accepts_major_market():
    _ensure_supported_market("KRW-ETH")


def test_parse_args_defaults():
    args = parse_args(["--market", "KRW-ETH", "--history-start", "2026-01-01"])

    assert args.market == "KRW-ETH"
    assert args.history_start == "2026-01-01"
    assert args.capital == 10_000_000
    assert args.min_days == 10
    assert args.stop_loss_pct == -5.0
    assert args.take_profit_pct == 8.0
    assert args.candidate_pool == 20


def test_run_pipeline_isolates_label_failures(monkeypatch):
    segments = {
        "하락": None,
        "횡보": {"start": "2026-02-01T00:00:00+00:00", "end": "2026-02-10T00:00:00+00:00", "label": "횡보", "bar_count": 240},
        "상승": {"start": "2026-03-01T00:00:00+00:00", "end": "2026-03-15T00:00:00+00:00", "label": "상승", "bar_count": 360},
    }
    monkeypatch.setattr("scripts.regime_strategy_pipeline.select_target_segments", lambda market, history_start: segments)

    def fake_run_grid_for_window(market, start, end, capital):
        # adjust_window가 짧은 세그먼트(횡보, 9일<min_days10)의 start를 1월로
        # 당기므로 start가 아니라 항상 고정인 end로 라벨을 구분한다.
        if end.month == 2:
            raise SystemExit("워밍업 부족")
        return {
            "df": None, "risk_config": {"initial_capital": capital},
            "results": [{"return_pct": 5.0, "trades": [{}, {}, {}]}],
        }

    monkeypatch.setattr("scripts.regime_strategy_pipeline.run_grid_for_window", fake_run_grid_for_window)
    monkeypatch.setattr(
        "scripts.regime_strategy_pipeline.top_candidates",
        lambda results, min_trades, pool: [{
            "return_pct": 5.0, "trades": [{}, {}, {}],
            "buy_block": {"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30},
            "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        }],
    )
    monkeypatch.setattr(
        "scripts.regime_strategy_pipeline.pick_final_strategy",
        lambda df, candidates, risk_config, min_trades, sl, tp: {
            "buy_conditions": {}, "sell_conditions": {}, "return_pct": 6.0, "trades": [{}, {}, {}],
            "raw_return_pct": 5.0, "raw_trade_count": 3,
        },
    )
    monkeypatch.setattr("scripts.regime_strategy_pipeline.save_and_map", lambda *a, **k: "run-xyz")

    history_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    summary = run_pipeline("KRW-ETH", history_start, 10_000_000, 10, -5.0, 8.0, 20)

    by_regime = {row["regime"]: row for row in summary}
    assert by_regime["하락"]["status"] == "skipped"
    assert by_regime["하락"]["reason"] == "탐지된 구간 없음"
    assert by_regime["횡보"]["status"] == "failed"
    assert "워밍업 부족" in by_regime["횡보"]["reason"]
    assert by_regime["상승"]["status"] == "mapped"
    assert by_regime["상승"]["run_id"] == "run-xyz"


def test_print_summary_table_smoke(capsys):
    summary = [
        {"regime": "하락", "status": "skipped", "reason": "탐지된 구간 없음"},
        {
            "regime": "상승", "status": "mapped", "run_id": "abc",
            "period": "2026-03-01~2026-03-15", "return_pct": 12.34, "trade_count": 5,
        },
    ]

    print_summary_table(summary)

    captured = capsys.readouterr()
    assert "하락" in captured.out
    assert "상승" in captured.out
    assert "12.34" in captured.out
```

`import pytest` 가 파일 상단에 없으면 추가.

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name '_ensure_supported_market'`

- [ ] **Step 3: 최소 구현 작성**

파일 상단 import 추가:

```python
import argparse
from datetime import timezone

from fastapi import HTTPException

from engine.regime_adx_constants import MAJOR_MARKETS
```

```python
def _ensure_supported_market(market: str) -> None:
    if market not in MAJOR_MARKETS:
        raise SystemExit(f"{market}은(는) 지원하지 않는 마켓입니다.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="장세별 전략 자동 발굴 파이프라인")
    parser.add_argument("--market", required=True, help="마켓코드 (예: KRW-ETH)")
    parser.add_argument("--history-start", required=True, help="시작일 YYYY-MM-DD (이 날짜 이후 세그먼트만 사용)")
    parser.add_argument("--capital", type=float, default=10_000_000)
    parser.add_argument("--min-days", type=int, default=10)
    parser.add_argument("--stop-loss-pct", type=float, default=-5.0)
    parser.add_argument("--take-profit-pct", type=float, default=8.0)
    parser.add_argument("--candidate-pool", type=int, default=20)
    return parser.parse_args(argv)


def run_pipeline(
    market: str, history_start: datetime, capital: float, min_days: int,
    stop_loss_pct: float, take_profit_pct: float, candidate_pool: int,
) -> list[dict]:
    segments = select_target_segments(market, history_start)
    summary: list[dict] = []
    for regime, seg in segments.items():
        if seg is None:
            summary.append({"regime": regime, "status": "skipped", "reason": "탐지된 구간 없음"})
            continue
        try:
            start, end = adjust_window(seg, min_days, history_start)
            period_days = (end - start).days
            min_trades = min_trades_for_days(period_days)

            grid = run_grid_for_window(market, start, end, capital)
            candidates = top_candidates(grid["results"], min_trades, candidate_pool)
            if not candidates:
                summary.append({"regime": regime, "status": "skipped", "reason": "거래횟수 조건을 만족하는 후보 없음"})
                continue

            final = pick_final_strategy(
                grid["df"], candidates, grid["risk_config"], min_trades,
                stop_loss_pct, take_profit_pct,
            )
            if final is None:
                summary.append({"regime": regime, "status": "skipped", "reason": "TP/SL 부착 후 거래횟수 조건을 만족하는 후보 없음"})
                continue

            run_id = save_and_map(
                market, regime, start, end, final,
                grid["df"], grid["risk_config"], stop_loss_pct, take_profit_pct,
            )
            summary.append({
                "regime": regime, "status": "mapped", "run_id": run_id,
                "period": f"{start.date()}~{end.date()}",
                "return_pct": round(final["return_pct"], 2),
                "trade_count": len(final["trades"]),
            })
        except (HTTPException, SystemExit) as exc:
            summary.append({"regime": regime, "status": "failed", "reason": str(exc)})
    return summary


def print_summary_table(summary: list[dict]) -> None:
    print(f"{'장세':6}{'상태':10}{'기간':24}{'수익률':>10}{'거래':>6}  비고")
    for row in summary:
        period = row.get("period", "-")
        return_pct = f"{row['return_pct']:+.2f}%" if "return_pct" in row else "-"
        trade_count = str(row.get("trade_count", "-"))
        reason = row.get("reason", row.get("run_id", ""))
        print(f"{row['regime']:6}{row['status']:10}{period:24}{return_pct:>10}{trade_count:>6}  {reason}")


def main() -> None:
    args = parse_args()
    _ensure_supported_market(args.market)
    history_start = datetime.strptime(args.history_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    summary = run_pipeline(
        args.market, history_start, args.capital, args.min_days,
        args.stop_loss_pct, args.take_profit_pct, args.candidate_pool,
    )
    print_summary_table(summary)
    print(f"RESULT_JSON: {json.dumps({'market': args.market, 'segments': summary}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_strategy_pipeline.py -v`
Expected: PASS (17개 테스트 전부)

- [ ] **Step 5: 전체 테스트 스위트 회귀 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전체 PASS, 기존 테스트 실패 없음

- [ ] **Step 6: 커밋**

```bash
git add scripts/regime_strategy_pipeline.py tests/test_regime_strategy_pipeline.py
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 - CLI 진입점 + 오케스트레이션

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
```

---

### Task 9: 스킬 래퍼 + 최종 확인 + 푸시

**Files:**
- Create: `.claude/skills/regime-strategy-pipeline/SKILL.md`

**Interfaces:**
- Consumes: Task 8의 `scripts/regime_strategy_pipeline.py` CLI(`--market`, `--history-start` 등)
- Produces: 없음(문서 파일)

- [ ] **Step 1: 스킬 파일 작성**

`.claude/skills/regime-strategy-pipeline/SKILL.md` 새로 생성:

```markdown
---
name: regime-strategy-pipeline
description: 코인 하나를 지정하면 장세(하락/횡보/상승)별 grid search를 자동으로
  돌려 TP/SL을 부착한 최종 전략을 전략 라이브러리에 매핑한다. "장세전략 파이프라인
  <코인>", "<코인> 전략 자동발굴" 같은 요청에 사용한다.
---

# 장세 전략 자동 발굴 파이프라인

설계 문서: `docs/superpowers/specs_v2/2026-09-06-regime-strategy-auto-pipeline-design.md`

1. 사용자가 준 코인명을 마켓코드(`KRW-XXX`)로 변환한다(`engine/regime_adx_constants.py`의
   `MAJOR_MARKETS` 참고). 목록에 없는 코인이면 지원하지 않는다고 안내하고 중단한다.
2. 로컬에서 이미 실행 중인 grid search(웹 탭 `/grid-search`의 job 큐)가 있는지
   사용자에게 확인한다 — 있으면 멀티프로세싱 워커 리소스가 겹치니 끝난 뒤
   실행하라고 안내한다.
3. `--history-start`는 사용자가 명시하지 않으면 이번 달 1일로 기본값을 잡되,
   실행 전 사용자에게 확인한다.
4. 다음 명령을 `run_in_background: true`로 실행한다(코인 하나당 grid search가
   6개 카테고리 전체를 도는데 수 시간~십수 시간 걸릴 수 있다):
   ```
   PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
       --market <마켓코드> --history-start <YYYY-MM-DD>
   ```
5. 완료되면 stdout의 `RESULT_JSON:` 라인을 파싱해 라벨별 결과(매핑됨/스킵됨/
   실패, 기간, 수익률, 거래횟수, 사유)를 표로 정리해 사용자에게 보여준다.
6. 마지막에 반드시 안내한다: "라이브 배포는 이 파이프라인의 범위 밖입니다 —
   `/strategy-library`에서 결과를 확인하고, 만족스러우면 라이브 전략을 만들거나
   기존 전략에 자동스왑을 켜주세요."
```

- [ ] **Step 2: 스킬 파일 검토**

`Read` 도구로 방금 작성한 `.claude/skills/regime-strategy-pipeline/SKILL.md`를 다시
읽어 frontmatter(`name`/`description`)와 본문 번호목록이 깨지지 않았는지 확인한다.

- [ ] **Step 3: 전체 테스트 스위트 최종 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전체 PASS

- [ ] **Step 4: 커밋 + 푸시**

```bash
git add .claude/skills/regime-strategy-pipeline/SKILL.md
git commit -m "$(cat <<'EOF'
feat: 장세 전략 파이프라인 스킬 래퍼 추가

코인명만 주면 scripts/regime_strategy_pipeline.py를 실행해주는 얇은 스킬.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XQTeBv4oDdG91N8RRoykAU
EOF
)"
git push
```

- [ ] **Step 5: 실제 코인으로 수동 검증(다음 세션 또는 사용자 몫)**

`PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py
--market KRW-ETH --history-start 2026-01-01`을 실제로 실행해(수 시간 소요) 콘솔
요약과 `/strategy-library` 탭의 KRW-ETH 행에 매핑이 반영되는지 브라우저로 확인한다.
이 스텝은 자동화 테스트 범위 밖이라 체크리스트로만 남겨둔다.

---

## 최종 완료 기준 (스펙과 동일)

- `python scripts/regime_strategy_pipeline.py --market KRW-ETH --history-start 2026-01-01`
  실행 시 하락/횡보/상승 각 라벨에 대해 세그먼트 탐지 → 기간 조정 → grid search
  (6개 카테고리) → 거래횟수 필터 → TP8%/SL5% 증강 → `regime_strategy_library` 매핑까지
  자동 완료
- 콘솔에 라벨별 요약 표 + `RESULT_JSON:` 라인 출력
- `/strategy-library` 탭에서 KRW-ETH 행의 하락/횡보/상승 슬롯에 매핑 결과가 반영된
  것을 확인 가능(브라우저 수동 검증)
- `--market`을 바꿔 다른 `MAJOR_MARKETS` 코인에도 동일하게 재사용 가능
- 신규 스킬 파일로 "코인명만 주면" 트리거 가능
- 신규 유닛 테스트 전부 통과, 기존 테스트 스위트 회귀 없음
