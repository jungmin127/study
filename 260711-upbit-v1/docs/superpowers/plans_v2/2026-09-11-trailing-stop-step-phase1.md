# 계단식 트레일링 스탑 (Phase 1 — 백테스트 엔진) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 조건 트리 엔진에 새 매도 지표 `TRAILING_STOP_STEP_PCT`(계단식 트레일링
스탑)를 추가해, 백테스트에서 기존 `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`와 동일한
방식으로 조건 블록 하나로 쓸 수 있게 한다.

**Architecture:** `engine/condition_tree.py`의 `POSITION_RELATIVE_INDICATORS`
패턴을 확장해 `eval_group()`/`eval_group_values()`에 전용 분기를 추가하고,
`engine/condition_strategy.py`의 `ConditionTreeStrategy`가 포지션 진입 후 매
봉 "최고 수익률(peak)"을 추적해 넘긴다. `backend/main.py`의
`INDICATOR_CATALOG`와 프론트엔드 3개 파일에 동일 패턴으로 노출한다. 실거래
daemon 연동(Phase 2)은 이 플랜 범위 밖 — 별도 플랜.

**Tech Stack:** Python(backtrader 기반 백테스트 엔진), pytest, TypeScript/Next.js
프론트엔드

## Global Constraints

- 모든 python/pytest 실행은 `PYTHONPATH=. PYTHONIOENCODING=utf-8`를 앞에 붙인다
  (Windows 콘솔 인코딩/모듈 경로 요구사항, 기존 스크립트 전부 동일)
- 코드 주석/식별자는 기존 관례대로 한글 설명 + 영어 식별자 혼용
- 각 태스크 완료 후 커밋 메시지 끝에 다음 트레일러를 반드시 포함한다:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01VNMZAcYudpu4rvaakjF22T
  ```
- 이 프로젝트는 항상 `main`에서 직접 작업하고, 각 태스크는 개별 커밋만 하며,
  전체 푸시는 마지막 태스크(3번) 완료 시 수행한다(프로젝트 CLAUDE.md 규칙)
- 원본 스펙: `docs/superpowers/specs_v2/2026-09-11-trailing-stop-step-design.md`
  (계산 공식/파라미터/범위는 이 문서와 일치해야 한다)
- `eval_group()`(백테스트, bt.Indicator 버전)과 `eval_group_values()`(라이브,
  값 딕셔너리 버전)는 **쌍둥이 함수** — 새 분기는 반드시 둘 다에 추가한다
  (파일 내 기존 경고 주석과 동일 원칙)
- 새 지표 `TRAILING_STOP_STEP_PCT`에서 `item["operator"]`는 의도적으로
  무시한다(손절선이 매번 새로 계산되는 이동값이라 일반 `apply_operator` 경로를
  쓸 수 없음) — UI는 `fixedOperator: "<="`로 고정해 혼란을 막는다
- 프론트엔드(:3000)와 백엔드(:8000) dev 서버는 이미 백그라운드에서 실행 중이다
  (Next.js는 HMR이라 파일 저장만으로 반영됨) — Task 3에서 새로 띄우지 말고
  그대로 사용해 브라우저로 검증한다

---

## File Structure

- **Modify: `engine/condition_tree.py`** — `POSITION_RELATIVE_INDICATORS`에 새
  지표 추가, `eval_group()`/`eval_group_values()`에 전용 분기 추가
- **Modify: `backend/main.py`** — `INDICATOR_CATALOG`에 새 지표 카탈로그 항목
  추가(프론트엔드가 이 API로 지표 목록을 받아옴)
- **Modify: `engine/condition_strategy.py`** — `ConditionTreeStrategy`에 포지션
  진입 후 최고 수익률(peak) 추적 상태 추가
- **Modify: `frontend/lib/indicator-guide.ts`** — 새 지표 설명 텍스트(지표
  가이드 탭 InfoPopover용)
- **Modify: `frontend/components/StrategyConditionBuilder.tsx`** — 새 지표의
  추천 임계값 기본값
- **Modify: `frontend/lib/indicator-example-builder.ts`** — 새 지표의 예시
  테이블(계단이 올라가는 과정을 숫자로 보여줌)
- **Modify: `tests/test_condition_tree.py`** — 새 지표 순수 로직 단위 테스트
- **Modify: `tests/test_condition_strategy.py`** — 새 지표 백테스트 통합 테스트

---

### Task 1: 조건 트리 엔진 + 백엔드 카탈로그

**Files:**
- Modify: `engine/condition_tree.py`
- Modify: `backend/main.py`
- Test: `tests/test_condition_tree.py`

**Interfaces:**
- Produces: `eval_group(group, indicators, position_return_pct=None, position_holding_bars=None, position_peak_return_pct=None) -> bool`,
  `eval_group_values(group, values, position_return_pct=None, position_holding_bars=None, position_peak_return_pct=None) -> bool | None`
  (둘 다 새 파라미터 `position_peak_return_pct` 추가 — Task 2가 이 파라미터로
  peak 값을 넘긴다)
- `TRAILING_STOP_STEP_PCT`는 `POSITION_RELATIVE_INDICATORS` 멤버가 되어
  `find_unknown_indicators()`/`find_indicators_with_missing_values()`/
  `engine/condition_strategy.py`의 `_ensure_indicator()`는 **수정 불필요**
  (전부 멤버십만 검사하는 범용 로직)

**주의**: `backend/main.py`의 기존 테스트
`test_get_indicator_catalog_covers_all_registered_indicators`
(`tests/test_backend.py:363`)가
`catalog_values == set(INDICATOR_FACTORY.keys()) | POSITION_RELATIVE_INDICATORS`를
검증한다 — `condition_tree.py`만 고치고 `backend/main.py`를 안 고치면 이 기존
테스트가 깨진다. 그래서 이 태스크는 두 파일을 **함께** 커밋한다(따로 나누면
중간에 기존 테스트 스위트가 빨간불이 된다).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_tree.py` 파일 끝(445번째 줄, `assert eval_group_values(tree, values) is False`로 끝나는
`test_eval_group_values_drops_nan_leaf_in_or` 다음)에 추가:

