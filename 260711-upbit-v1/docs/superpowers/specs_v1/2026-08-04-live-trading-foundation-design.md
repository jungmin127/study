# 업비트 자동매매(라이브 트레이딩) 시스템 — Foundation Design Spec

## 배경 및 목표

`260711-upbit-v1`은 처음엔 EDA 대시보드로 시작했지만([[upbit-v1-project-vision]]), 실제
목표는 항상 "백테스트 + 업비트 실거래 API + 자동매매"였다. 지금까지는 백테스트/그리드서치
기능을 web으로 포팅하는 단계였고, 이제 이 프로젝트에서 **가장 크고 중요한 작업**인 실제
자동매매 모듈을 시작한다.

사용자 요구사항 원문(4개 축):
1. 매매 Execution & 신호 감지 로직(신호 판단 시점, 주문 실행 전략 3종, 자금관리)
2. 모니터링/수동개입/텔레그램 제어
3. 성과 분석 및 매매일지 대시보드(백테스트 vs 실매매 비교, 슬리피지 추적)
4. 핵심 기술 사양(WebSocket, Rate Limit, NTP, State Hydration, 서킷브레이커, 배포)

이 스펙은 **위 4개 축을 관통하는 기반**(아키텍처 결정 + 모듈 구조 + DB 스키마 + 트레이딩
엔진 핵심 흐름 + 단계별 로드맵)을 확정한다. 실제 구현 코드는 이 스펙이 승인된 뒤 별도
플랜(1단계: 트레이딩 엔진 + 핵심 안전장치)에서 작성한다 — 이 저장소의 기존 관례(스펙 →
플랜 → subagent-driven-development)를 그대로 따른다.

## 범위

**이 스펙에서 확정하는 것:**
- 핵심 아키텍처 결정 8개(아래 참고)
- 전체 모듈/폴더 구조
- DB 스키마(7개 테이블)
- 트레이딩 엔진 핵심 흐름(신호평가/주문실행/리스크관리/장애복구)
- 0~4단계 로드맵과 각 단계의 범위
- 추가로 고려해야 할 항목

**이 스펙에서 다루지 않는 것(후속 단계·플랜에서):**
- 실제 구현 코드(Python 모듈 상세 구현) — 1단계 플랜에서
- 텔레그램 봇 상세 설계(대화형 명령어 목록, 알림 레벨 등) — 2단계 스펙에서
- 분석 대시보드 UI/API 상세 설계 — 3단계 스펙에서
- PM2/Docker 배포 스크립트, 업비트 API Open IP 등록 절차 상세 — 4단계 스펙에서

## 핵심 아키텍처 결정

브레인스토밍 과정에서 사용자와 함께 확정한 8개 결정(결정 6·7·8은 2026-08-06 개정). 각각
"왜"를 남겨서 나중에 재논의할 필요가 없게 한다.

### 결정 1 — backtrader를 라이브 엔진에서 완전히 배제한다

기존 백테스트 엔진(`engine/condition_strategy.py`, `engine/indicators/*.py`)은 지표
계산부터 신호 평가까지 backtrader(`bt.Indicator`, `Cerebro`)에 강하게 결합돼 있다. 이
저장소에는 이미 기록된 이슈로 "backtrader `Cerebro()`가 반복 호출 시 메모리를 누수한다"는
게 있다([[upbit-v1-runner-memory-leak]], 그리드서치 9-오실레이터 그리드에서 실제로 겪음).
24/7 무중단 라이브 프로세스에 이걸 그대로 물리는 건 운영 리스크가 크다.

**결정:** 라이브 엔진은 backtrader를 전혀 쓰지 않는다. 대신:
- `engine/condition_tree.py`의 `eval_group()`(AND/OR 재귀 평가 + `apply_operator()` 연산자
  비교)은 **이미 backtrader에 종속되지 않은 순수 함수**다 — 유일하게 종속된 부분은
  `get_indicator_value(name, obj: bt.Indicator)`(지표 종류별로 `bt.Indicator`의 라인
  버퍼에서 float를 뽑아내는 부분)뿐이다.
- 기존 `eval_group()`은 **수정하지 않고 그대로 백테스트에 남긴다**(회귀 위험 0).
- `engine/condition_tree.py`에 **새 함수** `eval_group_values(group, values: dict[str,
  float | None], position_return_pct=None, position_holding_bars=None)`를 추가한다 —
  로직은 `eval_group()`과 동일하되, 이미 계산된 float 딕셔너리(`values[key]`)를 직접
  읽는다. `values[key]`가 `None`(지표 계산 불가)이면 그 리프는 "unknown"으로 취급되어
  AND/OR 평가에서 제외된다(unknown 처리 상세는 결정 8). 순수 추가이므로 기존 코드에
  영향 없음.
- `trading/live_indicators.py`(신규)가 pandas rolling 계산으로 `{indicator_key: float}`
  딕셔너리를 만들어 `eval_group_values()`에 넘긴다.
