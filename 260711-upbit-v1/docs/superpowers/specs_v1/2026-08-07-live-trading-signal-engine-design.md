# 라이브 트레이딩 서브플랜⑤-2 — signal_engine.py Design Spec

## 배경 및 목표

서브플랜⑤(트레이딩 엔진 코어)의 두 번째 조각. ⑤-1(`docs/superpowers/specs_v1/2026-08-07-live-trading-position-risk-manager-design.md`)이
DB CRUD + 자금관리 + 서킷브레이커를 끝냈고, 서브플랜②③(`trading/live_indicators.py`,
`LIVE_INDICATOR_FACTORY` 39개)과 서브플랜①(`engine/condition_tree.py`의
`eval_group_values()`)이 이미 완성돼 있다. 이 스펙은 그 셋을 실제로 결합해 "새 봉이
마감되면 매수/매도 신호를 계산해 기록"하는 `trading/signal_engine.py`를 확정한다.

## 범위

**이 스펙에서 확정하는 것:**
- `trading/db.py`에 추가할 `signals` CRUD(이 서브플랜이 실제로 쓰는 것만)
- `upbit_data_service.py`의 `_timeframe_duration()`을 공개 `timeframe_duration()`으로
  리네임(서브플랜④ Task4가 `binance_data_service.py`에 했던 것과 동일 패턴)
- `trading/signal_engine.py`의 정확한 함수 시그니처와 동작(캔들 조회·워밍업 계산·보조마켓
  병합·B그룹 외부데이터 결합·지표 계산·조건평가·기록·일시정지/재개)

**이 스펙에서 다루지 않는 것(후속 서브플랜에서):**
- 실제 주문 실행 여부 결정(서킷브레이커 체크 → `order_executor.enter()`/`exit()` 호출) —
  ⑤-3/⑤-4의 몫. 이 서브플랜은 신호의 True/False/판단불가만 계산·기록한다.
- 손절/익절의 ticker 기반 실시간 평가(기반 스펙: "캔들 마감을 기다리지 않는다") — 이건
  daemon의 ticker 소비 루프(⑤-4)가 `upbit_ws.stream_ticker()` + 이 서브플랜과 같은
  `eval_group_values()` 호출을 재사용해서 별도로 한다. `signal_engine.py`는 캔들 기반
  평가만 다룬다.
- `orders` 테이블 CRUD(`signals.resulting_order_id`를 채우는 건 주문이 실제로 나간 뒤의
  일이라 ⑤-3의 몫) — 이 서브플랜은 `resulting_order_id=NULL`로만 기록한다.

## 핵심 결정

### 결정 1 — A그룹/B그룹을 이 모듈 레벨에서 구분하지 않는다

`LIVE_INDICATOR_FACTORY[name](df, **params).iloc[-1]`를 모든 지표에 동일하게 호출한다.
차이는 **df를 준비하는 방식**뿐이다:
- A그룹(대상마켓 OHLCV만): 대상마켓 캔들 롤링 윈도우를 그대로 df로 씀.
- 보조마켓형(MARKET_TREND/BTC_CORRELATION/USDT_CORRELATION): 필요한 보조마켓의 캔들을
  `required_aux_markets()`로 확인해 조회하고, `btc_close`/`usdt_close` 컬럼으로 병합.
- 외부API형(FEAR_GREED_CMC/KOREA_PREMIUM/FUNDING_RATE): `fetch_live_*()`로 얻은 현재값을
  **마지막 행에만** 채운다(나머지 행은 NaN이어도 무방 — 평가는 항상 `.iloc[-1]`만 보므로).

### 결정 2 — 보조마켓 병합은 백테스트와 동일하게 `ffill().bfill()`을 쓴다

`backend/main.py`의 백테스트 병합 로직(`df[line_name] = df[line_name].ffill().bfill()`)과
똑같이 한다 — 이건 B그룹의 "지연/실패 시 forward-fill 금지"(스펙 결정8)와는 다른 문제다.
결정8은 "외부 데이터 소스 자체가 통째로 못 갱신되는 상황"을 다루고, 이건 "보조마켓 캔들
집합에서 특정 타임스탬프 하나가 살짝 비는 정상적인 갭"을 다룬다 — 백테스트-라이브 패리티를
지키기 위해 같은 방식(작은 갭은 채움)을 쓴다.

### 결정 3 — 워밍업 봉 수는 `max_required_period(조건트리) + 5`

`engine/condition_tree.max_required_period()`가 이미 백테스트에서 쓰는 지표 워밍업
추정치다. +5는 롤링 상관계수(`BTC_CORRELATION`/`USDT_CORRELATION`)가 `period+1`봉을
필요로 하는 것 등에 대한 여유분이다(정확한 워밍업 계산은 지표마다 다르므로, 근사치에
안전 마진을 더하는 기존 관례를 그대로 따름).