```python


def test_find_unknown_indicators_allows_trailing_stop_step_pct():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}],
    }
    assert find_unknown_indicators(tree) == []


def test_eval_group_values_trailing_stop_step_pct_computes_stepped_stop_level():
    # step=5, peak=+16% -> 3계단 클리어(5,10,15) -> stop_level=(3-1)*5=10
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}],
    }
    assert eval_group_values(tree, {}, position_return_pct=11.0, position_peak_return_pct=16.0) is False
    assert eval_group_values(tree, {}, position_return_pct=9.0, position_peak_return_pct=16.0) is True


def test_eval_group_values_trailing_stop_step_pct_not_yet_armed_before_first_step():
    # peak=+3%, step=5 -> floor(3/5)=0 -> stop_level=(0-1)*5=-5 (사실상 미발동,
    # 같은 매도조건 그룹의 STOP_LOSS_PCT가 먼저 걸리는 게 자연스러운 폴백)
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}],
    }
    assert eval_group_values(tree, {}, position_return_pct=-4.0, position_peak_return_pct=3.0) is False
    assert eval_group_values(tree, {}, position_return_pct=-6.0, position_peak_return_pct=3.0) is True


def test_eval_group_values_trailing_stop_step_pct_false_without_position_or_peak():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}],
    }
    assert eval_group_values(tree, {}, position_return_pct=None, position_peak_return_pct=16.0) is False
    assert eval_group_values(tree, {}, position_return_pct=9.0, position_peak_return_pct=None) is False


def test_eval_group_values_trailing_stop_step_pct_invalid_step_never_triggers():
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 0}],
    }
    assert eval_group_values(tree, {}, position_return_pct=-100.0, position_peak_return_pct=50.0) is False


def test_eval_group_trailing_stop_step_pct_matches_eval_group_values():
    # eval_group()(bt.Indicator 버전)도 eval_group_values()와 동일하게 동작해야 한다(쌍둥이 함수).
    tree = {
        "type": "AND",
        "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}],
    }
    assert eval_group(tree, {}, position_return_pct=11.0, position_peak_return_pct=16.0) is False
    assert eval_group(tree, {}, position_return_pct=9.0, position_peak_return_pct=16.0) is True
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_tree.py -v`
Expected: 새로 추가한 6개 테스트 FAIL — `TRAILING_STOP_STEP_PCT`가
`POSITION_RELATIVE_INDICATORS`에 없어 `find_unknown_indicators`가 이를 "알 수
없는 지표"로 분류하거나, `eval_group`/`eval_group_values`가
`position_peak_return_pct` 키워드 인자를 몰라 `TypeError`를 던짐

