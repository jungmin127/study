# 매매일지 수수료 반영 + 그래프 리디자인

## 배경

사용자가 매매일지(journal)를 살펴보다 두 가지 문제를 지적했다.

1. 계좌 전체/코인별 누적 손익(`cumulative_pnl`)이 수수료를 제대로 반영하고 있는지 확실치 않다.
2. 매매일지의 그래프(계좌 전체 요약, 코인별)의 가시성이 나쁘다 — 모바일에서 누적손익이 3,818원(0.25%)인데 y축은 약 1,500,000으로 찍혀 있어(총자산 기준) 의미가 와닿지 않고, 천단위 콤마도 없고, 탭 시 뜨는 팝업(툴팁)도 불필요하다.

조사 결과 두 가지 사실이 확인됐다.

- **수수료 버그**: `realized_pnl` 계산(`trading/position_manager.py:close_position`)이 매도 수수료만 차감하고 매수 수수료는 반영하지 않는다. `entry_price * entry_qty`에 매수 수수료가 포함되지 않아 손익이 매수 수수료만큼 과대 계상된다. `positions` 테이블에는 애초에 매수 수수료를 저장할 컬럼도 없다.
- **그래프 y축 오표현**: `equity_curve`/`daily`의 값은 "baseline(원금) + 누적 realized_pnl" = **총자산**이지 순수 누적손익이 아니다. 또한 청산 없는 날짜는 배열에서 생략되고, X축이 카테고리축이라 공백 기간이 시각적으로 뭉개진다. y축/툴팁에 콤마 포맷이 없고, 모바일에서 tap이 recharts 기본 hover 툴팁을 트리거해 사용자가 "팝업"으로 느낀다.

## 목표

1. 매수 수수료를 손익 계산에 포함시키고, 이미 청산된 과거 거래도 소급 재계산한다.
2. 매매일지 그래프를 "총자산 누적선" 대신 "최근 30일 일별 실현손익 막대그래프"로 바꾼다.

## 범위 밖

- `JournalCalendar.tsx`(달력 뷰)의 표현 방식 변경 — 이번 작업 대상 아님, 현행 유지.
- 미실현손익(open position)의 수수료 처리 — 청산되지 않은 포지션은 대상 아님.
- 외부(수동) 주문/입출금으로 인한 `adjust_position_qty` top-up 케이스의 entry_price 재보정 — 별도 알려진 한계([[upbit-v1-live-trading-edge-case-audit]] 참고), 이번 스코프 아님.

---

## 1. 수수료 버그 수정

### 스키마 변경

`trading/db.py`의 `positions` 테이블에 컬럼 추가:

```sql
ALTER TABLE positions ADD COLUMN entry_fee REAL NOT NULL DEFAULT 0;
```

### 진행 흐름 수정

- `trading/order_executor.py::enter()`: 매수 체결 결과 `result["fee"]`를 `position_manager.open_position()` 호출 시 함께 전달.
- `trading/position_manager.py::open_position()`: `entry_fee` 파라미터를 받아 `positions` insert에 포함하도록 시그니처 확장.
- `trading/position_manager.py::close_position()`: 손익 계산식을
  ```python
  realized_pnl = (exit_price * exit_qty) - (entry_price * entry_qty) - entry_fee - exit_fee
  ```
  로 변경 (`entry_fee`는 포지션 row에서 조회).
- 부수 수정: `trading/order_executor.py::enter()`에서 매수 주문 삽입 시 `position_id=None`으로 넣고 이후 갱신하지 않던 부분을, `open_position()` 직후 해당 주문 row의 `position_id`를 채우도록 수정(`db.update_order_position_id()` 신설 또는 유사 함수). 이번 소급 재계산에는 쓰이지 않지만(과거 데이터엔 적용 불가), 향후 조인/추적을 위해 함께 고친다.

### 소급 재계산 (1회성 마이그레이션 스크립트)

경로: `scripts/backfill_entry_fee.py` (1회성, 재사용 안 함).

절차:
1. 실행 전 `trading.db`의 sqlite 파일을 타임스탬프 붙여 백업.
2. `live_strategy_id`별로:
   - 그 전략의 `orders` 중 `side='bid' AND status='done'`인 행을 `created_at` 오름차순 정렬.
   - 그 전략의 `positions` 중 `status='closed'`인 행을 `entry_time` 오름차순 정렬.
   - 두 리스트 길이가 같으면 순서대로 1:1 매칭, 각 포지션의 `entry_fee`를 매칭된 주문의 `fee`로 채우고 `realized_pnl`/`realized_pnl_pct`를 재계산해 업데이트.
   - 길이가 다르면(주문 유실/중복 재시도 등으로 매칭 불확실) 아무것도 바꾸지 않고 `live_strategy_id`와 사유를 콘솔에 경고 출력 — 수동 확인 대상으로 남긴다.
3. `daily_performance` 테이블도 realized_pnl 변경분을 반영해 해당 날짜 행들을 재계산(`trading/risk_manager.py`의 기존 집계 로직 재사용).
4. 실행 후 처리 건수(재계산됨/스킵됨)를 요약 출력.

로컬 DB 사본으로 먼저 드라이런 후, 문제 없으면 AWS 서버 DB에도 동일 스크립트 적용(사용자가 직접 실행).

---

## 2. 그래프 리디자인

### 백엔드 (`backend/trading_analytics_service.py`, `backend/main.py`)

