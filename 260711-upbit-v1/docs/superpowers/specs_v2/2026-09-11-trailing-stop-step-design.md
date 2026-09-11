# 계단식 트레일링 스탑 — 설계 스펙

## 배경

지금 조건 트리 엔진(`engine/condition_tree.py`)은 포지션 관련 매도 조건으로
`STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`/`HOLDING_PERIOD_BARS` 세 가지를 제공한다.
셋 다 "진입가 대비 현재 수익률(%)"이라는 고정값 하나만 보고 판단하는 정적
지표라, 수익이 커져도 손절선은 처음 설정한 그대로 유지된다 — 큰 폭으로 올랐다가
그대로 반납하고 손절되는 상황을 막을 수 없다.

이 스펙은 "수익이 일정 폭(step) 오를 때마다 손절선을 한 단계씩 따라 올리는"
계단식 트레일링 스탑을 새 조건 트리 지표로 추가한다. 방식 선택과 범위는
직전 세션(2026-09-11)에서 사용자와 브레인스토밍으로 확정했다:

- 트레일링 방식은 3가지(본절 잠금/계단식/연속 추적) 중 **계단식**으로 확정
- 파라미터는 진입 임계치와 간격을 분리하지 않고 **`step_pct` 하나**로 단순화
  (+step마다 손절선이 한 단계 전 수준으로 이동)
- 고점(추적 기준값)은 **백테스트는 종가, 실거래는 tick 체결가** 기준(기존
  `position_return_pct` 계산 방식과 일관)
- 범위는 **백테스트 엔진(Phase 1)** + **실거래 daemon 연동(Phase 2)** 둘 다
  포함하되, 실거래 연동은 `trading/daemon.py`의 `_run_risk_exit_loop`(7라운드
  리뷰를 거친 동시성 critical 코드)를 건드리는 무게가 있어 **별도 구현 플랜으로
  분리**한다. 이 스펙 문서는 두 Phase를 함께 설계하지만, 뒤이어 작성하는
  구현 플랜은 Phase 1만 다룬다.

**참고**: 이 기능은 [[2026-09-06-regime-strategy-auto-pipeline-design]] 스펙의
"비범위" 섹션에서 "트레일링 스탑은 별도 세션에서 브레인스토밍"이라고 이미
예고돼 있었다.

## 목표

1. 매도 조건 트리에 새 지표 `TRAILING_STOP_STEP_PCT`를 추가해, `STOP_LOSS_PCT`와
   똑같이 조건 블록 하나로 OR 그룹에 넣어 쓸 수 있게 한다.
2. 백테스트(`engine/condition_strategy.py`)에서 포지션 진입 후 매 봉마다 "지금까지
   도달한 최고 수익률(peak)"을 추적하고, 이를 기준으로 계단식 손절선을 계산해
   매도 여부를 판단한다.
3. 전략 빌더 UI(`/strategy-library`, 백테스트 상세 등에서 쓰는 조건 빌더)에서
   다른 손익 지표와 동일한 방식으로 선택/설명/예시를 볼 수 있게 한다.
4. (Phase 2, 별도 플랜) 실거래 daemon의 실시간 리스크 청산 루프에서도 동일한
   계단식 로직이 동작하도록, 포지션의 고점 상태를 `trading.db`에 영속화한다.

## 비범위

- **본절 잠금 / 연속 추적 방식** — 이번엔 계단식만. 다른 방식이 필요해지면
  별도 지표(예: `TRAILING_STOP_PCT`)로 추가하는 후속 작업.
- **grid search / `scripts/regime_strategy_pipeline.py` 자동 연동** — 이 스펙은
  엔진에 새 지표를 추가하는 것까지다. 자동 발굴 파이프라인의
  `augment_with_tp_sl()`에 트레일링 스탑을 자동으로 얹는 건 사용자가 명시적으로
  요청한 "그걸로 자동 백테스팅을 구현" 단계에서 별도로 다룬다.