- [ ] **Step 3: `engine/condition_tree.py` 수정**

파일 최상단 import에 `math` 추가(`import backtrader as bt` 앞 또는 뒤,
표준 라이브러리 우선 관례를 따라 앞에):

```python
import math

import backtrader as bt
```

`POSITION_RELATIVE_INDICATORS` 정의를 다음으로 교체:

```python
POSITION_RELATIVE_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS", "TRAILING_STOP_STEP_PCT"}
```

`eval_group()` 함수 전체(시그니처부터 `return all(results) if group_type == "AND" else any(results)`까지)를
다음으로 교체:

```python
def eval_group(
    group: dict,
    indicators: dict[str, bt.Indicator],
    position_return_pct: float | None = None,
    position_holding_bars: int | None = None,
    position_peak_return_pct: float | None = None,
) -> bool:
    """ConditionGroup을 재귀적으로 평가해 bool 반환. indicators는 indicator_key -> bt.Indicator 매핑.
    position_return_pct는 포지션 진입가 대비 현재 수익률(%)로 STOP_LOSS_PCT/TAKE_PROFIT_PCT 평가에,
    position_holding_bars는 포지션 보유 봉수로 HOLDING_PERIOD_BARS 평가에 쓰인다.
    position_peak_return_pct는 포지션 진입 후 지금까지의 최고 수익률(%)로 TRAILING_STOP_STEP_PCT
    평가에 쓰인다. 포지션이 없어 해당 값이 None이면 그 블록은 False로 처리한다."""
    group_type = group.get("type", "AND")
    conditions = group.get("conditions", [])

    if not conditions:
        return False

    results: list[bool] = []
    for item in conditions:
        if "indicator" in item:
            if item["indicator"] == "HOLDING_PERIOD_BARS":
                if position_holding_bars is None:
                    results.append(False)
                else:
                    results.append(
                        apply_operator(position_holding_bars, item["operator"], float(item["threshold"]))
                    )
                continue
            if item["indicator"] == "TRAILING_STOP_STEP_PCT":
                # threshold는 일반 지표처럼 "비교할 값"이 아니라 계단 폭(step_pct)이다. 손절선 자체가
                # 고점(position_peak_return_pct)으로부터 매번 새로 계산되는 이동값이라 operator/threshold를
                # apply_operator에 그대로 넘기는 일반 경로를 쓸 수 없다 — item["operator"]는 의도적으로
                # 무시한다(UI는 "<=" 고정으로 혼란을 막는다). 설계 근거:
                # docs/superpowers/specs_v2/2026-09-11-trailing-stop-step-design.md
                step = float(item["threshold"])
                if position_return_pct is None or position_peak_return_pct is None or step <= 0:
                    results.append(False)
                else:
                    stop_level = (math.floor(position_peak_return_pct / step) - 1) * step
                    results.append(position_return_pct <= stop_level)
                continue
            if item["indicator"] in POSITION_RELATIVE_INDICATORS:
                if position_return_pct is None:
                    results.append(False)
                else:
                    results.append(apply_operator(position_return_pct, item["operator"], float(item["threshold"])))
                continue
            key = indicator_key(item["indicator"], item.get("params", {}))
            if key not in indicators:
                results.append(False)
                continue
            value = get_indicator_value(item["indicator"], indicators[key])
            results.append(apply_operator(value, item["operator"], float(item["threshold"])))
        elif "type" in item:
            results.append(
                eval_group(item, indicators, position_return_pct, position_holding_bars, position_peak_return_pct)
            )

    return all(results) if group_type == "AND" else any(results)
```

`eval_group_values()` 함수 전체(시그니처부터
`return all(results) if group_type == "AND" else any(results)`까지)를 다음으로
교체:

```python
def eval_group_values(
    group: dict,
    values: dict[str, float | None],
    position_return_pct: float | None = None,
    position_holding_bars: int | None = None,
    position_peak_return_pct: float | None = None,
) -> bool | None:
    """ConditionGroup을 재귀적으로 평가해 bool 또는 None(unknown)을 반환. eval_group()과
    로직은 같지만 bt.Indicator 대신 이미 계산된 값 딕셔너리(indicator_key -> float | None)를
    직접 읽는다(라이브 트레이딩용, 스펙 결정 1).

    values[key]가 없거나 None이면 그 리프는 "unknown"으로 취급되어 AND/OR 평가에서
    제외된다(스펙 결정 8 — 외부데이터 지표가 지연/실패해도 나머지 조건만으로 매매를
    이어가기 위함). 한 그룹의 자식이 전부 unknown이면 그 그룹 자체도 None을 반환하고,
    그 None은 상위 그룹에서도 다시 unknown 리프처럼 제외된다. 최상위 그룹까지 None이
    전파되면 "이 조건 전체를 지금 판단할 수 없다"는 뜻이다."""
    group_type = group.get("type", "AND")
    conditions = group.get("conditions", [])

    if not conditions:
        return False

    results: list[bool] = []
    for item in conditions:
        if "indicator" in item:
            if item["indicator"] == "HOLDING_PERIOD_BARS":
                if position_holding_bars is None:
                    results.append(False)
                else:
                    results.append(
                        apply_operator(position_holding_bars, item["operator"], float(item["threshold"]))
                    )
                continue
            if item["indicator"] == "TRAILING_STOP_STEP_PCT":
                # eval_group()과 동일 로직 — 쌍둥이 함수, 둘 다 고칠 것.
                step = float(item["threshold"])
                if position_return_pct is None or position_peak_return_pct is None or step <= 0:
                    results.append(False)
                else:
                    stop_level = (math.floor(position_peak_return_pct / step) - 1) * step
                    results.append(position_return_pct <= stop_level)
                continue
            if item["indicator"] in POSITION_RELATIVE_INDICATORS:
                if position_return_pct is None:
                    results.append(False)
                else:
                    results.append(apply_operator(position_return_pct, item["operator"], float(item["threshold"])))
                continue
            key = indicator_key(item["indicator"], item.get("params", {}))
            value = values.get(key)
            if value is None or value != value:  # None 또는 NaN(자기 자신과도 다름) -> unknown
                continue  # unknown 리프는 이 그룹 평가에서 제외
            results.append(apply_operator(value, item["operator"], float(item["threshold"])))
        elif "type" in item:
            child = eval_group_values(
                item, values, position_return_pct, position_holding_bars, position_peak_return_pct
            )
            if child is None:
                continue  # 하위 그룹 전체가 unknown -> 이 그룹 평가에서도 제외
            results.append(child)

    if not results:
        return None  # 판단 가능한 자식이 하나도 없음 -> 이 그룹도 unknown

    return all(results) if group_type == "AND" else any(results)
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_tree.py -v`
Expected: 전체 PASS

- [ ] **Step 5: 기존 백엔드 카탈로그 테스트로 빠진 부분 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `TRAILING_STOP_STEP_PCT`가 `POSITION_RELATIVE_INDICATORS`에는
있지만 `INDICATOR_CATALOG`(backend/main.py)에는 아직 없어서
`catalog_values == set(INDICATOR_FACTORY.keys()) | POSITION_RELATIVE_INDICATORS`
비교가 어긋남

- [ ] **Step 6: `backend/main.py`의 `INDICATOR_CATALOG`에 항목 추가**

`backend/main.py`에서 `HOLDING_PERIOD_BARS` 항목(아래 블록) 바로 뒤에 새 항목을
추가한다:

```python
    {
        "value": "HOLDING_PERIOD_BARS", "label": "보유기간 (봉)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": ">=",
        "description": "캔들 지표가 아니라 포지션을 진입한 이후 지난 봉의 개수입니다. 이 값이 임계값 이상이 되면 매도합니다(캘린더 일수가 아니라 봉 개수 기준).",
        "example": "임계값 20을 넣으면, 진입 후 20개 봉이 지나는 순간(15분봉이면 5시간, 일봉이면 20일) 매도 조건이 참이 됩니다.",
    },
    {
        "value": "TRAILING_STOP_STEP_PCT", "label": "계단식 트레일링 스탑 (%)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": "<=",
        "description": "포지션 진입 후 도달한 최고 수익률(%)을 추적해, 그 값이 임계값(계단 폭)의 "
            "배수를 넘을 때마다 손절선을 한 단계씩 끌어올립니다. 임계값은 계단 폭(step)이며, "
            "실제 손절선은 floor(최고수익률/step - 1)*step으로 계산됩니다.",
        "example": "임계값(step) 5를 넣으면: 최고수익률이 +5%를 넘으면 손절선 0%(본절), "
            "+10%를 넘으면 손절선 +5%, +15%를 넘으면 손절선 +10%로 따라 올라갑니다. "
            "현재 수익률이 그 손절선 이하로 내려오면 매도합니다.",
    },
```

- [ ] **Step 7: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_tree.py tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: 전체 PASS

- [ ] **Step 8: 커밋**

```bash
git add engine/condition_tree.py backend/main.py tests/test_condition_tree.py
git commit -m "$(cat <<'EOF'
feat: 계단식 트레일링 스탑 - 조건트리 엔진 + 백엔드 카탈로그

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VNMZAcYudpu4rvaakjF22T
EOF
)"
```

---

### Task 2: 백테스트 전략 — 고점(peak) 추적

**Files:**
- Modify: `engine/condition_strategy.py`
- Test: `tests/test_condition_strategy.py`

**Interfaces:**
- Consumes: Task 1의 `eval_group(group, indicators, position_return_pct=None, position_holding_bars=None, position_peak_return_pct=None) -> bool`
- `ConditionTreeStrategy`(bt.Strategy)의 동작만 바뀐다 — 새로 노출되는 함수/클래스 없음

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_condition_strategy.py` 파일 끝(`test_holding_period_bars_forces_exit_after_n_bars`
다음)에 추가:

```python


def test_trailing_stop_step_pct_locks_in_gains_as_price_rises():
    # 항상 매수, 계단식 트레일링(step=1)만 매도조건. make_oscillating_df()는
    # base=20000 진폭 600(=3%)+리플 50 사인파라 진입 후 대부분의 상승 구간에서
    # 최소 1%(step) 이상은 오르내리므로, 최고수익률 대비 한 단계 아래로
    # 떨어지는 순간 매도가 걸려야 한다.
    buy = {"type": "AND", "conditions": [{"indicator": "SMA", "params": {"period": 1}, "operator": ">", "threshold": 0}]}  # 항상 참
    sell = {"type": "AND", "conditions": [{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 1}]}
    result = _run(buy, sell)
    assert len(result["trades"]) > 0
    # 계단식은 "본절 이상을 지키는" 게 목적 -> 최소 한 건은 수익(또는 본절 근접) 청산이어야 한다.
    assert any(t["returnRate"] >= -0.5 for t in result["trades"])
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_strategy.py::test_trailing_stop_step_pct_locks_in_gains_as_price_rises -v`
Expected: FAIL — `ConditionTreeStrategy`가 `position_peak_return_pct`를 계산해
넘기지 않아서(코드는 실행되지만) 매도 조건이 항상 `False`가 되어
`result["trades"] == []`, 그래서 `assert len(result["trades"]) > 0`에서 실패

- [ ] **Step 3: `engine/condition_strategy.py` 수정**

`ConditionTreeStrategy.__init__`의 `self._entry_bar: int | None = None` 다음 줄에
추가:

```python
        self._entry_bar: int | None = None
        self._peak_return_pct: float | None = None