- **전략 설정 포맷(ConditionGroup/ConditionBlock JSON)은 백테스트와 라이브가 100% 동일** —
  백테스트로 찾은 전략을 그대로 라이브로 승격할 수 있는 이유가 이것이다.

**후속 검증 필요:** 지표 계산 로직이 두 곳(backtrader vs pandas)에 따로 존재하므로, 같은
파라미터로 같은 값이 나오는지 검증하는 **골든테스트**가 1단계 플랜에 필수로 포함돼야
한다(예: 같은 과거 캔들 구간에 대해 backtrader RSI(14)와 pandas RSI(14)가 부동소수점
오차 범위 내에서 일치하는지).

### 결정 2 — `INDICATOR_FACTORY` 39개 지표를 전부 포팅하되, 두 그룹으로 나눠 인프라를 다르게 둔다 (2026-08-06 개정, 2026-08-07 개수 정정)

승격 대상 전략을 코인·봉타입마다 계속 새로 추가할 것이므로(결정 6), 매번 "이번엔 어떤
지표가 필요한지" 확인하며 하나씩 포팅하는 대신 **매수/매도 조건에 쓸 수 있는 지표 전체를
1단계에 포팅한다**(사용자 확정 — 원래는 승격 전략이 실제 쓰는 것만 골라 포팅하려 했으나,
전략이 여러 개·계속 늘어난다는 게 확정되면서 범위를 넓힘). `INDICATOR_FACTORY` 39개는
필요한 실시간 데이터 소스에 따라 두 그룹으로 나뉜다:

- **A그룹(33개, 대상 마켓 OHLCV만으로 계산)** — `SMA/EMA/WMA/RSI/MACD류/STOCH류/CCI/
  WILLIAMS_R/BB류/ATR류/OBV/VOLUME_SMA/MOMENTUM_PCT/PIVOT류/FIB류/VPVR류/VPIN/
  TRADE_VALUE류`. 데몬이 어차피 구독하는 그 마켓의 실시간 캔들만 있으면 계산 가능 —
  추가 인프라 없이 1단계에 전부 포팅한다.
- **B그룹(6개, 외부데이터·보조마켓 의존)** — `FUNDING_RATE`(바이낸스 펀딩비),
  `FEAR_GREED_CMC`(CMC API), `KOREA_PREMIUM`(USD/KRW 환율+바이낸스 가격),
  `MARKET_TREND`/`BTC_CORRELATION`/`USDT_CORRELATION`(보조 마켓 캔들). 각각 별도의
  실시간 수집 파이프라인이 필요하고, 그만큼 장애 가능성도 늘어난다 — 지연/실패 시
  처리 정책은 결정 8 참고.

### 결정 3 — 프로세스 토폴로지: FastAPI와 완전히 분리된 상주 데몬

**결정:** `trading/daemon.py`는 `backend/main.py`(FastAPI)와 별도 프로세스로, PM2 또는
Docker로 24/7 상시 구동한다. FastAPI는 트레이딩 DB(`trading.db`)를 **읽어서** 상태를
보여주고, "일시정지/재개/승인/청산" 같은 제어 명령을 같은 DB에 **써넣기만** 한다 — 데몬이
다음 루프에서 그 명령을 읽어 반영한다. 대시보드가 재시작/재배포돼도 매매는 끊기지 않는다.

이 패턴은 그리드서치의 `_active` 단일 실행 락과 원칙은 같지만(항상 최대 1개 실행),
직접적인 프로세스 통신 대신 DB를 매개로 한다는 점이 다르다 — 데몬이 FastAPI 프로세스의
생사와 완전히 독립적이어야 하기 때문.

### 결정 4 — DB는 기존과 동일하게 SQLite (신규 파일 `trading.db`로 분리)

**결정:** 새 의존성(Postgres 등) 추가 없이 기존 `engine/cache.py`와 동일한 SQLite 패턴을
쓴다. 이 세션에서 실제로 SQLite 동시성 버그(공유 JSON 컬럼 read-modify-write lost-update)를
`BEGIN IMMEDIATE`로 직접 고친 경험이 있어([[upbit-v1-grid-search-result-delete]] 참고),
올바르게 다루면 SQLite로도 충분히 안전하다는 걸 확인했다. 데몬 프로세스가 트레이딩 관련
테이블의 유일한 쓰기 주체이고 FastAPI는 대부분 읽기 + 드문 제어 명령 쓰기만 하므로, 쓰기
경합 자체가 적다.

캐시용 `results.db`와는 별도 파일(`trading.db`)로 분리한다 — 실거래 데이터는 백테스트
캐시 데이터와 생명주기·백업 정책이 달라야 한다.

### 결정 5 — 모의투자(paper trading) 모드 없이 처음부터 실주문 지원

**결정(사용자):** 별도 모의투자 모드 없이 처음부터 실제 주문을 지원한다. 이 결정 때문에
State Hydration/Reconciler(수동개입 감지)/서킷브레이커는 "나중에 추가할 인프라"가 아니라
**1단계에 반드시 포함되는 최소 안전장치**로 재분류한다(로드맵 참고).

