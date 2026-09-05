# 세그먼트(추세 기반) 1단계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/analysis` 페이지 `세그먼트` 탭에 `규모`/`섹터`와 나란히 `추세 기반` 섹션을 추가해, 코인별 일봉 이력을 상승/하락/횡보 구간(+전반/후반 9패턴)으로 자동 분류하고 차트+표로 보여준다.

**Architecture:** 백엔드는 `engine/trend_segments.py`(순수 알고리즘: ZigZag 스윙→횡보 병합→최소기간 흡수→전반/후반 라벨링)와 `engine/cache.py`(SQLite `trend_segments` 테이블, market 단위 replace)로 나뉜다. 계산은 코인 선택 시 온디맨드로 트리거되며(`GET .../trend-segments/{market}`), "갱신" 버튼이 강제 재계산(`POST .../refresh`)을 요청한다. 프론트는 기존 `PriceChart.tsx`/`SegmentSizeTable.tsx`/`CoinSelect.tsx` 패턴을 재사용해 `TrendSegmentView.tsx`(코인 선택+오케스트레이션), `TrendSegmentChart.tsx`(lightweight-charts 캔들, 구간별 색상), `TrendSegmentTable.tsx`(구간 표)로 구성한다.

**Tech Stack:** Python(FastAPI, pandas, sqlite3), Next.js/React/TypeScript, lightweight-charts v5.

## Global Constraints

- 스펙 문서: `docs/superpowers/specs_v1/2026-08-16-trend-segment-analysis-design.md` (요구사항 원본, 상수 값의 출처).
- 봉 기준: 일봉(`"days"` 타임프레임), 상장일부터 현재까지 전체 이력.
- 계산 트리거: 전체 코인 일괄 배치 없음 — 선택한 코인만 온디맨드 계산, 캐시 있으면 재사용, "갱신"으로 강제 재계산.
- 저장은 market 단위 replace(히스토리 보관 없음, 항상 최신 1회분).
- 상수(코드 상수로 정의, 추후 튜닝 가능): `THRESHOLD_MULTIPLIER=6.0`, `MIN_THRESHOLD_PCT=5.0`, `MAX_THRESHOLD_PCT=25.0`, `SIDEWAYS_LEG_CAP_RATIO=1.5`, `MIN_SEGMENT_DAYS=14`, `HALF_THRESHOLD_RATIO=0.5`.
- Windows 개발 환경에서 dev 서버가 떠 있는 동안 `npm run build`를 실행하지 않는다(라이브 `.next`가 손상됨 — 기존에 확인된 이슈). 프론트 타입 체크는 `npx tsc --noEmit`으로 한다.
- 백엔드 테스트는 `pytest`로, `engine.cache.DB_PATH`를 `monkeypatch`+`tmp_path`로 격리한다(기존 `tests/test_cache.py`, `tests/test_backend.py` 패턴을 그대로 따른다).

---

### Task 1: `engine/cache.py` — `trend_segments` 테이블 + save/list

**Files:**
- Modify: `engine/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `save_trend_segments(market: str, rows: list[dict]) -> None`
  - `list_trend_segments(market: str) -> list[dict]` — 각 dict 키: `market, start_date, end_date, days, return_pct, trend, first_half_trend, second_half_trend, pattern_label, threshold_pct, computed_at`

- [ ] **Step 1: `_SCHEMA`에 `trend_segments` 테이블 추가**

`engine/cache.py`의 기존 `_SCHEMA += """..."""` 블록들(파일 63~95번째 줄 근처, `grid_search_jobs` 테이블 바로 뒤) 뒤에 추가:

```python
_SCHEMA += """
CREATE TABLE IF NOT EXISTS trend_segments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    market            TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    days              INTEGER NOT NULL,
    return_pct        REAL NOT NULL,
    trend             TEXT NOT NULL,
    first_half_trend  TEXT NOT NULL,
    second_half_trend TEXT NOT NULL,
    pattern_label     TEXT NOT NULL,
    threshold_pct     REAL NOT NULL,
    computed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trend_segments_market ON trend_segments(market);
"""
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_cache.py` 파일 끝에 추가 (파일 상단 import 블록에 `list_trend_segments, save_trend_segments`도 추가):

```python
def test_save_and_list_trend_segments_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    rows = [
        {
            "market": "KRW-BTC",
            "start_date": "2026-01-05",
            "end_date": "2026-03-20",
            "days": 75,
            "return_pct": 42.3,
            "trend": "up",
            "first_half_trend": "up",
            "second_half_trend": "up",
            "pattern_label": "지속형 상승",
            "threshold_pct": 8.5,
            "computed_at": "2026-08-16T00:00:00+00:00",
        },
        {
            "market": "KRW-BTC",
            "start_date": "2026-03-21",
            "end_date": "2026-04-10",
            "days": 20,
            "return_pct": 8.1,
            "trend": "up",
            "first_half_trend": "up",
            "second_half_trend": "sideways",
            "pattern_label": "상승 후 둔화",
            "threshold_pct": 8.5,
            "computed_at": "2026-08-16T00:00:00+00:00",
        },
    ]

    save_trend_segments("KRW-BTC", rows)
    result = list_trend_segments("KRW-BTC")

    assert len(result) == 2
    assert result[0]["start_date"] == "2026-01-05"
    assert result[0]["pattern_label"] == "지속형 상승"
    assert result[1]["pattern_label"] == "상승 후 둔화"