- **Phase 2 구현 자체** — 이 문서는 Phase 2 설계까지 포함하지만, 실제 구현
  플랜/태스크는 Phase 1이 끝나고 검증된 뒤 별도 세션에서 작성한다.
- **`봉 고가(High)` 기준 트레일링** — 고점 기준은 종가/tick가로 고정(브레인스토밍
  결정). 고가 기준이 필요해지면 별도 지표로.

## 설계 (Phase 1 — 백테스트 엔진)

### 1. 계산 공식

```
peak_return_pct = 포지션 진입 후 지금까지의 최고 수익률(%)   # 종가 기준
stop_level_pct  = (floor(peak_return_pct / step_pct) - 1) * step_pct
매도 조건        = position_return_pct <= stop_level_pct
```

`step_pct`는 조건 블록의 `threshold` 필드에 담는다(다른 손익 지표와 동일하게
grid search 등에서 숫자 하나로 다룰 수 있도록). `peak_return_pct`가 아직
`step_pct` 한 단계도 못 넘은 경우 `stop_level_pct`가 음수로 계산되어 사실상
미발동 상태가 된다 — 같은 매도 조건 그룹에 보통 함께 있는 `STOP_LOSS_PCT`가
먼저 걸리는 게 자연스러운 폴백이라 별도 처리를 두지 않는다.

예시(`step_pct=5`, 진입가 100):

| peak_return_pct | stop_level_pct |
|---|---|
| +3% (아직 5% 미달) | -5% (사실상 미발동) |
| +6% | 0% (본절) |
| +11% | +5% |
| +16% | +10% |

`step_pct <= 0`(잘못된 설정)이면 항상 매도하지 않음(`False`)으로 처리한다.

### 2. `engine/condition_tree.py`

- `POSITION_RELATIVE_INDICATORS`에 `"TRAILING_STOP_STEP_PCT"` 추가. 이 집합을
  이미 참조하는 `find_unknown_indicators()`/`find_indicators_with_missing_values()`/
  `condition_strategy.py`의 `_ensure_indicator()`는 **수정 불필요** — 전부
  `POSITION_RELATIVE_INDICATORS` 멤버십만 검사하는 범용 로직이라 자동으로
  새 지표를 인식한다.
- `eval_group()`/`eval_group_values()`(쌍둥이 함수, 파일 내 기존 경고 주석대로
  **둘 다** 수정) 시그니처에 `position_peak_return_pct: float | None = None`을
  추가하고, `HOLDING_PERIOD_BARS`와 같은 자리에 전용 분기를 추가한다:

  ```python
  if item["indicator"] == "TRAILING_STOP_STEP_PCT":
      step = float(item["threshold"])
      if position_return_pct is None or position_peak_return_pct is None or step <= 0:
          results.append(False)
      else:
          stop_level = (math.floor(position_peak_return_pct / step) - 1) * step
          results.append(position_return_pct <= stop_level)
      continue
  ```

  재귀 호출(`eval_group(item, indicators, position_return_pct, position_holding_bars)`
  등 하위 그룹으로 내려가는 지점)에도 새 파라미터를 그대로 전달해야 한다.
- **`item["operator"]`는 이 지표에서 의도적으로 무시한다** — 손절선 자체가
  `threshold`(step)로부터 매번 새로 계산되는 이동값이라, 다른 지표처럼
  `apply_operator(value, operator, threshold)` 일반 경로를 쓸 수 없다. UI에서는
  `fixedOperator: "<="`로 고정해 혼란을 막는다(아래 5번).
- 파일 상단에 `import math` 추가.

### 3. `engine/condition_strategy.py`

`ConditionTreeStrategy`에 `self._peak_return_pct: float | None = None`을
`self._entry_bar`와 동일한 생명주기로 추가한다:

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
            self._sell_cond, self._sell_inds,
            position_return_pct=position_return_pct,
            position_holding_bars=position_holding_bars,
            position_peak_return_pct=self._peak_return_pct,
        ):
            self.sell()