**보완 조치:** 사용자 대상 "모의투자 모드"는 없지만, `order_executor` 내부에는 테스트
편의를 위한 `dry_run` 플래그를 옵션으로 둔다(유닛/통합테스트 전용, 실제 사용자 플로우에는
노출하지 않음). 또한 실전 투입 전 **최소주문금액 규모의 소액 실전 테스트**를 1단계 플랜의
필수 검증 단계로 포함한다(사용자는 우선 10만원 규모로 검증할 계획).

### 결정 6 — 여러 라이브 전략을 동시에 실행하되, 코인(market)당 최대 1개로 제한한다 (2026-08-06 개정, 봉타입 무관하게 재정정)

그리드서치는 코인·봉타입 조합당 "최적 전략 1개"를 찾는 탐색 과정일 뿐이고, 실매매에서는
그렇게 검증된 좋은 전략을 여러 개 **병행 운용**해야 한다(예: BTC 전략과 ETH 전략을 동시에
돌림). 원래 결정("1단계 MVP는 단일 전략만 동시 실행")을 폐기하고 N개 동시실행을 1단계
핵심 요구사항으로 승격한다.

단, **같은 코인에는 봉타입과 무관하게 항상 최대 1개 전략만 running이다**(사용자 확정 —
처음엔 "같은 (market, timeframe) 조합당 1개"까지만 생각했으나, BTC-1시간봉 전략과
BTC-1일봉 전략을 동시에 돌리면 포지션/자금이 꼬일 위험이 커서 봉타입 관계없이 **코인
단위로** 1개만 허용하는 것으로 정정). 서로 다른 코인끼리는 여러 개가 동시에 running 가능.

하나의 전략 안에서 매수/매도 조건이 지표 여러 개(RSI/WILLIAMS_R/CCI 등)를 AND/OR로 조합하는
것은 원래도 ConditionGroup/ConditionBlock JSON이 지원하던 것(결정 1)이라 별도 설계가
필요 없다.

**영향:**
- `live_strategies.status='running'` 제약을 "테이블 전체 최대 1행"에서 **"market(코인)당
  최대 1행"**으로 변경한다(승인 시 애플리케이션 레벨에서 검사, timeframe은 무관).
- 데몬은 서로 다른 코인의 전략들을 asyncio 태스크로 동시에 관리한다. 코인 하나 = 전략 하나
  = 포지션/자금이 1:1로 대응하므로, 여러 전략이 같은 코인 잔고를 나눠 쓰는 경우 자체가
  없다 — Reconciler도 전략별로 독립적으로 "그 전략의 기대 포지션"과 실제 거래소의 해당
  코인 잔고를 1:1로 대조하면 된다(여러 전략의 포지션을 합산할 필요 없음).
- 시세 WebSocket 구독도 코인당 하나(해당 전략의 timeframe에 맞는 캔들 스트림)로 단순하다.
- **서킷브레이커/일일성과는 전략 단위로 독립 적용한다**(사용자 확정) — 전략 하나가 손실
  한도를 넘어 트립돼도 그 전략만 정지되고 나머지 running 전략은 영향받지 않는다. 이를 위해
  `circuit_breaker_state`/`daily_performance`에 `live_strategy_id`를 추가한다(DB 스키마 절
  참고). 코인당 전략이 1개뿐이므로 이 스코프는 "코인별 서킷브레이커"와도 동일하다.

### 결정 7 — 전략별 자금은 최초 투입 후 자체적으로 복리(compounding)된다 (2026-08-06 개정)

**결정(사용자, 세부 조정 가능):** 전략의 최초 진입 자금만 `risk_config_json.position_sizing_mode`
(고정금액 또는 계좌잔고의 n%)로 **승인 시점에 1회** 확정한다. 이후 1차 매수→매도가 끝나면,
그 매도 실현금액(수수료 차감 후)이 그대로 다음(2차) 진입 자금이 된다 — 매번 "계좌 전체
잔고의 n%"를 다시 계산하지 않는다.

**영향:**
- `live_strategies`에 `current_capital` 컬럼을 추가한다 — 승인 시 최초 세팅, 포지션 종료
  (매도 체결)마다 실현금액으로 갱신(DB 스키마 절 참고).
- `max_position_per_market`/`max_total_position`은 sizing 방식이 아니라, 복리로 자금이
  과도하게 불어나는 걸 막는 **안전 상한**으로 역할이 바뀐다.
- 전략별로 자금이 격리되므로, 여러 전략이 동시에 잔고를 재조회하며 경쟁하는 문제가 애초에
  생기지 않는다. 유일하게 확인이 필요한 시점은 **전략을 새로 승인할 때** — "이미 running
  중인 다른 전략들의 최초/현재 `current_capital` 합 + 신규 전략의 최초 자금 ≤ 실제 가용
  KRW 잔고"를 승인 단계에서 검증한다.

### 결정 8 — B그룹 지표가 지연/실패하면 해당 조건만 스킵하고, 조건 전체가 B그룹뿐이면 그 전략을 일시정지한다 (2026-08-06 개정)