def test_save_trend_segments_replaces_only_that_market(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_trend_segments("KRW-BTC", [{
        "market": "KRW-BTC", "start_date": "2026-01-01", "end_date": "2026-02-01",
        "days": 31, "return_pct": 10.0, "trend": "up", "first_half_trend": "up",
        "second_half_trend": "up", "pattern_label": "지속형 상승",
        "threshold_pct": 8.0, "computed_at": "2026-08-16T00:00:00+00:00",
    }])
    save_trend_segments("KRW-ETH", [{
        "market": "KRW-ETH", "start_date": "2026-01-01", "end_date": "2026-02-01",
        "days": 31, "return_pct": -10.0, "trend": "down", "first_half_trend": "down",
        "second_half_trend": "down", "pattern_label": "지속형 하락",
        "threshold_pct": 9.0, "computed_at": "2026-08-16T00:00:00+00:00",
    }])
    # KRW-BTC를 다시 저장하면 KRW-ETH 행은 그대로 남아 있어야 한다.
    save_trend_segments("KRW-BTC", [{
        "market": "KRW-BTC", "start_date": "2026-02-01", "end_date": "2026-03-01",
        "days": 28, "return_pct": 5.0, "trend": "sideways", "first_half_trend": "sideways",
        "second_half_trend": "sideways", "pattern_label": "지속형 횡보",
        "threshold_pct": 8.0, "computed_at": "2026-08-16T01:00:00+00:00",
    }])

    assert len(list_trend_segments("KRW-BTC")) == 1
    assert list_trend_segments("KRW-BTC")[0]["start_date"] == "2026-02-01"
    assert len(list_trend_segments("KRW-ETH")) == 1


def test_list_trend_segments_returns_empty_list_when_not_computed(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert list_trend_segments("KRW-XRP") == []
```

- [ ] **Step 3: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_cache.py -k trend_segments -v`
Expected: FAIL with `ImportError` (`save_trend_segments`/`list_trend_segments` 없음)

- [ ] **Step 4: `save_trend_segments`/`list_trend_segments` 구현**

`engine/cache.py`의 `list_segment_classification` 함수 뒤에 추가:

```python
def save_trend_segments(market: str, rows: list[dict]) -> None:
    """추세 구간 분류 결과를 market 단위로 교체 저장한다. 히스토리는 보관하지
    않고 해당 market의 최신 1회분만 유지한다."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM trend_segments WHERE market = ?", (market,))
        conn.executemany(
            "INSERT INTO trend_segments "
            "(market, start_date, end_date, days, return_pct, trend, first_half_trend, "
            " second_half_trend, pattern_label, threshold_pct, computed_at) "
            "VALUES (:market, :start_date, :end_date, :days, :return_pct, :trend, "
            " :first_half_trend, :second_half_trend, :pattern_label, :threshold_pct, :computed_at)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def list_trend_segments(market: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT market, start_date, end_date, days, return_pct, trend, first_half_trend, "
            "       second_half_trend, pattern_label, threshold_pct, computed_at "
            "FROM trend_segments WHERE market = ? ORDER BY start_date",
            (market,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "market": r[0], "start_date": r[1], "end_date": r[2], "days": r[3],
            "return_pct": r[4], "trend": r[5], "first_half_trend": r[6],
            "second_half_trend": r[7], "pattern_label": r[8],
            "threshold_pct": r[9], "computed_at": r[10],
        }
        for r in rows
    ]
```

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_cache.py -k trend_segments -v`
Expected: PASS (3 passed)

- [ ] **Step 6: 커밋**

```bash
git add engine/cache.py tests/test_cache.py
git commit -m "feat: trend_segments 테이블 + save/list 함수 추가"
```

---

### Task 2: `engine/trend_segments.py` — ZigZag 스윙 탐지

**Files:**
- Create: `engine/trend_segments.py`
- Test: `tests/test_trend_segments.py`

**Interfaces:**
- Produces:
  - `_zigzag_pivot_indices(closes: list[float], threshold_pct: float) -> list[int]`
  - `_legs_from_pivots(closes: list[float], dates: list, pivots: list[int]) -> list[dict]` — 각 dict 키: `start_idx, end_idx, start_date, end_date, start_price, end_price, return_pct`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trend_segments.py` 신규 생성:

```python
from engine.trend_segments import _legs_from_pivots, _zigzag_pivot_indices


def test_zigzag_pivot_indices_finds_expected_swing_points():
    closes = [100, 105, 110, 115, 120, 118, 114, 108, 102, 96, 90, 95, 100, 108, 115, 122, 130]

    pivots = _zigzag_pivot_indices(closes, threshold_pct=10.0)

    assert pivots == [0, 4, 10, 16]


def test_zigzag_pivot_indices_handles_flat_series():
    closes = [100.0] * 10

    pivots = _zigzag_pivot_indices(closes, threshold_pct=10.0)

    assert pivots == [0, 9]


def test_zigzag_pivot_indices_handles_single_point():
    assert _zigzag_pivot_indices([100.0], threshold_pct=10.0) == [0]


def test_legs_from_pivots_computes_return_pct():
    closes = [100, 105, 110, 115, 120, 118, 114, 108, 102, 96, 90, 95, 100, 108, 115, 122, 130]
    dates = list(range(len(closes)))  # 실제로는 pandas Timestamp지만 테스트에서는 정수로 대체 가능
    pivots = [0, 4, 10, 16]

    legs = _legs_from_pivots(closes, dates, pivots)

    assert len(legs) == 3
    assert legs[0]["start_idx"] == 0 and legs[0]["end_idx"] == 4
    assert legs[0]["return_pct"] == pytest.approx(20.0)
    assert legs[1]["return_pct"] == pytest.approx(-25.0)
    assert legs[2]["return_pct"] == pytest.approx((130 - 90) / 90 * 100)
```

파일 맨 위에 `import pytest` 추가.

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.trend_segments'`

- [ ] **Step 3: 최소 구현 작성**

`engine/trend_segments.py` 신규 생성:

```python
"""
engine/trend_segments.py

코인별 일봉 이력을 ZigZag 스윙 기반으로 상승/하락/횡보 구간으로 분류하고,
각 구간을 전반/후반으로 나눠 9패턴으로 라벨링한다. 설계 문서:
docs/superpowers/specs_v1/2026-08-16-trend-segment-analysis-design.md
"""
from __future__ import annotations


def _zigzag_pivot_indices(closes: list[float], threshold_pct: float) -> list[int]:
    """종가 배열에서 ZigZag 스윙 고점/저점의 인덱스를 확정 순서대로 반환한다.
    항상 첫 인덱스(0)로 시작하고 마지막 인덱스로 끝난다."""
    n = len(closes)
    if n <= 1:
        return list(range(n))

    pivots = [0]
    anchor_price = closes[0]
    direction: str | None = None
    max_idx, max_price = 0, closes[0]
    min_idx, min_price = 0, closes[0]
    ext_idx, ext_price = 0, closes[0]

    for i in range(1, n):
        price = closes[i]

        if direction is None:
            if price > max_price:
                max_price, max_idx = price, i
            if price < min_price:
                min_price, min_idx = price, i
            up_pct = (max_price - anchor_price) / anchor_price * 100
            down_pct = (anchor_price - min_price) / anchor_price * 100
            if up_pct >= threshold_pct and up_pct >= down_pct:
                direction = "up"
                ext_idx, ext_price = max_idx, max_price
            elif down_pct >= threshold_pct:
                direction = "down"
                ext_idx, ext_price = min_idx, min_price
            continue

        if direction == "up":
            if price >= ext_price:
                ext_price, ext_idx = price, i
                continue
            retrace_pct = (ext_price - price) / ext_price * 100
            if retrace_pct >= threshold_pct:
                pivots.append(ext_idx)
                anchor_price = ext_price
                direction = "down"
                ext_price, ext_idx = price, i
        else:
            if price <= ext_price:
                ext_price, ext_idx = price, i
                continue
            retrace_pct = (price - ext_price) / ext_price * 100
            if retrace_pct >= threshold_pct:
                pivots.append(ext_idx)
                anchor_price = ext_price
                direction = "up"
                ext_price, ext_idx = price, i

    if pivots[-1] != n - 1:
        pivots.append(n - 1)
    return pivots


def _legs_from_pivots(closes: list[float], dates: list, pivots: list[int]) -> list[dict]:
    """확정된 스윙 인덱스 사이사이를 상승/하락 레그로 변환한다."""
    legs = []
    for a, b in zip(pivots, pivots[1:]):
        start_price = closes[a]
        end_price = closes[b]
        legs.append({
            "start_idx": a, "end_idx": b,
            "start_date": dates[a], "end_date": dates[b],
            "start_price": start_price, "end_price": end_price,
            "return_pct": (end_price - start_price) / start_price * 100,
        })
    return legs
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/trend_segments.py tests/test_trend_segments.py
git commit -m "feat: ZigZag 스윙 탐지(_zigzag_pivot_indices/_legs_from_pivots) 추가"
```

---

### Task 3: 횡보 병합 + 구간 1차 분류

**Files:**
- Modify: `engine/trend_segments.py`
- Test: `tests/test_trend_segments.py`

**Interfaces:**
- Consumes: `_legs_from_pivots`의 leg dict 형식(Task 2)
- Produces:
  - `SIDEWAYS_LEG_CAP_RATIO = 1.5` 상수
  - `_merge_sideways_runs(legs: list[dict], threshold_pct: float) -> list[list[dict]]`
  - `_run_to_segment(run: list[dict], threshold_pct: float) -> dict` — 키: `start_idx, end_idx, start_date, end_date, start_price, end_price, return_pct, trend`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trend_segments.py`에 추가:

```python
from engine.trend_segments import _merge_sideways_runs, _run_to_segment


def _leg(start_price, end_price, start_idx=0, end_idx=1):
    return {
        "start_idx": start_idx, "end_idx": end_idx,
        "start_date": start_idx, "end_date": end_idx,
        "start_price": start_price, "end_price": end_price,
        "return_pct": (end_price - start_price) / start_price * 100,
    }


def test_merge_sideways_runs_merges_low_net_change_legs():
    # leg0: 100->115(+15%), leg1: 115->104(약 -9.57%) → 누적 순변화 4% < threshold(10%) → 병합
    # leg2: 104->116(약 +11.5%) → 누적 순변화 16% >= threshold(10%) → 새 구간
    legs = [
        _leg(100, 115, 0, 1),
        _leg(115, 104, 1, 2),
        _leg(104, 116, 2, 3),
    ]

    runs = _merge_sideways_runs(legs, threshold_pct=10.0)

    assert len(runs) == 2
    assert len(runs[0]) == 2
    assert len(runs[1]) == 1


def test_merge_sideways_runs_does_not_absorb_leg_above_cap():
    # cap = threshold(10) * 1.5 = 15. leg1의 크기(20%)가 cap 이상이면 흡수하지 않고 분리.
    legs = [
        _leg(100, 104, 0, 1),   # +4%
        _leg(104, 124.8, 1, 2),  # +20% (cap 이상)
    ]

    runs = _merge_sideways_runs(legs, threshold_pct=10.0)

    assert len(runs) == 2
    assert len(runs[0]) == 1
    assert len(runs[1]) == 1


def test_run_to_segment_classifies_up_down_sideways_by_threshold():
    up_run = [_leg(100, 120, 0, 1)]
    down_run = [_leg(100, 80, 0, 1)]
    sideways_run = [_leg(100, 104, 0, 1)]

    assert _run_to_segment(up_run, threshold_pct=10.0)["trend"] == "up"
    assert _run_to_segment(down_run, threshold_pct=10.0)["trend"] == "down"
    assert _run_to_segment(sideways_run, threshold_pct=10.0)["trend"] == "sideways"
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: FAIL with `ImportError` (`_merge_sideways_runs`/`_run_to_segment` 없음)

- [ ] **Step 3: 구현 추가**

`engine/trend_segments.py`에 `_legs_from_pivots` 함수 뒤에 추가:

```python
SIDEWAYS_LEG_CAP_RATIO = 1.5


def _merge_sideways_runs(legs: list[dict], threshold_pct: float) -> list[list[dict]]:
    """연속된 레그를 훑으며 묶음 시작가 대비 누적 순변화율이 threshold_pct 미만인
    동안 계속 묶는다(순방향 진행 없이 등락만 반복되는 구간 = 횡보 후보). 단, 레그
    개별 크기가 threshold_pct * SIDEWAYS_LEG_CAP_RATIO 이상이면 강한 단일 돌파로
    보고 흡수하지 않는다."""
    if not legs:
        return []
    cap_pct = threshold_pct * SIDEWAYS_LEG_CAP_RATIO
    runs: list[list[dict]] = [[legs[0]]]
    for leg in legs[1:]:
        run = runs[-1]
        run_start_price = run[0]["start_price"]
        candidate_net_pct = abs((leg["end_price"] - run_start_price) / run_start_price * 100)
        if abs(leg["return_pct"]) < cap_pct and candidate_net_pct < threshold_pct:
            run.append(leg)
        else:
            runs.append([leg])
    return runs


def _classify_return(return_pct: float, threshold_pct: float) -> str:
    if return_pct >= threshold_pct:
        return "up"
    if return_pct <= -threshold_pct:
        return "down"
    return "sideways"


def _run_to_segment(run: list[dict], threshold_pct: float) -> dict:
    start_price = run[0]["start_price"]
    end_price = run[-1]["end_price"]
    return_pct = (end_price - start_price) / start_price * 100
    return {
        "start_idx": run[0]["start_idx"], "end_idx": run[-1]["end_idx"],
        "start_date": run[0]["start_date"], "end_date": run[-1]["end_date"],
        "start_price": start_price, "end_price": end_price,
        "return_pct": return_pct, "trend": _classify_return(return_pct, threshold_pct),
    }
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/trend_segments.py tests/test_trend_segments.py
git commit -m "feat: 횡보 병합(_merge_sideways_runs) + 구간 1차 분류(_run_to_segment) 추가"
```

---

### Task 4: 최소기간 흡수

**Files:**
- Modify: `engine/trend_segments.py`
- Test: `tests/test_trend_segments.py`

**Interfaces:**
- Consumes: `_run_to_segment`의 segment dict 형식(Task 3) — `start_idx, end_idx, start_date, end_date, start_price, end_price, return_pct, trend`
- Produces:
  - `MIN_SEGMENT_DAYS = 14` 상수
  - `_combine_segments(a: dict, b: dict, threshold_pct: float) -> dict`
  - `_absorb_short_segments(segments: list[dict], threshold_pct: float) -> list[dict]`

주의: 이 단계에서 `start_date`/`end_date`는 날짜 뺄셈이 가능한 타입(pandas Timestamp 또는 Python date/datetime)이어야 `.days` 계산이 된다. 테스트에서는 `datetime.date` 객체를 사용한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trend_segments.py` 상단 import에 `from datetime import date, timedelta` 추가. 파일에 테스트 추가:

```python
from engine.trend_segments import _absorb_short_segments, _combine_segments


def _segment(start_day, end_day, start_price, end_price, trend, start_idx=0, end_idx=1):
    return {
        "start_idx": start_idx, "end_idx": end_idx,
        "start_date": date(2026, 1, 1) + timedelta(days=start_day),
        "end_date": date(2026, 1, 1) + timedelta(days=end_day),
        "start_price": start_price, "end_price": end_price,
        "return_pct": (end_price - start_price) / start_price * 100,
        "trend": trend,
    }


def test_combine_segments_recomputes_trend_over_full_range():
    a = _segment(0, 5, 100, 130, "up", start_idx=0, end_idx=5)      # 5일, 30%
    b = _segment(5, 8, 130, 133, "sideways", start_idx=5, end_idx=8)  # 3일, ~2.3%

    combined = _combine_segments(a, b, threshold_pct=10.0)

    assert combined["start_idx"] == 0 and combined["end_idx"] == 8
    assert combined["trend"] == "up"
    assert combined["return_pct"] == pytest.approx(33.0)


def test_absorb_short_segments_merges_into_following_neighbor():
    # 가운데 구간(3일)이 MIN_SEGMENT_DAYS(14) 미만 → 다음 구간에 흡수되어야 한다.
    segments = [
        _segment(0, 20, 100, 130, "up", 0, 20),
        _segment(20, 23, 130, 132, "sideways", 20, 23),
        _segment(23, 50, 132, 90, "down", 23, 50),
    ]

    result = _absorb_short_segments(segments, threshold_pct=10.0)

    assert len(result) == 2
    assert result[0]["end_idx"] == 20
    assert result[1]["start_idx"] == 20 and result[1]["end_idx"] == 50


def test_absorb_short_segments_merges_last_into_previous():
    segments = [
        _segment(0, 20, 100, 130, "up", 0, 20),
        _segment(20, 25, 130, 132, "sideways", 20, 25),  # 5일, 마지막 구간
    ]

    result = _absorb_short_segments(segments, threshold_pct=10.0)

    assert len(result) == 1
    assert result[0]["start_idx"] == 0 and result[0]["end_idx"] == 25


def test_absorb_short_segments_keeps_single_segment_untouched():
    segments = [_segment(0, 5, 100, 101, "sideways", 0, 5)]
    result = _absorb_short_segments(segments, threshold_pct=10.0)
    assert result == segments
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 구현 추가**

`engine/trend_segments.py`에 `_run_to_segment` 함수 뒤에 추가:

```python
MIN_SEGMENT_DAYS = 14


def _combine_segments(a: dict, b: dict, threshold_pct: float) -> dict:
    first, second = (a, b) if a["start_idx"] <= b["start_idx"] else (b, a)
    start_price = first["start_price"]
    end_price = second["end_price"]
    return_pct = (end_price - start_price) / start_price * 100
    return {
        "start_idx": first["start_idx"], "end_idx": second["end_idx"],
        "start_date": first["start_date"], "end_date": second["end_date"],
        "start_price": start_price, "end_price": end_price,
        "return_pct": return_pct, "trend": _classify_return(return_pct, threshold_pct),
    }


def _absorb_short_segments(segments: list[dict], threshold_pct: float) -> list[dict]:
    """MIN_SEGMENT_DAYS 미만인 구간을 이웃 구간에 흡수한다(다음 구간 우선, 마지막
    구간이면 이전 구간). 흡수로 합쳐진 구간이 다시 짧으면 재귀적으로 계속 흡수된다."""
    segments = list(segments)
    changed = True
    while changed and len(segments) > 1:
        changed = False
        for i, seg in enumerate(segments):
            days = (seg["end_date"] - seg["start_date"]).days
            if days >= MIN_SEGMENT_DAYS:
                continue
            neighbor_i = i + 1 if i < len(segments) - 1 else i - 1
            lo, hi = sorted((i, neighbor_i))
            merged = _combine_segments(segments[lo], segments[hi], threshold_pct)
            segments = segments[:lo] + [merged] + segments[hi + 1:]
            changed = True
            break
    return segments
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/trend_segments.py tests/test_trend_segments.py
git commit -m "feat: 최소기간(14일) 미만 구간 인접 흡수(_absorb_short_segments) 추가"
```

---

### Task 5: 전반/후반 9패턴 라벨링 + `compute_trend_segments` 통합

**Files:**
- Modify: `engine/trend_segments.py`
- Test: `tests/test_trend_segments.py`

**Interfaces:**
- Consumes: Task 2~4의 모든 내부 함수
- Produces:
  - `HALF_THRESHOLD_RATIO = 0.5` 상수
  - `PATTERN_LABELS: dict[tuple[str, str], str]`
  - `_classify_half(closes: list[float], start_idx: int, end_idx: int, threshold_pct: float) -> str`
  - `compute_trend_segments(df: pandas.DataFrame, threshold_pct: float) -> list[dict]` — `df`는 `candle_time`(오름차순 정렬된 pandas Timestamp), `close` 컬럼 필요. 반환 리스트의 각 dict 키: `start_date, end_date, days, return_pct, trend, first_half_trend, second_half_trend, pattern_label` (`start_date`/`end_date`는 `"YYYY-MM-DD"` 문자열)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trend_segments.py` 상단 import에 `import pandas as pd` 추가. 파일에 추가:

```python
from engine.trend_segments import PATTERN_LABELS, _classify_half, compute_trend_segments


def test_classify_half_uses_half_threshold():
    closes = [100.0, 110.0, 96.0]
    # idx0->idx1: +10% (threshold=10, half_threshold=5 → up)
    assert _classify_half(closes, 0, 1, threshold_pct=10.0) == "up"
    # idx1->idx2: 약 -12.7% (half_threshold=5 → down)
    assert _classify_half(closes, 1, 2, threshold_pct=10.0) == "down"
    # idx0->idx0: 변화 없음 → sideways
    assert _classify_half(closes, 0, 0, threshold_pct=10.0) == "sideways"


def test_pattern_labels_cover_all_nine_combinations():
    trends = ["up", "down", "sideways"]
    for first in trends:
        for second in trends:
            assert (first, second) in PATTERN_LABELS

    assert PATTERN_LABELS[("up", "up")] == "지속형 상승"
    assert PATTERN_LABELS[("up", "sideways")] == "상승 후 둔화"
    assert PATTERN_LABELS[("sideways", "sideways")] == "지속형 횡보"


def test_compute_trend_segments_end_to_end_with_synthetic_series():
    closes = [100, 105, 110, 115, 120, 118, 114, 108, 102, 96, 90, 95, 100, 108, 115, 122, 130]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({"candle_time": dates, "close": closes})

    segments = compute_trend_segments(df, threshold_pct=10.0)

    assert len(segments) >= 1
    for seg in segments:
        assert seg["trend"] in ("up", "down", "sideways")
        assert seg["pattern_label"] == PATTERN_LABELS[(seg["first_half_trend"], seg["second_half_trend"])]
        assert seg["start_date"] < seg["end_date"]


def test_compute_trend_segments_returns_empty_list_for_empty_df():
    df = pd.DataFrame({"candle_time": pd.Series([], dtype="datetime64[ns, UTC]"), "close": pd.Series([], dtype=float)})
    assert compute_trend_segments(df, threshold_pct=10.0) == []
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 구현 추가**

`engine/trend_segments.py` 맨 위 import에 `import pandas as pd` 추가(파일 상단 `from __future__ import annotations` 바로 아래). `_absorb_short_segments` 함수 뒤에 추가:

```python
HALF_THRESHOLD_RATIO = 0.5

PATTERN_LABELS: dict[tuple[str, str], str] = {
    ("up", "up"): "지속형 상승",
    ("up", "down"): "상승 후 반전",
    ("up", "sideways"): "상승 후 둔화",
    ("down", "up"): "하락 후 반등",
    ("down", "down"): "지속형 하락",
    ("down", "sideways"): "하락 후 멈춤",
    ("sideways", "up"): "횡보 이탈(상승)",
    ("sideways", "down"): "횡보 이탈(하락)",
    ("sideways", "sideways"): "지속형 횡보",
}


def _classify_half(closes: list[float], start_idx: int, end_idx: int, threshold_pct: float) -> str:
    start_price = closes[start_idx]
    end_price = closes[end_idx]
    return_pct = (end_price - start_price) / start_price * 100
    return _classify_return(return_pct, threshold_pct * HALF_THRESHOLD_RATIO)


def compute_trend_segments(df: pd.DataFrame, threshold_pct: float) -> list[dict]:
    """일봉 df(candle_time 오름차순, close 컬럼)를 상승/하락/횡보 구간으로
    분류하고, 구간마다 전반/후반 9패턴 라벨을 붙인다."""
    if df.empty:
        return []

    closes = df["close"].tolist()
    dates = df["candle_time"].tolist()

    pivots = _zigzag_pivot_indices(closes, threshold_pct)
    legs = _legs_from_pivots(closes, dates, pivots)
    if not legs:
        return []

    runs = _merge_sideways_runs(legs, threshold_pct)
    segments = [_run_to_segment(run, threshold_pct) for run in runs]
    segments = _absorb_short_segments(segments, threshold_pct)

    result = []
    for seg in segments:
        mid_idx = seg["start_idx"] + (seg["end_idx"] - seg["start_idx"]) // 2
        first_half_trend = _classify_half(closes, seg["start_idx"], mid_idx, threshold_pct)
        second_half_trend = _classify_half(closes, mid_idx, seg["end_idx"], threshold_pct)
        start_date = seg["start_date"]
        end_date = seg["end_date"]
        result.append({
            "start_date": start_date.strftime("%Y-%m-%d") if hasattr(start_date, "strftime") else str(start_date),
            "end_date": end_date.strftime("%Y-%m-%d") if hasattr(end_date, "strftime") else str(end_date),
            "days": (end_date - start_date).days,
            "return_pct": seg["return_pct"],
            "trend": seg["trend"],
            "first_half_trend": first_half_trend,
            "second_half_trend": second_half_trend,
            "pattern_label": PATTERN_LABELS[(first_half_trend, second_half_trend)],
        })
    return result
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: PASS (15 passed)

- [ ] **Step 5: 커밋**

```bash
git add engine/trend_segments.py tests/test_trend_segments.py
git commit -m "feat: 전반/후반 9패턴 라벨링 + compute_trend_segments 통합 파이프라인 추가"
```

---

### Task 6: 적응형 임계값 + 온디맨드 캐시 오케스트레이션

**Files:**
- Modify: `engine/trend_segments.py`
- Test: `tests/test_trend_segments.py`

**Interfaces:**
- Consumes: `compute_trend_segments`(Task 5), `engine.segment_analysis._compute_volatility`, `engine.cache.save_trend_segments`/`list_trend_segments`(Task 1), `upbit_data_service.get_candles`
- Produces:
  - `THRESHOLD_MULTIPLIER = 6.0`, `MIN_THRESHOLD_PCT = 5.0`, `MAX_THRESHOLD_PCT = 25.0` 상수
  - `EARLIEST_CANDLE_START` (2017-10-24 UTC 기준 datetime — 업비트 상장 시작 이전 시점 근사)
  - `_compute_threshold_pct(market: str) -> float`
  - `get_or_compute_trend_segments(market: str, force_refresh: bool = False) -> dict` — 키: `market, threshold_pct, computed_at, segments: list[dict]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_trend_segments.py` 상단 import에 `import engine.trend_segments as trend_segments_module`와
`from engine.trend_segments import _compute_threshold_pct, get_or_compute_trend_segments` 추가. 파일에 추가:

```python
def test_compute_threshold_pct_scales_with_volatility_and_clamps(monkeypatch):
    # volatility 0.01(=1%) * 100 * multiplier(6) = 6% → MIN(5)~MAX(25) 사이라 그대로.
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.01)
    assert _compute_threshold_pct("KRW-BTC") == pytest.approx(6.0)

    # volatility가 매우 커도 MAX_THRESHOLD_PCT(25)로 clamp.
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.5)
    assert _compute_threshold_pct("KRW-JUNK") == 25.0

    # volatility가 매우 작아도 MIN_THRESHOLD_PCT(5)로 clamp.
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.001)
    assert _compute_threshold_pct("KRW-STABLE") == 5.0