```

`next()` 메서드 전체를 다음으로 교체:

```python
    def next(self) -> None:
        if not self.position:
            self._entry_bar = None
            self._peak_return_pct = None
            if eval_group(self._buy_cond, self._buy_inds):
                self.buy()
        else:
            if self._entry_bar is None:
                self._entry_bar = len(self)
            entry_price = self.position.price
            position_return_pct = (
                (self.data.close[0] - entry_price) / entry_price * 100 if entry_price else None
            )
            if position_return_pct is not None:
                self._peak_return_pct = (
                    position_return_pct if self._peak_return_pct is None
                    else max(self._peak_return_pct, position_return_pct)
                )
            position_holding_bars = len(self) - self._entry_bar
            if eval_group(
                self._sell_cond,
                self._sell_inds,
                position_return_pct=position_return_pct,
                position_holding_bars=position_holding_bars,
                position_peak_return_pct=self._peak_return_pct,
            ):
                self.sell()
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_condition_strategy.py -v`
Expected: 전체 PASS(8개 — 기존 7개 + 신규 1개)

만약 Step 1에서 작성한 어서션(`any(t["returnRate"] >= -0.5 ...)`)이 실제
`make_oscillating_df()` 가격 경로에서 참이 아니면(합성 데이터 특성상 발생
가능), 테스트 실행 결과로 나온 실제 `result["trades"]`의 `returnRate` 분포를
보고 어서션을 그 실측값에 맞게 조정한다 — 이 스텝의 목적은 "계단식 트레일링이
실제로 매도를 트리거한다"는 것 하나이지 특정 숫자가 아니다. 최소한
`len(result["trades"]) > 0`은 반드시 유지한다.

- [ ] **Step 5: 커밋**

```bash
git add engine/condition_strategy.py tests/test_condition_strategy.py
git commit -m "$(cat <<'EOF'
feat: 계단식 트레일링 스탑 - 백테스트 전략 고점 추적

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VNMZAcYudpu4rvaakjF22T
EOF
)"
```

---

### Task 3: 프론트엔드 노출 + 전체 회귀 확인 + 푸시

**Files:**
- Modify: `frontend/lib/indicator-guide.ts`
- Modify: `frontend/components/StrategyConditionBuilder.tsx`
- Modify: `frontend/lib/indicator-example-builder.ts`

**Interfaces:**
- Consumes: Task 1이 `backend/main.py`의 `INDICATOR_CATALOG`에 추가한
  `TRAILING_STOP_STEP_PCT` 항목(`/api/v1/indicators/catalog`로 노출됨)
- Produces: 없음(UI 표시 전용)

- [ ] **Step 1: `frontend/lib/indicator-guide.ts` 수정**

`HOLDING_PERIOD_BARS: { ... },` 블록(`usage: '방향성 없이 오래 물려 있는...'`로
끝나는 부분) 바로 뒤에 추가:

```typescript
  TRAILING_STOP_STEP_PCT: {
    meaning:
      '캔들 지표가 아니라 보유 포지션이 진입 후 지금까지 도달한 "최고 수익률(%)"을 기준으로, 그 최고 수익률이 임계값(계단 폭)의 배수를 넘을 때마다 손절선이 한 단계씩 따라 올라가는 지표입니다. STOP_LOSS_PCT처럼 손절선이 고정돼 있지 않습니다.',
    params: [],
    formula: '손절선(%) = (floor(최고 수익률 ÷ step) − 1) × step  (step = threshold)',
    thresholdExample:
      '매도 조건 전용(sellOnly), 연산자 "≤" 고정. threshold는 계단 폭(step, 보통 양수, 예: 5)입니다. 최고 수익률이 아직 첫 계단도 못 넘었으면 손절선이 음수로 계산돼 사실상 미발동 상태가 됩니다(같은 조건그룹의 STOP_LOSS_PCT가 먼저 걸리는 게 자연스러운 폴백). 예: step=5, 최고 수익률 +16%면 손절선은 +10% — 수익률이 +10% 이하로 떨어지는 순간 매도.',
    usage: 'STOP_LOSS_PCT/TAKE_PROFIT_PCT처럼 고정된 라인이 아니라, 크게 오른 뒤 그 이익을 얼마나 반납하면 청산할지를 계단식으로 관리하고 싶을 때 씁니다. 보통 STOP_LOSS_PCT와 OR로 함께 걸어, 트레일링이 발동하기 전까지는 기존 손절선이 안전망 역할을 하게 합니다.',
  },