결정 2에서 B그룹(외부데이터·보조마켓 의존 6개) 지표도 전부 포팅하기로 했으므로, 그 실시간
수집 파이프라인이 지연되거나 실패했을 때의 동작을 명확히 정의해야 한다.

**결정(사용자):**
- B그룹 지표 하나의 실시간 데이터가 지연/실패하면, `live_indicators.py`는 그 값을 오래된
  값으로 forward-fill하지 않고 **`None`(unknown)을 반환**한다(백테스트 FUNDING_RATE에서
  이미 겪은 stale-forward-fill 버그를 라이브에서 반복하지 않기 위함,
  [[upbit-v1-funding-rate-indicator]]).
- `eval_group_values()`(결정 1)는 리프의 지표값이 `None`이면 그 리프를 AND/OR 평가에서
  **제외**한다 — 예: `(RSI<31 and FUNDING_RATE<-0.01)`에서 FUNDING_RATE가 unknown이면
  `RSI<31`만으로 판단(나머지 조건으로 매매를 이어간다). 한 그룹의 자식이 전부 unknown이면
  그 그룹도 unknown으로 전파된다.
- **최상위 매수/매도 조건 그룹 전체가 unknown으로 평가되면**(= 그 전략이 전부 지연된
  B그룹 지표에만 의존) 그 후보를 `signals`에 "판단 불가"로 기록하고, **그 전략만**
  `live_strategies.status='paused'`로 전환한다(결정 6에 따라 전략은 서로 독립이므로 다른
  전략에는 영향 없음).
- 이렇게 일시정지된 경우는 손실 트립(서킷브레이커)과 성격이 달라 — 외부 API가 복구되어
  다음 성공적 fetch가 이뤄지면 **자동으로 `status='running'`으로 재개**한다(서킷브레이커는
  수동 재승인이 필요하지만, 이건 손실이 아니라 순수 데이터 가용성 문제이기 때문).
- A그룹만으로 구성된 전략은 이 정책의 영향을 받지 않는다(대상 마켓 캔들만 있으면 항상
  계산 가능).

## 모듈 구조

```
260711-upbit-v1/
├── engine/                      # 기존 (백테스트, backtrader 기반) — 변경 없음
│   ├── condition_tree.py        # eval_group_values() 추가(순수 추가, 기존 함수 불변)
│   └── ...
├── trading/                     # 신규 — 라이브 트레이딩 엔진 (backtrader 미사용)
│   ├── daemon.py                # 상주 프로세스 진입점(메인 루프, 백그라운드 태스크 기동)
│   ├── upbit_client.py          # REST 인증(JWT) 클라이언트 + Throttle/Queue
│   ├── upbit_ws.py              # WebSocket 구독(공개: 캔들/체결)
│   ├── live_indicators.py       # pandas 기반 지표 계산(A그룹 30개 + B그룹 6개 전부, B그룹은 지연/실패 시 None 반환)
│   ├── signal_engine.py         # live_indicators + condition_tree.eval_group_values 결합
│   ├── order_executor.py        # 시장가/지정가/지정가+타임아웃 하이브리드 주문 실행
│   ├── position_manager.py      # 포지션 추적, 전략별 자금관리(최초 고정금액/퍼센트 → 이후 복리), 최대진입금액 안전상한
│   ├── risk_manager.py          # 서킷브레이커(일일손실/연속손실 판정 및 트립)
│   ├── reconciler.py            # 외부 수동주문 감지(거래소 상태 vs 내부 DB 대조), 재시작 시 State Hydration에도 사용
│   ├── telegram_bot.py          # 알림 + 대화형 제어 (2단계에서 구현, 1단계에서는 빈 스텁 또는 미포함)
│   └── db.py                    # trading.db 스키마 정의/접근 (engine/cache.py와 같은 패턴, 별도 파일)
├── backend/main.py               # 기존 FastAPI — 라이브 전략 관리 API 추가(제어/조회만, 실행 로직 없음)
└── frontend/                     # 기존 Next.js — "라이브 전략 관리" 페이지 추가
```

**핵심 원칙:** `trading/` 패키지는 `engine/condition_tree.py`(전략 설정 포맷과 조건 평가
로직만) 외에는 `engine/`의 backtrader 관련 코드를 import하지 않는다. `backend/main.py`는
`trading/`의 실행 로직을 직접 호출하지 않고, `trading/db.py`가 정의한 스키마를 통해서만
간접적으로 상호작용한다(같은 DB 파일을 다른 프로세스가 각자 읽고 쓴다).

## DB 스키마

새 SQLite DB 파일 `trading.db`(기존 `results.db`와 분리)에 7개 테이블. 기존 코드베이스
관례(JSON은 TEXT 컬럼에 블롭으로, `datetime('now')` 타임스탬프, uuid4 hex 문자열 PK)를
그대로 따른다.

### 1) `live_strategies` — 승인된 라이브 전략

