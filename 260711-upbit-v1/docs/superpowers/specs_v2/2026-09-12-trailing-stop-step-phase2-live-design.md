# 계단식 트레일링 스탑 Phase 2 — 실거래 연동 설계 스펙

## 배경

Phase 1(백테스트 엔진 + 조건트리 + UI)은 이미 완료·배포됐다
(`9317aa0`/`e9016c3`/`b81c342`, [[2026-09-11-trailing-stop-step-design]] 참고).
이후 `scripts/regime_strategy_pipeline.py`에도 트레일링 스탑 step(2,3,4,5)을
grid search로 함께 탐색하도록 확장했다(`604dffd`).

이 확장 작업 중 "라이브 실매매에도 반영되냐"는 질문에서 출발해 확인한 결과,
`trading/signal_engine.py`가 `eval_group_values()`를 호출할 때
`position_peak_return_pct`를 아예 넘기지 않아 `TRAILING_STOP_STEP_PCT`는 라이브
전략에 매핑돼 있어도 **항상 미발동(False)** 상태다. Phase 1 스펙 문서의 "Phase 2"
섹션이 이 배선을 별도 세션에서 설계하기로 예고해뒀고, 이 문서가 그 설계다.

## 이번 세션 브레인스토밍으로 확정된 결정

1. **백테스트·라이브 일관성 유지** — 트레일링 스탑을 라이브 전용 별도 안전망으로
   분리하지 않는다. STOP_LOSS_PCT/TAKE_PROFIT_PCT처럼 grid search로 찾은 값을
   백테스트와 라이브에 동일하게 적용한다("백테스트한 대로 라이브가 움직인다"는
   기존 원칙 유지). 라이브 전용 분리안은 이번 세션에서 명시적으로 검토 후 기각.
2. **고점(peak) 추적 기준: 실시간 tick** — 봉 마감을 기다리지 않고 매 tick마다
   갱신한다. 봉 마감 기준으로만 하면 봉 중간의 스파이크(예: 봉 중 +10% 찍고
   종가는 +3%)가 고점에 전혀 반영되지 않아 이익 보호라는 지표 취지에 맞지 않기
   때문.
3. **매도 트리거 시점: 실시간 tick 즉시** — STOP_LOSS_PCT/TAKE_PROFIT_PCT와
   동일하게 `_TICKER_RISK_INDICATORS` 안전망 경로에 편입해 tick마다 즉시
   판단한다(봉 마감까지 기다리지 않음).
4. **API 호출 추가 없음** — `daemon.py`의 `_run_risk_exit_loop`가 이미
   `upbit_ws.stream_ticker([market])`로 웹소켓을 구독해 매 tick을 받고 있다(⑤-4c
   설계, STOP_LOSS_PCT/TAKE_PROFIT_PCT가 쓰는 것과 같은 연결). 고점 추적도 이
   기존 스트림 데이터로 메모리 계산만 추가하면 되고, 새 REST 호출/폴링은
   필요 없다.
5. **DB 영속화는 주기적으로만** — tick마다 SQLite에 쓰지 않는다. 이 프로젝트는
   `trading.db` 동시쓰기 부하/락 문제를 이미 여러 번 겪었다([[2026-09-06-regime-strategy-auto-pipeline-design]]
   최종 리뷰에서 지적된 `sqlite3.OperationalError` 우려 등). 고점은 메모리에서
   즉시 갱신하되, DB에는 스로틀된 주기로만(+청산 직전엔 반드시) flush한다.

## 목표

1. `trading/db.py`의 `positions` 테이블에 진입 후 고점을 영속화할 컬럼을 추가한다.
2. `trading/daemon.py`의 `_run_risk_exit_loop`가 매 tick마다 고점을 메모리에서
   갱신하고, 계단식 손절선을 계산해 STOP_LOSS_PCT/TAKE_PROFIT_PCT와 동일한
   반응 속도(실시간)로 청산을 트리거한다.