def test_compute_threshold_pct_falls_back_to_min_when_volatility_unavailable(monkeypatch):
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: None)
    assert _compute_threshold_pct("KRW-NEW") == 5.0


def test_get_or_compute_trend_segments_uses_cache_when_present(monkeypatch):
    cached_rows = [{
        "market": "KRW-BTC", "start_date": "2026-01-01", "end_date": "2026-02-01",
        "days": 31, "return_pct": 10.0, "trend": "up", "first_half_trend": "up",
        "second_half_trend": "up", "pattern_label": "지속형 상승",
        "threshold_pct": 8.0, "computed_at": "2026-08-16T00:00:00+00:00",
    }]
    monkeypatch.setattr(trend_segments_module, "list_trend_segments", lambda market: cached_rows)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("캐시가 있으면 get_candles를 호출하면 안 된다")
    monkeypatch.setattr(trend_segments_module, "get_candles", _fail_if_called)

    result = get_or_compute_trend_segments("KRW-BTC")

    assert result["market"] == "KRW-BTC"
    assert result["threshold_pct"] == 8.0
    assert result["computed_at"] == "2026-08-16T00:00:00+00:00"
    assert len(result["segments"]) == 1
    assert result["segments"][0]["pattern_label"] == "지속형 상승"


def test_get_or_compute_trend_segments_computes_and_saves_when_no_cache(monkeypatch):
    monkeypatch.setattr(trend_segments_module, "list_trend_segments", lambda market: [])
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({"candle_time": dates, "close": closes})
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)

    saved = {}
    monkeypatch.setattr(
        trend_segments_module, "save_trend_segments",
        lambda market, rows: saved.setdefault("rows", (market, rows)),
    )

    result = get_or_compute_trend_segments("KRW-BTC")

    assert result["market"] == "KRW-BTC"
    assert len(result["segments"]) >= 1
    assert saved["rows"][0] == "KRW-BTC"
    assert len(saved["rows"][1]) == len(result["segments"])