### 결정 4 — `upbit_data_service._timeframe_duration()`을 공개한다

봉 하나의 길이(분)를 알아야 워밍업 조회 시작 시각과, 포지션 보유 봉 수(아래 결정6)를
계산할 수 있다. `upbit_data_service.py`에 이미 있는 private `_timeframe_duration()`을
공개 `timeframe_duration()`으로 바꿔 재사용한다(서브플랜④ Task4가 `binance_data_service.py`에
한 것과 동일 패턴 — 새로 만들지 않음).

### 결정 5 — 신호는 봉 하나당 매수/매도 각각 별도 행으로 기록한다

`signals.signal_type`이 'buy'|'sell' 중 하나인 스키마이므로, `evaluate_signals()` 한
호출이 새 봉을 처리할 때 최대 2행(매수 판정 1행 + 매도 판정 1행)을 기록한다. 각 행의
`indicator_snapshot_json`은 그 판정에 쓰인 `values` 딕셔너리 전체(디버깅·백테스트 대조용,
기반 스펙 명시)를 담는다.

### 결정 6 — 판단불가(None) → 일시정지, 재개 전에 서킷브레이커 상태를 확인한다

기반 스펙 결정8: "판단불가로 일시정지된 경우는 다음 성공적 fetch가 이뤄지면 자동으로
`status='running'`으로 재개된다." 하지만 `live_strategies`에는 "왜 정지됐는지"를 구분하는
필드가 없다 — 서킷브레이커로 정지된 전략(`⑤-1`)을 이 자동재개 로직이 잘못 풀어버릴 위험이
있다. 그래서 `evaluate_signals()`가 재개를 시도하기 직전에 `trading.db.get_circuit_breaker_state()`
로 **오늘자 트립 여부**를 확인하고, 트립돼 있으면 재개하지 않는다(⑤-1이 이미 만든 CRUD와
`risk_manager.today_kst()`를 재사용 — 새 모듈 의존 추가 없음).

- 매수 조건 그룹 또는 매도 조건 그룹 **둘 중 하나라도** `eval_group_values()`가 `None`을
  반환하면(=판단불가) 그 후보를 `skip_reason='unknown'`으로 기록하고
  `live_strategies.status='paused'`로 전환한다.
- 매수·매도 둘 다 `None`이 아니면(정상 판정 가능) **그리고** 현재 `status='paused'`이면
  **그리고** 오늘자 서킷브레이커가 트립돼 있지 않으면 `status='running'`으로 재개한다.

### 결정 7 — 포지션 관련 컨텍스트(`position_return_pct`/`position_holding_bars`)는
`position_manager.get_open_position()`으로 조회한다

`STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`/`HOLDING_PERIOD_BARS` 조건(`POSITION_RELATIVE_INDICATORS`)
평가에 필요하다. 오픈 포지션이 있으면:
- `position_return_pct = (최신 종가 - entry_price) / entry_price * 100`
- `position_holding_bars = int((최신 봉 시각 - entry_time) / 봉길이)`

오픈 포지션이 없으면 둘 다 `None`을 넘긴다(`eval_group_values()`가 이미 이 경우를
"조건 False"로 처리하도록 구현돼 있음, ⑤-1 이전 작업).

## `trading/db.py`에 추가할 CRUD 함수

```python
def insert_signal(
    live_strategy_id: str, signal_type: str, candle_time: str,
    indicator_snapshot_json: str, skip_reason: str | None = None,
) -> str:
    """signals 행을 생성한다. resulting_order_id는 항상 NULL로 시작한다(주문이 실제로
    나간 뒤 채우는 건 ⑤-3의 몫이라 이 서브플랜은 갱신 함수를 만들지 않는다)."""
```

## `upbit_data_service.py` 변경

`_timeframe_duration(timeframe: str) -> timedelta`를 `timeframe_duration(timeframe: str) ->
timedelta`로 리네임(공개). 유일한 내부 호출부(`get_candles()` 안)도 함께 바꾼다.

## `trading/signal_engine.py`

```python
def evaluate_signals(live_strategy_id: str, now: datetime | None = None) -> dict:
    """새 봉 마감을 감지하면 지표 계산 + 조건평가를 수행해 signals에 기록하고,
    live_strategies.status(paused/running)를 필요시 갱신한다. 새 봉이 아니면 즉시
    {"new_candle": False, ...}로 조기 반환한다(daemon이 폴링 주기마다 안전하게
    반복 호출할 수 있는 멱등적 인터페이스).

    반환값: {
        "new_candle": bool,
        "candle_time": str | None,
        "buy_signal": bool | None,   # None = 판단불가
        "sell_signal": bool | None,
        "paused": bool,              # 이 호출로 일시정지됐으면 True
        "resumed": bool,             # 이 호출로 재개됐으면 True
    }
    """
```