3. 고점 값은 스로틀된 주기 + 청산 직전에만 DB에 flush한다.
4. `trading/signal_engine.py`의 봉 마감 평가 경로(`evaluate_signals` →
   `eval_group_values`)에도 저장된 고점을 넘겨, 다른 지표와 AND/OR로 결합된
   조건 트리 안에서도(예: `(RSI>70) OR TRAILING_STOP_STEP_PCT`) 정합적으로
   평가되게 한다 — STOP_LOSS_PCT/TAKE_PROFIT_PCT가 이미 "실시간 안전망 +
   봉마감 조건트리 평가" 이중 경로를 갖는 것과 동일한 패턴.
5. `engine/condition_tree.py`의 계단 공식을 라이브 쪽(`matched_risk_exit_indicator`)이
   중복 구현하지 않고 공유하도록 작은 리팩터링을 겸한다(쌍둥이 함수 드리프트 방지).

## 비범위

- 봉 고가(High)/저가(Low) 기준 트레일링 — 여전히 종가/tick가 기준(Phase 1과 동일).
- 트레일링 스탑을 grid search 대상에서 빼고 라이브 전용 안전망으로 분리하는
  방식 — 이번 세션에서 검토 후 기각(위 결정 1).
- 포지션별 고점 이력을 매매일지(journal) UI에 표시하는 기능 — 필요해지면 별도 요청.
- `_run_risk_exit_loop`의 기존 락 구조/가드 순서 변경 — 이 함수는 이미 7라운드
  리뷰를 거쳐 "언제 lock 밖/안에서 무엇을 읽고 쓰는지"가 정교하게 맞춰진
  상태다(주석 참고). 이 스펙의 변경은 **기존 패턴에 peak 갱신 로직을 끼워
  넣는 것**으로 한정하고, lock 획득 시점/가드 순서/쿨다운 로직은 건드리지 않는다.
- daemon 재시작 시 마지막 flush 이후의 고점 손실을 완전히 막는 것(예: graceful
  shutdown 훅에서 flush) — 이번 스펙은 주기적 flush의 알려진 한계로 감수한다
  (아래 "에러 처리" 참고).

## 설계

### 1. `engine/condition_tree.py` — 공식 공유 (작은 선행 리팩터링)

현재 `eval_group()`/`eval_group_values()` 안에 인라인으로 있는 계단 공식을
순수 함수로 뽑아 라이브 쪽과 공유한다. 동작 변경 없음(순수 리팩터링):

```python
def trailing_stop_step_level(peak_return_pct: float, step_pct: float) -> float | None:
    """계단식 트레일링 스탑의 현재 손절선(%)을 계산한다. step_pct<=0이면 None
    (항상 미발동을 뜻함) — 호출부가 결과를 그대로 bool 판정에 쓸 수 있도록
    None 처리는 호출부 책임으로 남긴다."""
    if step_pct <= 0:
        return None
    return (math.floor(peak_return_pct / step_pct) - 1) * step_pct
```

`eval_group()`/`eval_group_values()`의 `TRAILING_STOP_STEP_PCT` 분기를 이 함수를
쓰도록 교체한다(결과는 기존과 동일해야 함 — 회귀 테스트로 확인).

### 2. `trading/db.py` — 스키마 + 함수

- `positions` 테이블에 `peak_return_pct REAL NOT NULL DEFAULT 0` 컬럼을 추가한다
  (`entry_fee` 컬럼을 추가했던 것과 동일한 `ALTER TABLE ... ADD COLUMN` 마이그레이션
  패턴, `trading/db.py:225` 주변 참고). 포지션 오픈 시 기본값 0에서 시작한다
  (포지션이 새 행이므로 별도 리셋 로직 불필요).
- 새 함수:
  ```python
  def update_position_peak_return_pct(position_id: str, peak_return_pct: float) -> None:
      """열려있는 포지션의 고점 수익률을 갱신한다. status='open' 가드로 이미
      닫힌 포지션에 대한 뒤늦은 쓰기를 무시한다(daemon 쿨다운/재시도 경합 시
      안전)."""
  ```
  (`update_position_qty_and_price` 등 기존 함수와 동일한 `WHERE id = ? AND
  status = 'open'` 가드 패턴을 따른다, `trading/db.py:735` 주변 참고.)

### 3. `trading/daemon.py` — `_run_risk_exit_loop` (핵심 변경)

