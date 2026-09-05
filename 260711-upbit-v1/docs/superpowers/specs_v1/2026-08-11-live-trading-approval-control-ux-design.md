# 라이브 전략 승인/제어 UX — 설계 스펙 (⑥, 2026-08-11)

## 배경 및 목표

로드맵([[upbit-v1-live-trading-roadmap-sequencing]]) 1단계 완결의 마지막 서브플랜(⑥).
`⑤-4c` 백로그와 운영 가시성/데이터 무결성 항목은 이미 완료됐고, 이제 "승인된 전략 여러
개가 실제 자금으로 안전하게 동시 자동매매되는 최소 시스템"(원본 스펙
`2026-08-04-live-trading-foundation-design.md`의 1단계 목표)을 완성하려면 DB에 직접
SQL을 치지 않고도 전략을 승인/제어할 수 있는 API + 프론트엔드가 필요하다.

`trading/daemon.py`(`_task_set_manager_loop` → `db.list_active_strategies()`)는
`live_strategies.status IN ('running', 'paused')`만 읽는다는 계약을 이미 갖고 있다. 이
스펙이 정의하는 API가 그 컬럼에 값을 쓰기만 하면, daemon 쪽 코드는 전혀 건드리지 않고도
승인/일시정지/재개/중지가 그대로 반영된다.

**설계 중 발견한 스펙-코드 괴리:** 원본 스펙은 "승인 → `status='approved'` → 데몬이
다음 루프에서 감지해 `status='running'`으로 전환"이라 적었지만, 실제 구현된
`list_active_strategies()`는 `approved`를 전혀 보지 않는다(`running`/`paused`만
조회). `approved→running` 자동전환 코드는 daemon 어디에도 없다. 이 스펙은 원본 스펙의
문서상 오류로 보고, **`approved` 중간 상태를 두지 않는다** — "승인" 액션이 draft→running을
한 번에 수행한다(아래 결정 참고).

## 범위

**포함:**
- 백테스트 상세 페이지 → 라이브 전략 draft 생성 → 승인(자금검증 포함) → 일시정지/재개/중지
  전체 흐름의 API + 프론트엔드
- 라이브 전략 관리 페이지: 전략 카드 목록(상태 + 현재 포지션 요약 + 컨트롤 버튼)

**제외(후속 작업으로 이월):**
- 실행 중 전략의 조건/리스크설정 수정 플로우(원본 스펙 "운영" 절) — 지금은 정지 후
  백테스트 상세페이지에서 새 draft를 만드는 것으로 충분
- 주문/체결/신호 상세 로그 테이블 뷰 — 3단계(분석 대시보드) 범위
- 텔레그램 알림/제어 — 2단계 범위

## 확정된 설계 결정

### 결정 1 — draft → running 1단계 승인 (approved 중간상태 폐기)

"승인" 버튼이 `status='draft'`인 행을 자금검증 통과 시 바로 `status='running'`으로
전환한다. `approved_at` 컬럼은 승인 시각 기록용으로만 남기고(감사 목적),
`status='approved'`라는 값 자체는 이제 실제로 쓰이지 않는다(스키마는 유지, 애플리케이션이
안 씀).

**왜:** daemon이 approved를 인식하지 않는 이상 그 상태에 머무는 행은 영원히 실행되지
않는다. 사용자가 원한 "daemon 쪽 변경 없이 그대로 동작"을 만족시키는 유일한 방법은
API가 daemon이 이미 아는 상태(running/paused)로 직접 전환하는 것.

### 결정 2 — 승인 시 자금 검증을 이번에 구현한다 (원본 스펙 결정 7)

승인 API가 다음을 수행:
1. `upbit_client.get_accounts()`로 실제 가용 KRW 잔고 조회
2. `position_manager.calculate_initial_capital(risk_config, available_balance)`로 신규
   전략의 초기 진입 자금 계산
3. `db.list_active_strategies()`(현재 running/paused 전략들)의 `current_capital` 합
   + 신규 초기자금이 가용 잔고를 초과하면 400 반환, 승인 거부