```sql
CREATE TABLE live_strategies (
    id                  TEXT PRIMARY KEY,
    source_run_id       TEXT,                     -- 어느 백테스트에서 승격됐는지 (backtest_runs.id, nullable)
    market              TEXT NOT NULL,             -- 'KRW-BTC'
    timeframe           TEXT NOT NULL,
    buy_conditions_json  TEXT NOT NULL,            -- ConditionGroup JSON, 백테스트와 100% 동일 포맷
    sell_conditions_json TEXT NOT NULL,
    risk_config_json    TEXT NOT NULL,             -- 아래 "risk_config_json 필드" 참고
    current_capital     REAL,                      -- 전략 자체 자금(복리): 승인 시 최초 세팅, 포지션 종료마다 실현금액으로 갱신 (결정 7)
    status              TEXT NOT NULL DEFAULT 'draft',  -- draft|approved|running|paused|stopped
    last_processed_candle_time TEXT,               -- state hydration: 재시작 시 여기부터 다시 봄
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at         TEXT,
    started_at          TEXT,
    stopped_at          TEXT
);
```

`risk_config_json` 필드 구성:
```json
{
  "position_sizing_mode": "fixed | percent",
  "position_sizing_value": 100000,
  "max_position_per_market": 500000,
  "max_total_position": 2000000,
  "order_execution_mode": "market | limit | limit_timeout",
  "order_timeout_sec": 10,
  "manual_intervention_policy": "all_stop | acknowledge_and_continue",
  "daily_loss_limit_pct": -5.0,
  "consecutive_loss_limit": 3
}
```

`status='running'`은 **market(코인)당 항상 최대 1행**만 가능(애플리케이션 레벨에서 승인
시 강제, timeframe 무관 — 같은 코인에 중복 투자 금지, 결정 6). 서로 다른 코인끼리는 여러
개가 동시에 running일 수 있다(1단계부터 지원).

### 2) `positions` — 포지션(전량 진입/전량 청산, 백테스트와 동일한 all-in/all-out 모델)

```sql
CREATE TABLE positions (
    id               TEXT PRIMARY KEY,
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
    market           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',  -- open|closed
    entry_price      REAL,  entry_qty REAL,  entry_time TEXT,
    exit_price       REAL,  exit_qty  REAL,  exit_time  TEXT,
    realized_pnl     REAL,  realized_pnl_pct REAL,
    close_reason     TEXT,  -- signal|stop_loss|take_profit|manual|circuit_breaker
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
```

기존 백테스트 전략(`ConditionTreeStrategy`)이 부분 진입/부분 청산을 지원하지 않는 것과
동일하게, 라이브도 포지션 하나는 항상 전량 진입 → 전량 청산이다(백테스트-라이브 비교의
전제를 맞추기 위함).

### 3) `orders` — 실제 주문(엔진이 낸 것 + 감지된 외부 수동주문 포함)

```sql
CREATE TABLE orders (
    id                TEXT PRIMARY KEY,
    upbit_uuid        TEXT UNIQUE,             -- 업비트 주문 UUID (외부 감지 주문도 이 값으로 식별)
    live_strategy_id  TEXT REFERENCES live_strategies(id),
    position_id       TEXT REFERENCES positions(id),
    replaces_order_id TEXT REFERENCES orders(id),  -- 지정가 타임아웃→시장가 전환 시, 취소된 주문을 가리킴
    market            TEXT NOT NULL,
    side              TEXT NOT NULL,           -- bid|ask
    order_type        TEXT NOT NULL,           -- limit|price|market
    requested_price   REAL,  requested_volume REAL,
    filled_price      REAL,  filled_volume    REAL,  fee REAL,
    expected_price    REAL,  slippage_pct     REAL,  -- 신호 시점 기대가 대비 실제 체결가 괴리율
    status            TEXT NOT NULL,           -- wait|done|cancel|failed
    is_external       INTEGER NOT NULL DEFAULT 0,  -- 1이면 사용자가 앱 등에서 수동으로 넣은 주문
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
);
```

### 4) `signals` — 신호 판단 로그(주문 여부와 무관하게 전부 기록, 오차 분석용)