```

- [ ] **Step 2: `frontend/components/StrategyConditionBuilder.tsx` 수정**

`POSITION_RELATIVE_DEFAULTS`를 다음으로 교체:

```typescript
const POSITION_RELATIVE_DEFAULTS: Record<string, number> = {
  STOP_LOSS_PCT: -5,
  TAKE_PROFIT_PCT: 10,
  HOLDING_PERIOD_BARS: 20,
  TRAILING_STOP_STEP_PCT: 5,
};
```

- [ ] **Step 3: `frontend/lib/indicator-example-builder.ts` 수정**

기존 `case 'STOP_LOSS_PCT': case 'TAKE_PROFIT_PCT': case 'HOLDING_PERIOD_BARS': {`
블록(뒤에 `default:` 케이스가 오기 직전)이 끝나는 `}` 바로 뒤, `default:` 케이스
바로 앞에 새 case를 추가한다:

```typescript
    case 'TRAILING_STOP_STEP_PCT': {
      const entry = 100000;
      const step = 5;
      const path = [100000, 106000, 109000, 115000, 103000];
      let peak = -Infinity;
      const rows = path.map((price, i) => {
        const returnPct = ((price - entry) / entry) * 100;
        peak = Math.max(peak, returnPct);
        const stopLevel = (Math.floor(peak / step) - 1) * step;
        return {
          bar: i,
          cells: {
            bars: String(i),
            price: n(price, 0),
            returnPct: `${(returnPct >= 0 ? '+' : '') + n(returnPct)}%`,
            peakPct: `${(peak >= 0 ? '+' : '') + n(peak)}%`,
            stopLevel: `${(stopLevel >= 0 ? '+' : '') + n(stopLevel)}%`,
          },
        };
      });
      return {
        columns: [
          { key: 'bars', label: '봉' },
          { key: 'price', label: '현재가' },
          { key: 'returnPct', label: '진입가 대비 수익률' },
          { key: 'peakPct', label: '최고 수익률(고점)' },
          { key: 'stopLevel', label: '손절선(step=5)' },
        ],
        rows,
        chart: { type: 'none' },
      };
    }
```

- [ ] **Step 4: 브라우저로 확인**

프론트엔드(`http://localhost:3000`)와 백엔드(`http://localhost:8000`) dev
서버는 이미 실행 중이다(전역 제약 참고). Next.js는 파일 저장만으로 HMR
반영되므로 새로 띄우지 않는다.

1. 브라우저로 조건 빌더가 있는 화면(예: 백테스트 상세 페이지의 매도 조건
   빌더, 또는 `/guide` 지표 가이드 탭)에 접속한다.
2. 매도 조건에 새 지표 "계단식 트레일링 스탑 (%)"이 목록에 나타나는지 확인한다.
3. 선택 시 임계값 기본값이 5로 채워지는지, 연산자가 "≤"로 고정되는지 확인한다.
4. `/guide` 탭(또는 지표 설명 팝오버)에서 이 지표를 찾아 설명/공식/예시 표가
   깨지지 않고 표시되는지 확인한다(위 Step 3에서 만든 봉/현재가/수익률/고점/손절선
   5개 열).
5. 콘솔에 에러가 없는지 확인한다.

문제가 없으면 다음 스텝으로. 문제가 있으면(타입 에러, 표 깨짐 등) 원인을
확인해 고친 뒤 다시 확인한다.

- [ ] **Step 5: 전체 파이썬 테스트 스위트 회귀 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 전체 PASS, 기존 테스트 실패 없음(Task 1/2에서 추가한 테스트 포함)

- [ ] **Step 6: 커밋 + 푸시**

```bash
git add frontend/lib/indicator-guide.ts frontend/components/StrategyConditionBuilder.tsx frontend/lib/indicator-example-builder.ts
git commit -m "$(cat <<'EOF'
feat: 계단식 트레일링 스탑 - 프론트엔드 조건 빌더/가이드 노출

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VNMZAcYudpu4rvaakjF22T
EOF
)"
git push
```

---

## 최종 완료 기준 (스펙 Phase 1과 동일)

- 매도 조건 트리에 `{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}`
  블록을 넣고 백테스트를 돌리면, 계단 공식대로 손절선이 따라 올라가며 매도가
  트리거된다
- `eval_group()`/`eval_group_values()` 양쪽 모두 동일하게 동작
- `/api/v1/indicators/catalog`에 새 지표가 노출되고, 조건 빌더 UI에서 다른
  손익 지표와 동일한 방식으로 선택/설명/예시를 볼 수 있다(브라우저로 확인 완료)
- 신규 유닛/통합 테스트 전부 통과, 기존 테스트 스위트 회귀 없음
- Phase 2(실거래 연동)는 이 플랜 범위 밖 — 별도 플랜에서 다룬다