`last_risk_exit_attempt = float("-inf")` 초기화 직후(`daemon.py:210` 주변,
`async for tick in ...` 진입 전)에 다음 로컬 상태를 추가한다:

```python
tracked_position_id: str | None = None
local_peak_return_pct: float = 0.0
last_peak_flush = float("-inf")
```

매 tick, `position = position_manager.get_open_position(strategy_id)` 직후
(`daemon.py:220` 주변)에:

```python
if position["id"] != tracked_position_id:
    # 새 포지션(또는 이 태스크의 첫 tick) — DB에 남아있던 값에서 이어서 추적한다
    # (daemon 재시작 시에도 마지막 flush 값부터 재개, 완전한 정확성은 비범위).
    tracked_position_id = position["id"]
    local_peak_return_pct = position["peak_return_pct"]

position_return_pct = (trade_price - position["entry_price"]) / position["entry_price"] * 100
local_peak_return_pct = max(local_peak_return_pct, position_return_pct)

now = time.monotonic()
if now - last_peak_flush >= _PEAK_FLUSH_INTERVAL_SEC:
    db.update_position_peak_return_pct(position["id"], local_peak_return_pct)
    last_peak_flush = now
```

`_PEAK_FLUSH_INTERVAL_SEC`는 모듈 상수로 추가한다(권장 기본값 30초 —
`_RISK_EXIT_RETRY_COOLDOWN_SEC`와는 별개 상수, 정확한 값은 구현 플랜에서
확정). **이 블록은 lock 밖에서 실행한다** — 기존 주석대로 "포지션 조회/임계치
판정/쿨다운 사전필터는 lock 밖에서 수행"하는 원칙과 동일 선상이며, `_run_strategy_loop`의
주문실행 구간과 불필요하게 경합하지 않기 위함이다.

`matched_risk_exit_indicator()` 호출 두 지점(사전 체크 `daemon.py:239`, lock 안
재확인 `daemon.py:287`)에 `local_peak_return_pct`(사전 체크)와 lock 안에서 다시
읽은 `fresh_position["peak_return_pct"]`/`fresh_position_return_pct` 중 큰 값
(재확인)을 함께 넘기도록 수정한다 — 기존 "lock 안에서는 fresh 값으로 재계산"
원칙(3라운드 M3)과 동일하게, peak도 fresh 값 기준으로 재확인한다.

**청산 직전 강제 flush**: lock 안에서 `fresh_matched`가 확정된 직후(실제
`exit_for_risk()` 호출 직전)에 `db.update_position_peak_return_pct()`를 한 번 더
호출해, 실제로 청산을 유발한 고점 값이 주기적 flush 타이밍과 무관하게 정확히
남도록 한다.

### 4. `trading/signal_engine.py`

- `_TICKER_RISK_INDICATORS`에 `"TRAILING_STOP_STEP_PCT"`를 추가한다(`signal_engine.py:296`).
  `has_risk_exit_conditions()`는 이 집합 멤버십만 검사하므로 수정 없이 자동으로
  이 지표만 있는 전략도 ticker WS를 열게 된다.
- `matched_risk_exit_indicator()` 시그니처에 `position_peak_return_pct: float |
  None` 파라미터를 추가하고, `TRAILING_STOP_STEP_PCT` 블록을 만나면
  `engine.condition_tree.trailing_stop_step_level()`(위 1번 리팩터링)을 호출해
  판정한다:
  ```python
  if block["indicator"] == "TRAILING_STOP_STEP_PCT":
      if position_peak_return_pct is None:
          continue
      stop_level = trailing_stop_step_level(position_peak_return_pct, float(block["threshold"]))
      if stop_level is not None and position_return_pct <= stop_level:
          return block["indicator"]
      continue
  ```
- `_position_context()`가 반환하는 튜플을 `(position_return_pct, position_holding_bars,
  position_peak_return_pct)` 3-tuple로 확장한다. `position_peak_return_pct`는
  `max(position["peak_return_pct"], position_return_pct)`로 계산해, 마지막 주기적
  flush 이후 값이 약간 stale해도 봉 마감 시점엔 방금 계산한 현재 수익률로
  즉석 보정한다(추가 DB 쓰기 없이 읽기만으로 보정 — 이 경로는 봉 마감마다
  1회뿐이라 쓰기 부하 우려가 없다).