```

`_ensure_indicator()`는 위에서 설명한 대로 수정 불필요.

### 4. `backend/main.py` — `INDICATOR_CATALOG`

`STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`/`HOLDING_PERIOD_BARS` 항목과 같은 자리
(`category: "손익"`)에 추가:

```python
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

### 5. 프론트엔드 (3개 파일, 기존 손익 지표 3종과 동일 패턴)

- `frontend/lib/indicator-guide.ts` — `TRAILING_STOP_STEP_PCT` 키로
  `meaning`/`params`/`formula`/`thresholdExample`/`usage` 항목 추가(계단 공식과
  "본절 잠금보다 촘촘하게 이익을 보호" 취지 설명).
- `frontend/components/StrategyConditionBuilder.tsx` — `POSITION_RELATIVE_DEFAULTS`
  맵에 `TRAILING_STOP_STEP_PCT: 5` 추가(추천 임계값 기본 5%).
- `frontend/lib/indicator-example-builder.ts` — 기존
  `case 'STOP_LOSS_PCT': case 'TAKE_PROFIT_PCT': case 'HOLDING_PERIOD_BARS':` 블록에
  묶지 않고 **별도 case**를 추가한다. 계단식은 "수익률"만으로는 계단 로직이
  안 보이므로, 예시 테이블에 "고점 수익률"과 "그 시점 손절선" 열을 추가로
  계산해 보여준다(백엔드와 동일한 공식을 TS로 재현 — 이 파일은 원래 UI
  예시용으로 백엔드 계산을 참고만 하는 성격이라 기존 관례와 일치).

## 설계 (Phase 2 — 실거래 연동, 별도 플랜에서 구현)

이 섹션은 설계 의도만 기록한다. 실제 구현 플랜/태스크 분해는 Phase 1이 끝나고
검증된 뒤 별도로 작성한다.

- `trading/db.py`: `positions` 테이블에 `peak_return_pct REAL NOT NULL DEFAULT 0`
  컬럼을 마이그레이션 추가(기존 `entry_fee` 추가 패턴과 동일 — `ALTER TABLE`).
  포지션 오픈 시 0으로 시작, 클로즈 시 초기화 불필요(행 자체가 새로 생김).
- `trading/signal_engine.py`: `_position_context()`가 저장된 `peak_return_pct`와
  현재 `position_return_pct` 중 큰 값으로 갱신(갱신된 값을 그 호출자가 DB에
  다시 써야 함 — 이 함수 자체는 순수 계산이라 쓰기 책임 소재를 플랜에서
  명확히 정해야 한다), `matched_risk_exit_indicator()`에도 같은 계단 공식을
  반영해 캔들 종가 루프(`_run_strategy_loop`)에서도 트레일링이 동작하게 한다.
- `trading/daemon.py`의 `_run_risk_exit_loop`: **가장 무거운 부분.** 매 tick마다
  `peak_return_pct`를 갱신·저장해야 실시간 보호 의미가 있는데, 이 함수는
  이미 7라운드 리뷰를 거쳐 "언제 lock 밖/안에서 무엇을 읽고 쓰는지"가 정교하게
  맞춰진 상태다. Phase 2 플랜의 명시적 제약으로 "기존 락 구조/가드 순서를
  바꾸지 않고, peak 갱신 쓰기만 기존 패턴에 맞춰 끼워 넣는다"를 못박아야 한다.
  tick마다 DB 쓰기가 늘어나는 것도 [[2026-09-06-regime-strategy-auto-pipeline-design]]
  최종 리뷰에서 지적된 "daemon과 동시 쓰기 시 sqlite3.OperationalError" 위험을
  키우는 방향이라, 배치/스로틀링(예: N초에 한 번만 peak을 flush) 여부를
  Phase 2 브레인스토밍에서 별도로 정해야 한다.

## 에러 처리 / 엣지 케이스

- `step_pct <= 0`: 위에서 정의한 대로 항상 미발동(`False`). 별도 예외를 던지지
  않는다 — 조건 트리 지표는 계산 시점에 유효성 검증을 하지 않는 기존 관례를
  따른다(사용자가 UI/파이프라인에서 이상한 값을 넣으면 그냥 안 팔릴 뿐).