def test_get_or_compute_trend_segments_force_refresh_ignores_cache(monkeypatch):
    def _fail_if_called(market):
        raise AssertionError("force_refresh=True면 캐시를 조회하면 안 된다")
    monkeypatch.setattr(trend_segments_module, "list_trend_segments", _fail_if_called)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({"candle_time": dates, "close": closes})
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "save_trend_segments", lambda market, rows: None)

    result = get_or_compute_trend_segments("KRW-BTC", force_refresh=True)

    assert result["market"] == "KRW-BTC"
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 구현 추가**

`engine/trend_segments.py` 상단 import 블록을 다음으로 교체(`from __future__ import annotations` 바로 아래):

```python
from datetime import datetime, timezone

import pandas as pd

from engine.cache import list_trend_segments, save_trend_segments
from engine.segment_analysis import _compute_volatility
from upbit_data_service import get_candles
```

파일 맨 아래(`compute_trend_segments` 함수 뒤)에 추가:

```python
THRESHOLD_MULTIPLIER = 6.0
MIN_THRESHOLD_PCT = 5.0
MAX_THRESHOLD_PCT = 25.0

# 업비트 정식 서비스 시작(2017-10-24) 이전 시점 근사치. get_candles()는 이 시점부터
# 실제 상장일까지는 빈 결과를 반환하므로, 코인마다 정확한 상장일을 몰라도 안전하게
# "상장일부터 전체"를 표현할 수 있다.
EARLIEST_CANDLE_START = datetime(2017, 10, 24, tzinfo=timezone.utc)


def _compute_threshold_pct(market: str) -> float:
    volatility = _compute_volatility(market)
    if volatility is None:
        return MIN_THRESHOLD_PCT
    pct = volatility * 100 * THRESHOLD_MULTIPLIER
    return max(MIN_THRESHOLD_PCT, min(MAX_THRESHOLD_PCT, pct))


def get_or_compute_trend_segments(market: str, force_refresh: bool = False) -> dict:
    """market의 추세 구간을 캐시에서 읽거나(없으면) 새로 계산해 저장한다.
    force_refresh=True면 캐시를 무시하고 항상 새로 계산한다."""
    if not force_refresh:
        cached = list_trend_segments(market)
        if cached:
            return {
                "market": market,
                "threshold_pct": cached[0]["threshold_pct"],
                "computed_at": cached[0]["computed_at"],
                "segments": [
                    {k: v for k, v in row.items() if k not in ("market", "threshold_pct", "computed_at")}
                    for row in cached
                ],
            }

    df = get_candles(market, "days", EARLIEST_CANDLE_START, datetime.now(timezone.utc))
    threshold_pct = _compute_threshold_pct(market)
    segments = compute_trend_segments(df, threshold_pct)
    computed_at = datetime.now(timezone.utc).isoformat()

    save_trend_segments(market, [
        {**seg, "market": market, "threshold_pct": threshold_pct, "computed_at": computed_at}
        for seg in segments
    ])

    return {
        "market": market,
        "threshold_pct": threshold_pct,
        "computed_at": computed_at,
        "segments": segments,
    }
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_trend_segments.py -v`
Expected: PASS (19 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트가 깨지지 않았는지 확인**

Run: `python -m pytest tests/ -q`
Expected: 기존 실패 없이 전부 PASS (신규 19개 포함 총 테스트 수 증가)

- [ ] **Step 6: 커밋**

```bash
git add engine/trend_segments.py tests/test_trend_segments.py
git commit -m "feat: 적응형 임계값 + 온디맨드 캐시 오케스트레이션(get_or_compute_trend_segments) 추가"
```

---

### Task 7: 백엔드 API 엔드포인트

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `engine.trend_segments.get_or_compute_trend_segments`, `engine.trend_segments.EARLIEST_CANDLE_START`(Task 6), 기존 `get_candles`, `_to_utc_iso`
- Produces:
  - `GET /api/v1/analysis/trend-segments/{market}` → `{market, threshold_pct, computed_at, segments: [...], ohlcv: [...]}`
  - `POST /api/v1/analysis/trend-segments/{market}/refresh` → 동일 형식(강제 재계산)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_backend.py` 파일 끝에 추가(상단 import에 `import engine.trend_segments as trend_segments_module` 추가):

```python
def test_get_trend_segments_endpoint_returns_segments_and_ohlcv(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "candle_time": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    resp = client.get("/api/v1/analysis/trend-segments/KRW-BTC")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert len(body["segments"]) >= 1
    assert len(body["ohlcv"]) == 4
    assert body["ohlcv"][0]["close"] == 100


def test_refresh_trend_segments_endpoint_forces_recompute(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "candle_time": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    resp = client.post("/api/v1/analysis/trend-segments/KRW-BTC/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert len(body["segments"]) >= 1
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `python -m pytest tests/test_backend.py -k trend_segments -v`
Expected: FAIL with 404 (엔드포인트 없음)

- [ ] **Step 3: 엔드포인트 구현**

`backend/main.py`의 `from engine.segment_analysis import run_segment_batch` 줄 바로 아래에 추가:

```python
from engine.trend_segments import EARLIEST_CANDLE_START, get_or_compute_trend_segments
```

`get_segment_size_analysis` 함수(`@app.get("/api/v1/analysis/segments/size")`) 뒤에 추가:

```python
def _trend_segment_ohlcv(market: str) -> list[dict]:
    df = get_candles(market, "days", EARLIEST_CANDLE_START, datetime.now(timezone.utc))
    return [
        {
            "time": _to_utc_iso(row.candle_time.isoformat()),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close),
        }
        for row in df.itertuples()
    ]


@app.get("/api/v1/analysis/trend-segments/{market}")
def get_trend_segments_endpoint(market: str) -> dict:
    result = get_or_compute_trend_segments(market)
    return {**result, "ohlcv": _trend_segment_ohlcv(market)}


@app.post("/api/v1/analysis/trend-segments/{market}/refresh")
def refresh_trend_segments_endpoint(market: str) -> dict:
    result = get_or_compute_trend_segments(market, force_refresh=True)
    return {**result, "ohlcv": _trend_segment_ohlcv(market)}
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `python -m pytest tests/test_backend.py -k trend_segments -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 전체 백엔드 테스트 스위트 확인**

Run: `python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/main.py tests/test_backend.py
git commit -m "feat: 추세 구간 조회/갱신 API 엔드포인트 추가"
```

---

### Task 8: 프론트엔드 타입 + API 클라이언트

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/lib/api/eda.ts`

**Interfaces:**
- Consumes: 기존 `OhlcvPoint` 타입(`frontend/lib/types/eda.ts`)
- Produces:
  - `TrendSegment` 인터페이스, `TrendSegmentAnalysis` 인터페이스
  - `getTrendSegments(market: string): Promise<TrendSegmentAnalysis>`
  - `refreshTrendSegments(market: string): Promise<TrendSegmentAnalysis>`

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`의 `SegmentSizeEntry` 인터페이스 뒤에 추가:

```typescript
export type TrendDirection = 'up' | 'down' | 'sideways';

export interface TrendSegment {
  start_date: string;
  end_date: string;
  days: number;
  return_pct: number;
  trend: TrendDirection;
  first_half_trend: TrendDirection;
  second_half_trend: TrendDirection;
  pattern_label: string;
}

export interface TrendSegmentAnalysis {
  market: string;
  threshold_pct: number;
  computed_at: string;
  segments: TrendSegment[];
  ohlcv: OhlcvPoint[];
}
```

- [ ] **Step 2: API 함수 추가**

`frontend/lib/api/eda.ts`의 `import` 블록에 `TrendSegmentAnalysis` 추가, `getSegmentSizeAnalysis` 함수 뒤에 추가:

```typescript
export function getTrendSegments(market: string): Promise<TrendSegmentAnalysis> {
  return apiFetch<TrendSegmentAnalysis>(`/api/v1/analysis/trend-segments/${market}`);
}

export function refreshTrendSegments(market: string): Promise<TrendSegmentAnalysis> {
  return apiFetch<TrendSegmentAnalysis>(`/api/v1/analysis/trend-segments/${market}/refresh`, {
    method: 'POST',
  });
}
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (이 시점에는 아직 아무도 새 타입/함수를 참조하지 않으므로 기존 에러가 없었다면 그대로 없어야 한다)

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/types/eda.ts frontend/lib/api/eda.ts
git commit -m "feat: 추세 구간 프론트 타입 + API 클라이언트 함수 추가"
```

---

### Task 9: `TrendSegmentChart.tsx`

**Files:**
- Create: `frontend/components/TrendSegmentChart.tsx`

**Interfaces:**
- Consumes: `OhlcvPoint`, `TrendSegment`, `TrendDirection` 타입(Task 8)
- Produces: `export default function TrendSegmentChart({ ohlcv, segments }: { ohlcv: OhlcvPoint[]; segments: TrendSegment[] })`

`frontend/components/PriceChart.tsx`(색상 resolveColor 패턴, lightweight-charts 초기화/리사이즈 패턴)를 그대로 재사용한다. lightweight-charts v5의 `CandlestickData`는 바(bar) 단위 `color`/`borderColor`/`wickColor` 오버라이드를 지원하므로(`frontend/node_modules/lightweight-charts/dist/typings.d.ts:837-850` 확인됨), 구간 배경 대신 해당 구간에 속한 날짜의 캔들 자체를 구간 색상으로 칠하는 방식으로 "구간 오버레이"를 구현한다.

- [ ] **Step 1: 컴포넌트 작성**

```typescript
'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode } from 'lightweight-charts';
import type { OhlcvPoint, TrendDirection, TrendSegment } from '@/lib/types/eda';

interface TrendSegmentChartProps {
  ohlcv: OhlcvPoint[];
  segments: TrendSegment[];
}

type DayString = `${number}-${number}-${number}`;

function trendForDay(day: string, segments: TrendSegment[]): TrendDirection | null {
  for (const seg of segments) {
    if (day >= seg.start_date && day <= seg.end_date) return seg.trend;
  }
  return null;
}

export default function TrendSegmentChart({ ohlcv, segments }: TrendSegmentChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || ohlcv.length === 0) return;

    // PriceChart.tsx와 동일한 이유: getComputedStyle의 oklch() 반환값을 lightweight-charts가
    // 파싱하지 못해, canvas 2D로 한 번 그려 rgba()로 변환한다.
    const resolveColor = (varName: string): string => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return raw;
      ctx.fillStyle = raw;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      return `rgba(${pixel[0]}, ${pixel[1]}, ${pixel[2]}, ${(pixel[3] / 255).toFixed(3)})`;
    };

    const upColor = resolveColor('--price-up');
    const downColor = resolveColor('--price-down');
    const sidewaysColor = resolveColor('--marker-boundary');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const trendColor: Record<TrendDirection, string> = {
      up: upColor,
      down: downColor,
      sideways: sidewaysColor,
    };

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: false, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor, downColor, borderVisible: false, wickUpColor: upColor, wickDownColor: downColor,
    });

    const candleData = ohlcv
      .map((bar) => {
        const day = bar.time.split('T')[0];
        const trend = trendForDay(day, segments);
        const color = trend ? trendColor[trend] : undefined;
        return {
          time: day as DayString,
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
          ...(color ? { color, borderColor: color, wickColor: color } : {}),
        };
      })
      .sort((a, b) => String(a.time).localeCompare(String(b.time)))
      .filter((bar, i, arr) => i === 0 || bar.time !== arr[i - 1].time);
    candleSeries.setData(candleData);

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [ohlcv, segments]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--price-up)' }} />
          상승 구간
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--price-down)' }} />
          하락 구간
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          횡보 구간
        </span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/TrendSegmentChart.tsx
git commit -m "feat: TrendSegmentChart 컴포넌트 추가 (구간별 캔들 색상 오버레이)"
```

---

### Task 10: `TrendSegmentTable.tsx`

**Files:**
- Create: `frontend/components/TrendSegmentTable.tsx`

**Interfaces:**
- Consumes: `TrendSegment` 타입(Task 8)
- Produces: `export default function TrendSegmentTable({ segments }: { segments: TrendSegment[] })`

- [ ] **Step 1: 컴포넌트 작성**

```typescript
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { TrendSegment } from '@/lib/types/eda';

function formatShortDate(iso: string): string {
  return iso.slice(5).replace('-', '/');
}

function formatReturn(pct: number): string {
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

const TREND_TEXT_CLASS: Record<TrendSegment['trend'], string> = {
  up: 'text-[color:var(--price-up)]',
  down: 'text-[color:var(--price-down)]',
  sideways: 'text-muted-foreground',
};

export default function TrendSegmentTable({ segments }: { segments: TrendSegment[] }) {
  if (segments.length === 0) {
    return <p className="text-muted-foreground">구간 데이터가 없습니다.</p>;
  }

  return (
    <div className="max-h-96 overflow-auto rounded-md border [&>[data-slot=table-container]]:overflow-visible">
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-background">
          <TableRow>
            <TableHead>기간</TableHead>
            <TableHead className="text-right">일수</TableHead>
            <TableHead className="text-right">등락률</TableHead>
            <TableHead>패턴</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {segments.map((seg) => (
            <TableRow key={`${seg.start_date}-${seg.end_date}`}>
              <TableCell className="whitespace-nowrap">
                {formatShortDate(seg.start_date)} ~ {formatShortDate(seg.end_date)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{seg.days}일</TableCell>
              <TableCell className={`text-right tabular-nums ${TREND_TEXT_CLASS[seg.trend]}`}>
                {formatReturn(seg.return_pct)}
              </TableCell>
              <TableCell>{seg.pattern_label}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/TrendSegmentTable.tsx
git commit -m "feat: TrendSegmentTable 컴포넌트 추가"
```

---

### Task 11: `TrendSegmentView.tsx` + 세그먼트 탭 연결

**Files:**
- Create: `frontend/components/TrendSegmentView.tsx`
- Modify: `frontend/components/AnalysisSidebarView.tsx`
- Modify: `frontend/app/analysis/page.tsx`

**Interfaces:**
- Consumes: `CoinSelect`(기존), `TrendSegmentChart`/`TrendSegmentTable`(Task 9~10), `getTrendSegments`/`refreshTrendSegments`(Task 8), `Market` 타입(기존)
- Produces: `export default function TrendSegmentView({ markets }: { markets: Market[] })`; `AnalysisSidebarView`가 `markets` prop을 받아 `'trend'` 섹션을 렌더링

- [ ] **Step 1: `TrendSegmentView.tsx` 작성**

```typescript
'use client';

import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import CoinSelect from '@/components/CoinSelect';
import TrendSegmentChart from '@/components/TrendSegmentChart';
import TrendSegmentTable from '@/components/TrendSegmentTable';
import { getTrendSegments, refreshTrendSegments } from '@/lib/api/eda';
import type { Market, TrendSegmentAnalysis } from '@/lib/types/eda';

export default function TrendSegmentView({ markets }: { markets: Market[] }) {
  const [selectedMarket, setSelectedMarket] = useState(markets[0]?.market ?? '');
  const [data, setData] = useState<TrendSegmentAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedMarket) return;
    setLoading(true);
    setError(null);
    getTrendSegments(selectedMarket)
      .then(setData)
      .catch(() => setError('구간 분석을 불러오지 못했습니다.'))
      .finally(() => setLoading(false));
  }, [selectedMarket]);

  function handleRefresh() {
    if (!selectedMarket) return;
    setRefreshing(true);
    setError(null);
    refreshTrendSegments(selectedMarket)
      .then(setData)
      .catch(() => setError('갱신에 실패했습니다.'))
      .finally(() => setRefreshing(false));
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="max-w-md flex-1">
          <CoinSelect markets={markets} value={selectedMarket} onChange={setSelectedMarket} />
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={!selectedMarket || loading || refreshing}
          className="flex shrink-0 items-center gap-1.5 rounded-md border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
        >
          <RefreshCw className={`size-4 ${refreshing ? 'animate-spin' : ''}`} />
          갱신
        </button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {loading && (
        <p className="text-muted-foreground">
          계산 중입니다. 상장 기간이 긴 코인은 수 초 걸릴 수 있어요...
        </p>
      )}

      {!loading && data && (
        <>
          <p className="text-xs text-muted-foreground">
            적용 임계값: {data.threshold_pct.toFixed(1)}% · 계산 시각:{' '}
            {new Date(data.computed_at).toLocaleString('ko-KR')}
          </p>
          <TrendSegmentChart ohlcv={data.ohlcv} segments={data.segments} />
          <TrendSegmentTable segments={data.segments} />
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: `AnalysisSidebarView.tsx` 수정**

`frontend/components/AnalysisSidebarView.tsx` 전체를 다음으로 교체:

```typescript
'use client';

import { useState } from 'react';
import { BarChart3, PieChart, TrendingUp } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import SegmentSizeTable, { type SegmentRow } from '@/components/SegmentSizeTable';
import TrendSegmentView from '@/components/TrendSegmentView';
import type { Market } from '@/lib/types/eda';

type Section = 'size' | 'sector' | 'trend';

const SECTIONS: { key: Section; label: string; icon: typeof BarChart3 }[] = [
  { key: 'size', label: '세그먼트(규모)', icon: BarChart3 },
  { key: 'sector', label: '세그먼트(섹터)', icon: PieChart },
  { key: 'trend', label: '추세 기반', icon: TrendingUp },
];

export default function AnalysisSidebarView({
  segmentSizeRows,
  markets,
}: {
  segmentSizeRows: SegmentRow[];
  markets: Market[];
}) {
  const [section, setSection] = useState<Section>('size');

  return (
    <div className="flex gap-6">
      <nav className="flex w-44 shrink-0 flex-col gap-1">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setSection(key)}
            className={
              section === key
                ? 'flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm font-medium text-foreground'
                : 'flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground'
            }
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1">
        {section === 'size' ? (
          <SegmentSizeTable rows={segmentSizeRows} />
        ) : section === 'trend' ? (
          <TrendSegmentView markets={markets} />
        ) : (
          <Card>
            <CardContent className="pt-4">
              <p className="text-muted-foreground">준비 중입니다.</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `app/analysis/page.tsx` 수정**

`frontend/app/analysis/page.tsx`의 마지막 `return` 블록을 다음으로 교체(이미 `markets`를 fetch하고 있으므로 prop만 추가):

```typescript
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">세그먼트</h1>
      <AnalysisSidebarView segmentSizeRows={segmentSizeRows} markets={markets} />
    </div>
  );
```

- [ ] **Step 4: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/TrendSegmentView.tsx frontend/components/AnalysisSidebarView.tsx frontend/app/analysis/page.tsx
git commit -m "feat: 추세 기반 탭을 세그먼트 사이드바에 연결"
```

---

### Task 12: 수동 브라우저 검증

**Files:** 없음(검증만)

- [ ] **Step 1: 백엔드/프론트 dev 서버 기동**

Run (저장소 루트): `uvicorn backend.main:app --reload --port 8000`
Run (별도 터미널, `frontend/` 디렉터리): `npm run dev`

- [ ] **Step 2: 브라우저로 `/analysis` → `세그먼트` → `추세 기반` 확인**

- 좌측 사이드바에 `세그먼트(규모)` / `세그먼트(섹터)` 와 나란히 `추세 기반` 항목이 보이는지 확인.
- `추세 기반` 클릭 → 코인 선택 드롭다운에서 변동성이 다른 코인 2~3개(예: 비트코인, 잡코인 하나) 선택.
- 코인 선택 시 "계산 중입니다..." 로딩 문구가 잠깐 보이고, 이후 캔들 차트와 구간 표가 렌더링되는지 확인.
- 차트의 캔들 색상이 구간(상승=주황/빨강 계열, 하락=파랑 계열, 횡보=회색)별로 바뀌는지 육안 확인.
- 표의 기간이 겹치지 않고 시간순으로 이어지는지, `패턴` 열에 9라벨 중 하나가 표시되는지 확인.
- "갱신" 버튼 클릭 → 다시 로딩 후 데이터가 갱신되는지(계산 시각이 바뀌는지) 확인.
- 같은 코인을 다시 선택(캐시 히트) 시 로딩 없이 바로 표시되는지 확인.

- [ ] **Step 3: 콘솔 에러 확인**

브라우저 개발자 도구 콘솔에 에러가 없는지 확인(특히 `resolveColor`의 `oklch()` 파싱 관련 에러가 없는지 — `PriceChart.tsx`와 동일한 방식이므로 발생하지 않아야 정상).

- [ ] **Step 4: 최종 전체 테스트 스위트 재확인**

Run: `python -m pytest tests/ -q`
Expected: 전부 PASS