```sql
CREATE TABLE signals (
    id                  TEXT PRIMARY KEY,
    live_strategy_id    TEXT NOT NULL REFERENCES live_strategies(id),
    signal_type         TEXT NOT NULL,   -- buy|sell
    candle_time         TEXT NOT NULL,
    indicator_snapshot_json TEXT,        -- 판단 당시 지표값들 (디버깅/백테스트 대조용)
    resulting_order_id  TEXT REFERENCES orders(id),  -- 주문으로 안 이어졌으면 NULL
    skip_reason         TEXT,            -- circuit_breaker_tripped|max_position_reached|manual_pause 등
    triggered_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 5) `daily_performance` — 전략별 일별 집계(서킷브레이커 판단에 사용, 결정 6 개정)

```sql
CREATE TABLE daily_performance (
    trading_date     TEXT NOT NULL,      -- KST 기준 'YYYY-MM-DD'
    live_strategy_id TEXT NOT NULL REFERENCES live_strategies(id),
    realized_pnl     REAL NOT NULL DEFAULT 0,
    realized_pnl_pct REAL NOT NULL DEFAULT 0,
    trade_count      INTEGER NOT NULL DEFAULT 0,
    win_count        INTEGER NOT NULL DEFAULT 0,
    loss_count       INTEGER NOT NULL DEFAULT 0,
    starting_balance REAL,  ending_balance REAL,
    max_drawdown_pct REAL,
    PRIMARY KEY (trading_date, live_strategy_id)
);
```

전략별 서킷브레이커 판단은 이 테이블을 `live_strategy_id`로 필터링해서 쓴다. 대시보드의
"계좌 전체" 일별 합산 뷰는 별도 테이블 없이 조회 시점에 `GROUP BY trading_date`로 계산한다
(결정 4의 "중복 저장 안 함" 원칙과 동일).

### 6) `circuit_breaker_state` — 전략별 상태 행 (싱글턴 아님, 결정 6 개정)

```sql
CREATE TABLE circuit_breaker_state (
    live_strategy_id   TEXT PRIMARY KEY REFERENCES live_strategies(id),
    trading_date       TEXT NOT NULL,     -- KST 'YYYY-MM-DD', 날짜 바뀌면 리셋
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    tripped            INTEGER NOT NULL DEFAULT 0,
    tripped_reason     TEXT,
    tripped_at         TEXT,
    resumed_at         TEXT
);
```

전략 하나가 손실 한도를 넘어 트립돼도 그 전략만 `live_strategies.status='paused'`로
멈추고, 다른 running 전략은 영향받지 않는다(사용자 확정: 서킷브레이커는 전략 단위 독립).

### 7) `manual_intervention_events` — 외부 수동개입 감지 로그

```sql
CREATE TABLE manual_intervention_events (
    id             TEXT PRIMARY KEY,
    detected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    market         TEXT,
    description    TEXT NOT NULL,
    action_taken   TEXT NOT NULL,   -- all_stop|acknowledged_and_continued
    resolved_at    TEXT
);
```

**백테스트 vs 실매매 비교(3단계)는 별도 테이블을 두지 않는다** — `positions`/`orders`를
`live_strategies.source_run_id`로 기존 `backtest_runs`/`backtest_results`와 조인해서 조회
시점에 계산한다(백테스트 데이터가 이미 있으니 중복 저장하지 않는다).

## 트레이딩 엔진 핵심 흐름

### "다음 봉 시가" 진입의 실전 의미

백테스트에서 "다음 봉 시가"는 과거 데이터의 특정 값이지만, 라이브에서는 그런 고정된
지점이 없다 — 연속적으로 거래되는 실시간 시장에서는 "현재 봉이 막 닫히고 다음 봉이 열리는
순간의 시가"가 곧 "신호 확정 직후의 현재 시세"와 같다. 즉, 라이브에서 "다음 봉 시가
진입"은 실무적으로 **"봉 마감 신호 확정 즉시 주문 실행"**으로 구현한다(지연 없이 즉시
실행하면 자연히 다음 봉의 시가 근처에서 체결된다).

### 메인 루프

```
[daemon.py 시작]
  1. live_strategies에서 status='running'인 행 전부 로드(N개, market(코인)당 최대 1개)
  2. 전략마다 독립적으로 State Hydration
     - positions(status='open', live_strategy_id=해당전략) 로드 → 현재 보유 포지션 파악
     - orders(status='wait', live_strategy_id=해당전략) 로드 → 미체결 주문 파악
     - 즉시 Upbit REST로 그 코인의 실제 잔고/미체결주문 조회 → 그 전략 하나의 내부 상태와
       1:1 대조(코인당 전략이 1개뿐이므로 여러 전략 합산 불필요, 재시작 복구 + 수동개입
       감지를 같은 로직으로 처리)
  3. 전략마다 시세 WebSocket 구독 시작(해당 market의 캔들/체결 스트림)
  4. 전략별 [캔들 처리] 태스크를 asyncio로 동시 구동 + 공통 백그라운드 루프 3개
     ([주문상태 감시] [Reconciler] [전략별 서킷브레이커 체크])
```

**[캔들 처리]** (해당 timeframe의 캔들이 마감될 때마다)
```
캔들 마감 → live_indicators로 지표 계산 → eval_group_values()로 매수/매도 조건 평가
  → signals 테이블에 무조건 기록(주문 여부 무관, 오차 분석용)
  → 포지션 없고 매수신호 True:
       risk_manager: 서킷브레이커 안 걸림? 최대진입금액 한도 안에? → 통과 시 order_executor.enter()
       걸림/한도초과 → signals.skip_reason 기록, 주문 안 냄
  → 포지션 있고 매도신호 True → order_executor.exit()