**왜:** 소액이라도 실거래에 들어가기 전 필수 안전장치. 원본 스펙 결정 7이 이미 요구한
검증이고, 지금 구현하지 않으면 여러 전략을 동시에 승인했을 때 잔고 초과 주문이 나갈 수
있다.

**"실행 로직 미호출" 원칙과의 관계:** 원본 스펙 모듈구조 절은 "`backend/main.py`는
`trading/`의 실행 로직을 직접 호출하지 않는다"고 규정한다. `get_accounts()`(조회)와
`calculate_initial_capital()`(순수 계산)은 주문을 내지 않으므로 이 원칙의 의도(백엔드가
매매를 실행하지 못하게 함)를 위반하지 않는 예외로 취급한다 — 사용자 확인 완료.

### 결정 3 — 새 라이브 전략은 백테스트 상세 페이지에서만 생성한다

`/live-strategies` 페이지 자체에서 조건을 직접 입력해 새 전략을 만드는 경로는 두지
않는다. 원본 스펙의 "실매매 승격 UX 흐름"이 명시한 경로(`/backtests/{run_id}` → "이
전략으로 실매매 시작" 버튼) 하나로 제한한다.

**왜:** YAGNI. 조건 직접 입력 UI는 조건 트리 에디터를 통째로 새로 만들어야 해 범위가
크게 늘어나고, 원본 스펙도 요구하지 않았다.

### 결정 4 — 실행중 전략의 조건/리스크설정 수정 플로우는 이번 범위에서 제외

원본 스펙 "운영" 절의 "정지 → 열린 포지션 확인 → 변경 → 재시작" 플로우는 구현하지 않는다.
조건을 바꾸고 싶으면 기존 전략을 중지하고, 백테스트 상세 페이지에서 새 draft를 만드는
것으로 대체한다.

**왜:** draft 생성 직후(아직 running 아님)에만 수정이 필요한데, 그 시점엔 그냥 새로
제출하면 된다. running 상태에서의 인플레이스 수정은 사용/빈도가 낮고 별도 플랜으로 미뤄도
손실이 없다.

### 결정 5 — "중지"는 열린 포지션이 있으면 거부한다

`stop` API는 호출 시점에 해당 전략의 열린 포지션(`positions.status='open'`)이 있으면
400을 반환하고 상태를 바꾸지 않는다.

**왜:** 코드 확인 결과 `_run_strategy_loop`와 `_run_risk_exit_loop`는 둘 다
`status not in ('running', 'paused')`가 되면(= stopped) 즉시 태스크를 종료한다 — 자동
손절/익절/신호 매도가 전부 멈춘다. 열린 포지션을 무방비 상태로 방치하는 걸 API가 원천
차단한다. 사용자는 업비트 앱에서 직접 매도해 포지션을 정리한 뒤 중지를 재시도해야 한다.

`draft` 상태의 전략은 포지션이 있을 수 없으므로 중지(=draft 취소) 요청은 항상 즉시
통과한다.

### 결정 6 — 라이브 전략 관리 페이지의 모니터링 수준: 상태 + 현재 포지션 요약

각 전략 카드는 `market/timeframe/status/current_capital` + (열린 포지션이 있으면)
`진입가/수량/현재 손익%`을 보여준다. 주문/신호 로그 테이블은 이번 범위에서 만들지 않는다
(3단계로 이월). 손익% 계산을 위해 `upbit_data_service.get_current_prices()`로 열린
포지션이 있는 마켓들의 현재가를 배치 조회한다.

**왜:** 소액 실전 테스트 단계에서 "지금 뭘 갖고 있고 얼마 벌고/잃고 있는지"가 사용자가
실제로 필요로 하는 최소 정보. 상세 로그는 지켜보면서 필요해지면 3단계에서 다룬다.

## API 엔드포인트 (`backend/main.py`)

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/live-strategies` | draft 생성 |
| GET | `/api/v1/live-strategies` | 전체 목록(모든 status), 열린 포지션 요약 포함 |
| POST | `/api/v1/live-strategies/{id}/approve` | draft→running (자금검증 포함) |
| POST | `/api/v1/live-strategies/{id}/pause` | running→paused |
| POST | `/api/v1/live-strategies/{id}/resume` | paused→running |
| POST | `/api/v1/live-strategies/{id}/stop` | draft/running/paused→stopped (열린 포지션 있으면 거부) |

### POST `/api/v1/live-strategies` 요청/응답

요청 바디(프론트가 이미 갖고 있는 백테스트 상세 응답에서 그대로 채워 보냄 — 백엔드가
`backtest_runs`를 다시 조회하지 않음):
```json
{
  "source_run_id": "string",
  "market": "KRW-BTC",
  "timeframe": "1h",
  "buy_conditions_json": "...",
  "sell_conditions_json": "...",
  "risk_config": {
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
}
```
검증(`_validate_live_strategy_request`, 기존 `_validate_backtest_request` 패턴 재사용):
market/timeframe 유효성, risk_config 각 필드 타입/범위, buy/sell_conditions_json이
파싱 가능한 JSON인지. 실패 시 400 + 에러 목록.

응답: 생성된 행 전체(`status='draft'`).

### GET `/api/v1/live-strategies` 응답

```json
[
  {
    "id": "...", "market": "KRW-BTC", "timeframe": "1h", "status": "running",
    "current_capital": 103200.0,
    "created_at": "...", "approved_at": "...", "started_at": "...", "stopped_at": null,
    "open_position": {
      "entry_price": 62100000.0, "entry_qty": 0.00166, "entry_time": "...",
      "unrealized_pnl_pct": 1.2
    }
  }
]
```
`open_position`은 열린 포지션이 없으면 `null`. `unrealized_pnl_pct`는
`(현재가 - entry_price) / entry_price * 100`, 현재가는 열린 포지션이 있는 마켓들을 모아
`get_current_prices()` 1회 배치 호출로 조회.

### POST `/api/v1/live-strategies/{id}/approve`

- 대상이 없으면 404, `status != 'draft'`면 409("이미 승인되었거나 취소된 전략입니다" 등)
- 자금 검증 실패(결정 2) 시 400 + 부족 금액을 알 수 있는 메시지(예: "가용 잔고
  850,000원, 필요 자금 1,000,000원(기존 전략 300,000원 + 신규 700,000원)")
- 통과 시 `db.approve_live_strategy(id, current_capital)` 호출(원자적, `WHERE
  status='draft'` 가드) → rowcount 0이면 그 사이 다른 요청이 먼저 승인/취소한 것이므로
  409

### POST `/api/v1/live-strategies/{id}/pause`, `/resume`

- `db.transition_live_strategy_status(id, from_status, to_status)` 호출(`WHERE
  status=from_status` 가드) → False면 409("현재 상태에서는 수행할 수 없습니다")

### POST `/api/v1/live-strategies/{id}/stop`

- `db.stop_live_strategy_if_no_open_position(id)` 호출 → False면 400("열린 포지션이
  있어 중지할 수 없습니다. 먼저 포지션을 정리하세요")

## DB 계층 추가 (`trading/db.py`)

기존 함수 재사용: `get_live_strategy`, `get_open_position`, `list_active_strategies`.

신규 5개:
```python
def insert_live_strategy(
    source_run_id: str | None, market: str, timeframe: str,
    buy_conditions_json: str, sell_conditions_json: str, risk_config_json: str,
) -> str: ...  # status='draft' 행 삽입, id 반환

def list_live_strategies() -> list[dict]: ...  # 전체 status, created_at DESC

def approve_live_strategy(live_strategy_id: str, current_capital: float) -> bool:
    # UPDATE ... SET status='running', current_capital=?, approved_at=datetime('now'),
    #   started_at=datetime('now') WHERE id=? AND status='draft'
    # rowcount > 0 이면 True

def transition_live_strategy_status(
    live_strategy_id: str, from_status: str, to_status: str,
) -> bool:
    # UPDATE ... SET status=? WHERE id=? AND status=?  (pause/resume 공용)
    # rowcount > 0 이면 True

def stop_live_strategy_if_no_open_position(live_strategy_id: str) -> bool:
    # 단일 커넥션 내에서 get_open_position 대응 SELECT 후, 없으면
    # UPDATE ... SET status='stopped', stopped_at=datetime('now')
    # WHERE id=? AND status IN ('draft','running','paused')
    # 포지션이 있으면 UPDATE 없이 False
```

## 프론트엔드

### `frontend/app/backtests/[runId]/page.tsx`

"이 전략으로 실매매 시작" 버튼 추가 → `/live-strategies/new?source_run_id={runId}`로
이동(백테스트 detail 응답에 이미 있는 market/timeframe/buy_conditions/sell_conditions는
새 페이지가 같은 API로 다시 조회 — URL에 큰 JSON을 싣지 않기 위함).

### `frontend/app/live-strategies/new/page.tsx` (신규)

마운트 시 `GET /api/v1/backtests/{source_run_id}`로 조건/market/timeframe 재조회 →
읽기전용 미리보기로 표시. 그 아래 `risk_config` 입력 폼(자금관리 방식/값, 최대한도,
주문실행모드, 서킷브레이커 한도, 수동개입정책) — 원본 스펙 필드 그대로, 기본값은
`engine/sweep.py`의 `DEFAULT_RISK_CONFIG`가 있으면 참고, 없으면 합리적 기본값(시장가,
fixed 10만원 등)을 하드코딩. 제출 시 `POST /api/v1/live-strategies` → 성공하면
`/live-strategies`로 이동.

### `frontend/app/live-strategies/page.tsx` (신규)

`GET /api/v1/live-strategies` 폴링(그리드서치 페이지의 기존 폴링 패턴 재사용) 결과를
카드 목록으로 렌더링:
- `draft`: "승인" 버튼(클릭 시 approve 호출, 실패 메시지는 그대로 노출) + "취소"(stop)
- `running`: "일시정지" + "중지" 버튼, 열린 포지션 요약 표시
- `paused`: "재개" + "중지" 버튼, 열린 포지션 요약 표시
- `stopped`: 읽기전용, 종료 시각 표시

## 테스트

- `tests/test_trading_db.py`: 신규 db 함수 5개 — 특히 `approve_live_strategy`/
  `transition_live_strategy_status`의 `WHERE status=` 가드(잘못된 from_status로 호출 시
  rowcount 0 → False), `stop_live_strategy_if_no_open_position`이 열린 포지션 유무에
  따라 분기하는지
- `tests/test_backend.py`: API 통합테스트 — draft 생성 → 승인 성공 → 승인 실패(잔고초과,
  `upbit_client.get_accounts`를 monkeypatch로 고정 잔고 반환) → pause/resume →
  stop 성공 / stop 거부(열린 포지션 fixture 삽입) 각 케이스
- 프론트엔드: 이 저장소는 프론트엔드 자동테스트 관례가 없음 — dev 서버로 전체 플로우
  수동 확인(백테스트 상세 → draft 생성 → 승인 → 일시정지 → 재개 → 중지)

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음 — 모든 결정에 "왜"를 남겼다.
- 스펙-코드 괴리(결정 1)를 발견 즉시 명시하고 해소 방법을 확정해, 나중에 "왜 approved
  상태를 안 쓰지"라는 혼란을 막았다.
- 승인 API가 `trading/` 실행 로직에 준하는 함수를 호출하는 것(결정 2)이 원본 스펙의
  "backend는 실행 로직 미호출" 원칙과 부딪히는 지점을 명시적으로 짚고, 조회/계산이라는
  예외 근거를 남겼다 — 사용자 확인 완료.
- 범위 제외 항목(운영중 수정 플로우, 상세 로그 뷰)을 명시해 이번 플랜이 너무 커지지
  않게 했다.
- 안전 관련 결정(중지 시 열린 포지션 가드, 승인 시 자금 검증)은 모두 "왜 위험한지"를
  코드 근거(daemon.py의 status 체크 위치)와 함께 남겼다.