- `position_return_pct`가 `None`(포지션 없음/진입가 0 등): 결과 `False` —
  `STOP_LOSS_PCT`/`HOLDING_PERIOD_BARS`와 동일한 관례.
- `position_peak_return_pct`가 `None`(포지션 있지만 아직 peak 계산 전 — 이론상
  발생하지 않지만 방어적으로): 결과 `False`.
- `eval_group_values()`(라이브용) 경로도 `eval_group()`과 동일 분기를 가지므로,
  라이브 신호 평가(백테스트 아님, 캔들 종가 기준 `_run_strategy_loop`)에서도
  Phase 2 없이 이미 "값이 주어지면" 평가 가능 — 다만 Phase 1만으로는
  `position_peak_return_pct`를 채워 넣는 실거래 쪽 배선이 없어 항상 `None`이라
  실질적으로 미발동 상태로 남는다(Phase 2가 그 배선을 채운다).

## 테스트 전략

- `tests/test_condition_tree.py`: 기존 `STOP_LOSS_PCT`/`HOLDING_PERIOD_BARS`
  테스트 패턴(`test_eval_group_values_position_relative_indicators_unaffected_by_unknown_handling`,
  `test_eval_group_values_holding_period_bars_false_without_position` 등)을
  그대로 참고해 `TRAILING_STOP_STEP_PCT`용으로 추가:
  - 계단 경계값(peak=6,step=5 → stop=0 → return=0이면 매도, return=0.1이면 매도 안 함 등)
  - `position_return_pct`/`position_peak_return_pct` 중 하나라도 `None`이면 `False`
  - `step_pct <= 0`이면 `False`
  - `find_unknown_indicators()`가 새 지표를 "알 수 없는 지표"로 잘못 분류하지
    않는지(기존 `STOP_LOSS_PCT` 커버리지와 동일 선상에서 확인)
- `tests/test_condition_strategy.py`: `ConditionTreeStrategy`에 대한 통합
  시나리오 테스트 — 합성 가격 시퀀스(예: 100→106→109→115→하락)를 backtrader로
  직접 돌려, 계단식 손절선을 통과하는 봉에서 실제로 매도되는지 확인(본
  스펙의 "설계 (Phase 1)" 1번 공식 예시 표를 그대로 시나리오로 사용).
- `backend/main.py`의 `INDICATOR_CATALOG`에 새 항목이 스키마(필수 키:
  `value`/`label`/`category`/`params`)를 만족하는지 확인하는 테스트가 기존에
  있다면 그 테스트가 새 항목도 자동으로 커버하는지 확인, 없다면 새로 추가하지
  않는다(기존 커버리지 범위를 임의로 넓히지 않음, YAGNI).
- 프론트엔드 3개 파일은 TypeScript라 이 저장소의 기존 관례상 별도 자동 테스트가
  없다면 추가하지 않는다(기존 `STOP_LOSS_PCT` 등도 테스트 없이 수동 확인
  범위인지 구현 단계에서 확인).

## 완료 기준 (Phase 1)

- 매도 조건 트리에 `{"indicator": "TRAILING_STOP_STEP_PCT", "params": {}, "operator": "<=", "threshold": 5}`
  블록을 넣고 백테스트를 돌리면, 위 계단 공식대로 손절선이 따라 올라가며
  매도가 트리거된다.
- `eval_group()`/`eval_group_values()` 양쪽 모두 동일하게 동작(쌍둥이 함수
  불일치 없음).
- `/strategy-library` 등 조건 빌더 UI에서 다른 손익 지표와 동일한 방식으로
  이 지표를 선택하고, 설명/예시를 볼 수 있다.
- 신규 유닛/통합 테스트 전부 통과, 기존 테스트 스위트 회귀 없음.
- Phase 2(실거래 연동)는 이 스펙에 설계만 기록되고 구현은 별도 플랜으로 남는다.