기존 `equity_curve`(계좌 전체)와 `daily`(코인별)는 **그대로 유지**한다 — `daily`는 `JournalCalendar.tsx`가 `pnl_pct` 필드를 쓰고, `equity_curve`/`daily`의 누적값은 MDD(`_mdd_pct`) 계산 내부 입력으로 계속 필요하다.

대신 그래프 전용 새 필드를 추가한다:

- `get_journal_summary()` 응답에 `daily_pnl_30d: [{date, pnl}]` 추가.
- `get_market_journal()` 응답에도 동일하게 `daily_pnl_30d: [{date, pnl}]` 추가.

계산 방식:
- 오늘 포함 최근 30일의 날짜를 전부 생성(`date` = 'YYYY-MM-DD').
- 이미 계산된 `pnl_by_date`(계좌 전체) / 코인별 합산 dict에서 값을 채우고, 없는 날짜는 `pnl: 0`.
- `_strategy_metrics()`/`_market_metrics()`가 이미 만드는 `pnl_by_date` 딕셔너리를 재사용 — 새 30일 zero-fill 헬퍼 함수(`_zero_filled_last_30_days(pnl_by_date)`) 하나만 추가해 두 곳(계좌 전체/코인별)에서 공용으로 쓴다.

### 프론트엔드 타입 (`frontend/lib/types/journal.ts`)

```ts
export interface JournalDailyPnlPoint {
  date: string;
  pnl: number;
}
```

`JournalSummary`, `JournalMarketDetail`에 `daily_pnl_30d: JournalDailyPnlPoint[]` 필드 추가(기존 필드는 그대로 둠).

### 프론트엔드 컴포넌트 (`frontend/components/JournalPage.tsx`)

계좌 전체 요약 그래프(현재 132-147행), 코인별 그래프(현재 179-194행) 둘 다 동일한 패턴으로 교체:

- `recharts`의 `LineChart` → `BarChart`, `data={summary.daily_pnl_30d}` / `data={detail.daily_pnl_30d}`.
- X축(`date`): 30개 항목이라 라벨은 5일 간격으로 스킵 표시(`interval` prop 또는 `tickFormatter`로 5의 배수 인덱스만 표기).
- Y축: `tickFormatter={(v) => v.toLocaleString()}`로 천단위 콤마.
- `Bar`: 막대별 색상은 `Cell`로 개별 지정 — 양수 초록, 음수 빨강, 0은 옅은 회색(거의 안 보이는 정도).
- 값 라벨: `Bar`의 `LabelList` (또는 커스텀 `label`)로 막대 위/아래에 콤마 포맷된 값을 상시 표기하되, `pnl === 0`인 날은 라벨을 생략(30개 전부 라벨을 붙이면 과밀).
- `Tooltip` 컴포넌트는 완전히 제거(hover/tap 모두 비활성화) — 라벨이 항상 보이므로 툴팁이 불필요해진 것과 일치.
- 그래프 상단의 "누적손익" 텍스트(카드, `summary.cumulative_pnl`/`detail.cumulative_pnl`)는 기존 그대로 유지 — 이미 수수료 반영된 값을 쓰게 됨(1번 수정 이후).

### 엣지 케이스

- 거래 이력이 아예 없는 코인/계좌: 30일 전부 `pnl: 0` — 축선만 있는 빈 막대그래프로 보임. 기존에 "거래 내역 없음" 빈 상태 문구가 있다면 그 문구 우선(그래프는 안 보이거나 숨김 처리, 기존 로직 유지).
- 전략이 30일 이내에 시작된 경우: 시작 이전 날짜도 자연스럽게 0으로 채워짐(문제 없음).

---

## 테스트 계획

**백엔드 (수수료 수정)**
- `close_position()`이 `entry_fee`와 `exit_fee`를 모두 차감하는지 단위 테스트.
- `open_position()`이 전달받은 fee를 `entry_fee` 컬럼에 저장하는지 단위 테스트.
- `enter()` 통합 테스트로 매수 체결 fee가 `open_position()`까지 전달되는지 확인(`dry_run=True`일 땐 fee=0 유지).
- 매수 주문 `position_id` 백필(사후 업데이트) 테스트.

**마이그레이션 스크립트**
- 격리 테스트 DB: (a) 주문/포지션 수가 일치하는 정상 매칭 케이스 — entry_fee/realized_pnl이 올바르게 갱신되는지, (b) 수가 불일치하는 케이스 — 아무 것도 바뀌지 않고 경고만 출력되는지.
- 스크립트 실행 후 `daily_performance`와 journal summary API 응답이 일관되게 재계산되는지 확인.
- 실 서버 적용 전 로컬 DB 사본으로 드라이런, 처리/스킵 건수 로그 확인.

**프론트엔드 (그래프)**
- 타입 변경 후 `tsc` 타입체크.
- 개발 서버 기동 후 Playwright(webapp-testing)로 실제 화면 확인: 계좌 전체/코인별 그래프의 막대 렌더링, 색상, 라벨, 콤마 포맷 확인.
- 모바일 뷰포트에서 막대 tap 시 툴팁/팝업이 뜨지 않는지 확인.
- 거래가 전혀 없는 코인(30일 전부 0)에서도 그래프가 깨지지 않는지 확인.

**에러 핸들링**
- 마이그레이션 스크립트는 사전 DB 백업 + 스킵된 `live_strategy_id` 목록을 로그로 남겨 수동 확인 가능하게 한다.
- API 레벨 에러 핸들링은 기존 엔드포인트 확장이라 별도 추가 없음(기존 실패 모드와 동일).