```

**리스크 청산(손절/익절)은 캔들 마감을 기다리지 않는다** — `STOP_LOSS_PCT`/
`TAKE_PROFIT_PCT`는 실시간 체결(ticker) 스트림마다 별도로 평가한다. 봉 하나가 다 닫히기
전에 손절선이 뚫릴 수 있어서, 이 부분만 캔들 주기와 분리해 더 빠르게 반응한다(신호 조건
자체는 여전히 캔들 마감 기준으로 백테스트와 동일하게 유지 — parity를 지키기 위함).

**[주문 실행 — `order_executor.py`]**
```
enter(side, amount):
  position_manager가 해당 전략의 live_strategies.current_capital(최초 진입은 승인 시
    확정된 고정금액/퍼센트, 이후는 직전 매도 실현금액 — 결정 7)을 기준으로 최종 주문금액을
    산출하고, max_position_per_market/max_total_position은 안전 상한으로만 적용
  주문가는 해당 마켓의 틱사이즈에 맞춰 라운딩(orders/chance API로 사전 확인)
  execution_mode에 따라:
    market  → 즉시 시장가 주문
    limit   → 지정가 주문, 이후 미체결 방치(사용자가 명시적으로 이 모드 선택했을 때만)
    limit_timeout(기본) → 지정가 주문 → N초 타이머 시작
        N초 내 체결 → 완료
        N초 내 미체결(전량 or 부분) → 미체결분 취소 요청 → 남은 수량만 시장가로 재주문
          (orders.replaces_order_id로 연결) → 체결분+시장가 체결분의 평균 매입가 재계산
  주문 API 응답을 못 받은 경우, 바로 재시도하지 않고 먼저 미체결/체결 내역을 재조회해서
  실제로 주문이 안 들어갔는지 확인 후에만 재시도(이중 주문 방지, idempotency)
  모든 주문에 expected_price(신호 확정 시점 가격) 기록 → 체결 후 slippage_pct 계산

exit(...) 체결 완료 시: 실현금액(수수료 차감 후)을 해당 live_strategies.current_capital에
  반영한다 — 이 값이 그대로 다음(N+1차) 진입 자금이 된다(결정 7, 복리).