내부 흐름:
1. `db.get_live_strategy(live_strategy_id)` — 없으면 `ValueError`.
2. `buy_conditions`/`sell_conditions`를 JSON 파싱.
3. `required_bars = max(max_required_period(buy_conditions), max_required_period(sell_conditions)) + 5`.
4. `upbit_data_service.get_candles(market, timeframe, start, now)`로 대상마켓 캔들 조회
   (`start`는 `now - required_bars * timeframe_duration(timeframe)`).
5. 최신 캔들이 없으면(빈 df) `{"new_candle": False, ...}` 반환.
6. 최신 `candle_time`이 `live_strategies.last_processed_candle_time`보다 새롭지 않으면
   `{"new_candle": False, ...}` 반환.
7. `required_aux_markets(buy_conditions) | required_aux_markets(sell_conditions)`로 보조마켓
   확인 → 필요하면 같은 방식으로 조회해 `btc_close`/`usdt_close` 컬럼 병합(결정2).
8. 조건 트리에 쓰인 지표 이름 집합을 `collect_blocks()`로 모아, B그룹 지표가 있으면
   `fetch_live_fear_greed_value()`/`fetch_live_funding_rate_value(market)`/
   `fetch_live_binance_close(market, timeframe)`를 호출해 df 마지막 행에 채움(결정1).
   `KOREA_PREMIUM`이 필요하면 `compute_korea_premium_value()`로 `korea_premium_value`
   컬럼도 채운다.
9. 조건 트리에 쓰인 모든 `(indicator, params)` 조합에 대해
   `LIVE_INDICATOR_FACTORY[name](df, **params).iloc[-1]`을 호출해 `values` 딕셔너리 구성
   (`indicator_key(name, params)` 키).
10. `position_manager.get_open_position(live_strategy_id)`로 포지션 컨텍스트 조회(결정7).
11. `eval_group_values(buy_conditions, values, position_return_pct, position_holding_bars)`,
    `eval_group_values(sell_conditions, values, ...)` 각각 호출.
12. `db.insert_signal(...)`을 buy/sell 각 1행씩 호출(결정5).
13. 결정6에 따라 일시정지/재개 판단 및 `db.update_live_strategy_status()` 호출.
14. `db.update_live_strategy_last_candle(live_strategy_id, candle_time)`.
15. 결과 dict 반환.

## 에러 처리

- 존재하지 않는 `live_strategy_id` → `ValueError`(다른 모듈들과 일관).
- `LIVE_INDICATOR_FACTORY`에 없는 지표 이름이 조건 트리에 있으면 → `ValueError`(승인
  단계에서 `find_unknown_indicators()`로 이미 걸렀어야 하는 상황이라, 여기서 만나면 버그
  신호이므로 조용히 넘기지 않는다).
- 캔들 조회 자체가 실패(업비트 API 에러)하면 예외를 그대로 전파한다 — 재시도 정책은
  daemon의 몫(⑤-4).

## 테스트 전략

- `LIVE_INDICATOR_FACTORY` 호출부는 실제 `make_oscillating_df()`류 합성 데이터로 최소
  1~2개 지표(A그룹 하나, B그룹 하나)를 골라 end-to-end 검증 — 39개 전부를 다시 검증하지
  않는다(②③에서 이미 골든테스트 끝남, 이 서브플랜은 "결합"만 검증).
- `upbit_data_service.get_candles()`/`fetch_live_*()` 등 네트워크 관련 함수는 전부
  monkeypatch로 대체(실제 HTTP 없음) — 캔들 조회는 고정 DataFrame을 반환하는 스텁으로.
- 판단불가→일시정지, 재개 시 서킷브레이커 확인 로직은 `trading/db.py`/`trading/risk_manager.py`
  (⑤-1, 이미 검증됨)를 실제 SQLite로 왕복시켜 검증.

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음.
- 결정1~7이 상충하지 않는지 확인: 결정1(A/B그룹 구분 없이 동일 dispatch) ↔ 결정1의 B그룹
  df 준비 방식이 ③의 `fetch_live_*()`/`compute_korea_premium_value()` 시그니처와 정확히
  맞물림 ↔ 함수 흐름 절의 Step 8이 그대로 구현. 결정6(재개 전 서킷브레이커 확인)이
  ⑤-1의 기존 CRUD(`get_circuit_breaker_state`)만으로 가능함을 확인해 새 의존성을 만들지
  않았다.
- "이 스펙에서 다루지 않는 것"에 ticker 기반 손절/익절과 실제 주문 실행 결정을 명시해
  ⑤-3/⑤-4로 범위를 명확히 넘겼다.