- `evaluate_signals()`의 `eval_group_values()` 호출 두 곳(`signal_engine.py:230-231`)에
  `position_peak_return_pct` 인자를 추가한다.

## 에러 처리 / 엣지 케이스

- **daemon 재시작 시 peak 손실**: 마지막 flush 이후의 고점 상승분을 잃는다 —
  사용자가 명시적으로 감수하기로 한 트레이드오프(결정 5). 완전한 정확성이
  필요해지면 추후 "종료 시그널 핸들러에서 flush" 등을 별도로 검토(이번
  스펙 비범위).
- **포지션 전환**: 같은 전략이 청산 후 새로 진입하면 `position["id"]`가
  바뀌므로 `tracked_position_id` 불일치를 감지해 로컬 peak을 새 포지션의 DB
  값(0)으로 리셋한다.
- **마이그레이션 이전에 이미 열려있던 포지션**: 컬럼 기본값 0으로 채워지므로
  정상 동작은 하지만, 실제 진입 이후 고점보다 낮은 값에서 추적이 재개될 수
  있다 — 문서화만 하고 별도 백필은 하지 않는다(YAGNI, 마이그레이션 시점에
  열려있는 포지션 수는 극히 적을 것으로 예상).
- Phase 1과 동일: `step <= 0` → 항상 미발동, `position_return_pct`/
  `position_peak_return_pct` 중 하나라도 없으면 미발동.

## 테스트 전략

- `engine/condition_tree.py`: `trailing_stop_step_level()` 추출 리팩터링 후
  기존 `test_eval_group_values_trailing_stop_step_pct_*` 테스트 전체가 그대로
  통과하는지 확인(순수 리팩터링 — 동작 불변 확인용 회귀 테스트).
- `trading/db.py`: 마이그레이션 후 기존 오픈 포지션 조회 시 `peak_return_pct`
  기본값 0 확인, `update_position_peak_return_pct()`가 `status='open'` 가드를
  지키는지(닫힌 포지션엔 쓰지 않음) 단위 테스트.
- `trading/signal_engine.py`: `matched_risk_exit_indicator()`에 대해 계단 공식
  경계값 테스트(Phase 1의 `test_eval_group_values_trailing_stop_step_pct_*`
  패턴과 동일한 케이스: 계단 미달/통과, `step<=0`, `peak=None`), `_position_context()`가
  DB 저장값과 현재 수익률 중 큰 값을 돌려주는지 확인.
- `trading/daemon.py`의 `_run_risk_exit_loop`: 기존 동시성 테스트 스위트(⑤-4c
  관련, 7라운드 리뷰 이력)에 peak 추적/스로틀 flush/청산 직전 강제 flush
  시나리오를 추가 — 정확한 기존 테스트 파일 위치는 구현 플랜 작성 시 확인.

## 완료 기준

- 실거래 전략에 `TRAILING_STOP_STEP_PCT` 조건이 있으면, tick마다 실시간으로
  고점이 갱신되고 계단식 손절선이 즉시 평가되어 STOP_LOSS_PCT/TAKE_PROFIT_PCT와
  동일한 반응 속도로 청산된다.
- peak 값은 tick마다가 아니라 주기적으로만(+청산 직전 강제로) DB에 기록된다.
- daemon 재시작 후에도 마지막 flush 시점 기준으로 추적이 이어진다.
- 봉 마감 시점 조건트리 평가(`evaluate_signals`) 경로도 동일한 고점 기준으로
  정합적으로 동작한다.
- `_run_risk_exit_loop`의 기존 락/가드 구조는 변경되지 않는다.
- 계단 공식은 `engine/condition_tree.py`에 단일 정의로 남아 백테스트/라이브가
  같은 함수를 공유한다(공식 드리프트 불가능).
- 신규 유닛 테스트 통과, 기존 테스트 스위트 회귀 없음.
- 실제 구현(코드 변경)은 이 스펙을 기반으로 별도 플랜(`writing-plans` →
  `docs/superpowers/plans_v2/`)에서 태스크 분해 후 진행한다 — 이 세션에서는
  스펙만 작성한다.