```

**[주문상태 감시]** — `wait` 상태 주문을 Throttle 큐를 통해 폴링하며 체결/취소 여부 갱신.
`limit_timeout` 모드의 타이머도 여기서 관리.

**[Reconciler — 수동개입 감지 + 재시작 복구]** — 주기적으로(예: 15~30초) REST로 실제
미체결주문/잔고를 조회해 내부 DB와 대조. 엔진이 만들지 않은 주문(`upbit_uuid`가 내부에
없음)이나 설명되지 않는 잔고 변화가 보이면 `manual_intervention_events`에 기록하고,
`risk_config_json.manual_intervention_policy`에 따라 전체 정지(`all_stop`) 또는 인지 후
계속(`acknowledge_and_continue`).

**[서킷브레이커]** — 포지션이 청산될 때마다 `daily_performance`/`circuit_breaker_state`
갱신 → 일일 손실률(`daily_loss_limit_pct`) 또는 연속손실 횟수(`consecutive_loss_limit`)
초과 시 `tripped=1` + `live_strategies.status='paused'`(+2단계에서 텔레그램 긴급 알림).
`trading_date`(KST 'YYYY-MM-DD')가 바뀌면 `consecutive_losses`/`tripped`를 리셋한다.

### 인프라 세부사항

- **Rate Limit:** REST 호출은 전부 하나의 async 큐를 통과하며 엔드포인트 그룹별(주문/
  계좌/시세) 토큰버킷으로 스로틀 — 초당/분당 한도를 넘지 않게 한다.
- **NTP:** 서버 OS 레벨 시간동기화(Linux `chronyd`/`systemd-timesyncd`)를 1차 방어선으로
  안내하고, 데몬 시작 시 + 주기적으로 로컬시간과 업비트 서버시간의 오차를 자체 점검해
  임계치(예: 500ms) 초과 시 로그/알림 — JWT 서명 인증 실패를 예방.
- **WebSocket 재연결:** 끊겼다가 재연결되면 그 사이 누락된 캔들을 REST로 보충(gap fill).

## 실매매 승격 UX 흐름

1. `/backtests/{run_id}` 상세 페이지에 "이 전략으로 실매매 시작" 버튼 추가.
2. 클릭 시 새 "라이브 전략 관리" 페이지로 이동, 해당 백테스트의 `buy_conditions`/
   `sell_conditions`/`market`/`timeframe`이 미리 채워진 채로 표시.
3. 사용자가 자금관리(고정금액/퍼센트, 최대한도), 주문실행 모드(시장가/지정가/지정가+
   타임아웃), 서킷브레이커 한도, 수동개입 정책을 확정 → `live_strategies` 행 생성
   (`status='draft'`).
4. 최종 확인(승인) → `status='approved'` → 데몬이 다음 루프에서 감지해 `status='running'`
   으로 전환하고 실제 매매 시작.
5. "라이브 전략 관리" 페이지에서 언제든 일시정지/재개/중지 가능(제어 명령을 DB에 기록,
   데몬이 반영).

## 로드맵

애초 사용자가 제시한 4개 축(엔진/모니터링/분석/인프라)보다 **"실거래 안전성" 기준으로
재구성**한다 — 모의투자 없이 처음부터 실주문을 쓰기로 했으므로, 서킷브레이커/수동개입
감지/상태복구는 "나중에 추가할 인프라"가 아니라 1단계에 반드시 포함되는 최소 안전장치다.

| 단계 | 범위 | 결과물 |
|---|---|---|
| 0(이 스펙) | 아키텍처/모듈구조/DB스키마 확정 | 이 문서 |
| **1. 트레이딩 엔진 + 핵심 안전장치** | Upbit REST/WS 클라이언트, 신호평가(pandas), 주문실행 3모드, 전략별 복리 자금관리, State Hydration, Reconciler, 전략별 서킷브레이커, 실매매 승격 UX | 승인된 전략 여러 개(코인당 1개)가 실제 자금으로 안전하게 동시 자동매매되는 최소 시스템 |
| 2. 텔레그램 제어/알림 | 신호/체결/청산 알림, 대화형 제어(정지/재개/청산/잔고조회) | 휴대폰으로 모니터링·개입 가능 |
| 3. 분석 대시보드 | 백테스트 vs 실매매 대조, 슬리피지 추적, Daily/누적/MDD/승률 | 매매일지 화면 |
| 4. 운영 하드닝 | PM2/Docker 배포, 업비트 API Open IP 등록 가이드, 지표 커버리지 확장, 부하/장기구동 테스트 | 24/7 운영 준비 완료 |

1단계가 가장 크고 중요하므로, 이 스펙 승인 후 **1단계 전용 상세 플랜(태스크 분해 + 실제
구현 코드)**을 별도로 작성한다.

## 추가로 고려해야 할 항목

**보안**
- Upbit API 키에서 **출금(Withdraw) 권한은 절대 켜지 않는다** — 주문/조회 권한만 발급.
  키 유출 시에도 자금이 빠져나갈 수 없게 하는 최소권한 원칙.
- API 키는 서버 환경변수로만 보관, 코드/저장소에 절대 커밋하지 않음(`.env` +
  `.gitignore`).

**주문 정확성**
- 코인별 최소주문금액/가격 틱사이즈를 `orders/chance` API로 확인 후 주문가를 틱사이즈에
  맞게 라운딩(안 하면 주문 자체가 거부됨).
- 부분체결 처리와 평균 매입가 재계산(위 주문 실행 흐름에 반영됨).
- 주문 API 응답 실패 시 재조회 후에만 재시도(이중 주문 방지, 위에 반영됨).

**장애 복구**
- WebSocket 재연결 시 gap fill(위에 반영됨).
- 업비트 정기 점검/API 장애 시 자동매매 일시정지 + 알림(무작정 재시도 반복 방지) —
  1단계 플랜에서 구체적인 재시도 백오프 정책으로 구체화.

**테스트/검증**
- 지표 골든테스트(backtrader vs pandas 지표값 일치 검증) — 1단계 플랜 필수 포함.
- Upbit API를 모킹한 유닛테스트로 주문실행/자금관리/서킷브레이커 로직 검증.
- 실거래 연동은 최소주문금액 규모의 소액 실전 테스트를 먼저 거친 뒤 계획된 금액 투입.

**운영**
- 전략 변경 시 안전한 전환: 실행 중인 전략의 조건/리스크설정을 수정하려면 정지 → 열린
  포지션 확인 → 변경 → 재시작을 강제(열린 포지션이 있는 채로 즉시 변경 금지).
- 알림 피로도 관리: 이벤트 중요도(긴급/일반) 구분 — 2단계 텔레그램 스펙에서 구체화.

## 자기 검토(스펙 완성도)

- 플레이스홀더/TBD 없음 — 각 결정 항목에 "왜"와 "어떻게"를 남겼다.
- 8개 핵심 아키텍처 결정이 서로 상충하지 않는지 확인: backtrader 배제(결정1) ↔ 모듈구조의
  `trading/`이 `engine/condition_tree.py`만 재사용(모듈구조 절) ↔ DB 스키마의
  `buy_conditions_json`이 그 재사용을 전제로 함 — 세 곳이 일관됨.
- 모의투자 없이 실주문(결정5)이라는 사용자 결정과, 로드맵에서 서킷브레이커/Reconciler/
  State Hydration을 1단계로 끌어올린 재구성 사이의 인과관계를 명시했다(왜 재구성했는지
  설명 없이 그냥 다르게 배치하면 나중에 "왜 순서가 바뀌었지"라는 혼란이 생김).
- 스코프가 매우 크다는 걸 인지하고, "이 스펙에서 다루지 않는 것"을 명시적으로 나열해
  범위를 좁혔다 — 텔레그램 상세/대시보드 UI 상세/배포 스크립트 상세/정확한 지표 구현
  범위는 각각 후속 스펙·플랜에서 다룬다.
- 1단계가 압도적으로 크다는 점을 인지하고, 이 스펙 자체에서 1단계의 구현 코드까지
  쓰지 않고 "승인 후 별도 플랜"으로 명확히 넘겼다 — 스펙과 플랜의 책임을 혼동하지 않음.
