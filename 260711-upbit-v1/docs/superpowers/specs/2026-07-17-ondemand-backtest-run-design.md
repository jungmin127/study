# 온디맨드 백테스트 실행 화면 설계

- 작성일: 2026-07-17
- 상태: 승인 대기 (사용자 리뷰 전)
- 선행 문서: `project-plan/2026-07-12-upbit-strategy-eda-dashboard-design.md` (서브2-1, 구현 완료)

## 배경 및 목적

서브2-1 EDA 대시보드는 `run_sweep()`으로 미리 실행해둔 고정 조합(코인×봉타입×전략)만 `sweep_history`에서 조회한다. 사용자가 대시보드에 없는 코인/전략 조합이나 임의의 기간을 궁금해하면, 매번 스크립트(`scripts/run_eda_sweep.py`)를 직접 수정해서 재실행해야 한다.

이번 스펙의 목적은 대시보드에서 **코인/전략/기간을 직접 선택하면 그 자리에서 새 백테스트를 실행해 결과를 보여주는 화면**을 추가하는 것이다. 서브2-1 스펙의 "향후 확장" 항목에서 의도적으로 제외했던 "대시보드에서 직접 스윕 트리거"를, 스윕 전체가 아니라 **단일 조합 실행**으로 한정해 지금 구현한다.

## 스코프

- FastAPI에 온디맨드 백테스트 실행 엔드포인트(`POST /api/v1/backtests/run`)와 신호 목록 조회 엔드포인트(`GET /api/v1/eda/signals`) 추가.
- Next.js `/backtests` 탭의 플레이스홀더 안내문을 선택 폼(코인/봉타입/전략/기간)으로 교체.
- 실행 결과는 기존 `/backtests/[runId]` 상세 화면(자산곡선+거래내역)을 그대로 재사용해 보여준다 — 상세 화면 자체는 변경하지 않는다.
- **범위 밖**: 리스크 설정(초기자본/수수료/포지션 사이징) 커스터마이징, 임의 코인 자유 입력(전체 마켓 동적 조회), 실행 결과를 `sweep_history`(히트맵/랭킹 탭)에 반영하는 것, 실행 이력 목록/취소 기능.

## 이전 설계와의 관계 / 재사용

- `engine/cache.run_backtest_cached()`를 그대로 호출한다 — 동일 조건 재실행 시 캐시 hit으로 즉시 응답하는 동작을 그대로 활용.
- `upbit_data_service.get_candles()`의 페이지네이션(200개 단위 반복 호출)과 로컬 parquet 캐시를 그대로 재사용한다. 장기간+짧은 봉타입을 캐시 없이 처음 조회하면 API 호출이 수십~백여 회 발생해 응답이 수십 초 걸릴 수 있다는 점을 UI에 반영한다.
- `signals.SIGNAL_REGISTRY`를 그대로 신호 소스로 사용해 레지스트리 확장성(새 신호 등록 시 다른 코드 수정 없이 동작)을 프런트까지 유지한다.
- 결과는 `backtest_results` 테이블(캐시)에는 저장되지만, `sweep_history`에는 기록하지 않는다 — `sweep_history`는 `run_sweep()`이 관리하는 고정 그리드 전용으로 유지해, 히트맵/랭킹 탭이 임의 조합으로 오염되지 않게 한다.

## 아키텍처

```
frontend/components/BacktestRunForm.tsx (신규, 'use client')
  - 마운트 시 GET /api/v1/eda/signals 호출 → 전략 체크박스 렌더링
  - 코인/봉타입 목록은 프런트에 고정 상수로 하드코딩
  - 제출 시 POST /api/v1/backtests/run 호출 → 성공하면 router.push(`/backtests/${run_id}`)

frontend/app/backtests/page.tsx (교체)
  - 기존 안내 문구 대신 <BacktestRunForm /> 렌더링

frontend/lib/api/eda.ts (수정)
  - getSignals(), runBacktest(req) 함수 추가

frontend/lib/types/eda.ts (수정)
  - RunBacktestRequest, RunBacktestResponse 타입 추가

backend/main.py (수정)
  - GET /api/v1/eda/signals: SIGNAL_REGISTRY 키 목록 반환
  - POST /api/v1/backtests/run: 요청 검증 → get_candles() → run_backtest_cached() → {"run_id": ...} 반환
```

기존 `/backtests/[runId]/page.tsx`, `EquityCurveChart`, `engine/*`, `signals.py`는 변경하지 않는다.

## 인터페이스

```python
# backend/main.py

class RunBacktestRequest(BaseModel):
    market: str
    timeframe: str
    start: str            # "YYYY-MM-DD"
    end: str              # "YYYY-MM-DD"
    signal_keys: list[str]  # SIGNAL_REGISTRY 키, 최소 1개

@app.get("/api/v1/eda/signals")
def get_signals() -> list[str]:
    return sorted(SIGNAL_REGISTRY.keys())

@app.post("/api/v1/backtests/run")
def run_backtest_endpoint(req: RunBacktestRequest) -> dict:
    # signal_keys 검증(빈 리스트/미등록 키 → 400)
    # start/end 파싱 및 start < end 검증(→ 400)
    # get_candles(market, timeframe, start_dt, end_dt) 호출
    #   - 결과가 비어있으면 → 400 "해당 기간에 캔들 데이터가 없습니다"
    # signals = [SIGNAL_REGISTRY[k] for k in signal_keys]
    # is_combined = len(signals) > 1
    # run_backtest_cached(df=..., strategy_cls=SignalStrategy, risk_config=DEFAULT_RISK_CONFIG,
    #                      market=..., timeframe=..., start=start_dt, end=end_dt,
    #                      strategy_params={"signals": signals})
    # return {"run_id": result["run_id"]}
```

```typescript
// frontend/lib/types/eda.ts
export interface RunBacktestRequest {
  market: string;
  timeframe: string;
  start: string;   // "YYYY-MM-DD"
  end: string;     // "YYYY-MM-DD"
  signal_keys: string[];
}

export interface RunBacktestResponse {
  run_id: string;
}
```

```typescript
// frontend/lib/api/eda.ts
export function getSignals(): Promise<string[]> {
  return apiFetch<string[]>('/api/v1/eda/signals');
}

export function runBacktest(req: RunBacktestRequest): Promise<RunBacktestResponse> {
  return apiFetch<RunBacktestResponse>('/api/v1/backtests/run', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}
```

## 대시보드 화면 (프런트엔드)

`/backtests` 탭 — 기존 안내 문구를 아래 선택 폼으로 교체:

```
┌─ 백테스트 실행 ──────────────────────────┐
│ 코인:    [KRW-BTC ▾]  (KRW-BTC, KRW-ETH)  │
│ 봉타입:  [일봉 ▾]  (일봉/4시간봉/1시간봉/15분봉)│
│ 전략:    ☑ macd_cross   ☐ rsi_zone        │
│          ☐ sma_cross    ☐ bollinger_band  │
│ 기간:    [2026-04-19] ~ [2026-07-17]      │
│          (기본값: 오늘 ~ 90일 전)             │
│                                            │
│          [실행]                           │
│ 기간이 길고 봉타입이 짧을수록 최초 조회 시     │
│ 시간이 걸릴 수 있습니다.                     │
│                                            │
│ (에러 시 이 자리에 빨간 텍스트로 사유 표시)     │
└────────────────────────────────────────────┘
```

- 코인 목록(`["KRW-BTC", "KRW-ETH"]`)과 봉타입 목록(`days`=일봉, `minutes240`=4시간봉, `minutes60`=1시간봉, `minutes15`=15분봉)은 프런트 상수로 고정.
- 전략 체크박스는 `getSignals()` 결과로 동적 렌더링, 최소 1개 선택 필요.
- 실행 버튼은 요청 진행 중 "실행 중..."으로 비활성화.
- 네비게이션 탭 구성(5개: 히트맵/랭킹/추이/백테스트 상세/모델 정확도)은 변경 없음.

## 데이터 흐름

1. `/backtests` 진입 → `BacktestRunForm` 마운트 → `GET /api/v1/eda/signals` 호출, 체크박스 렌더링.
2. 사용자가 코인/봉타입/전략(1개 이상)/기간 선택 후 "실행" 클릭.
3. 클라이언트 유효성 검사(전략 미선택, 기간 역전) 실패 시 API 호출 없이 즉시 인라인 에러.
4. `POST /api/v1/backtests/run` 호출, 버튼 비활성화.
5. 백엔드: 검증 → `get_candles()`(캐시+페이지네이션 내부 처리) → `run_backtest_cached()` → `{"run_id": ...}` 반환.
6. 성공 시 `router.push('/backtests/' + run_id)` → 기존 상세 페이지가 `GET /api/v1/backtests/{run_id}`로 자산곡선+거래내역 표시.
7. 동일 조건 재실행 시 `run_backtest_cached()`가 캐시 hit을 반환해 즉시 응답.

## 에러 처리

| 상황 | 처리 |
|---|---|
| 전략 미선택 / 기간 역전(`start >= end`) | 프런트 즉시 검증, API 호출 안 함 |
| `signal_keys`가 빈 리스트이거나 레지스트리에 없는 키 포함 | 백엔드 400 |
| 해당 기간에 캔들 데이터 없음(빈 DataFrame) | 백엔드 400 "해당 기간에 캔들 데이터가 없습니다" |
| Upbit API 호출 실패(재시도 소진, `upbit_data_service`가 `RuntimeError` 발생) | 백엔드가 500으로 매핑, `detail` 메시지 전달 |
| 위 모든 백엔드 에러 | 폼 하단에 `detail` 메시지 표시, 버튼 재활성화 |
| 응답 지연(장기간+짧은 봉, 캐시 미스) | 별도 타임아웃 없이 끝까지 대기, 폼에 소요 시간 안내 문구 표시 |

## 테스트

- `tests/test_backend.py`에 케이스 추가:
  - 정상 요청 → `run_id` 반환, 이어서 `GET /api/v1/backtests/{run_id}`로 조회 가능한지 확인
  - `signal_keys=[]` → 400
  - 존재하지 않는 signal key 포함 → 400
  - `start >= end` → 400
  - `GET /api/v1/eda/signals`가 `SIGNAL_REGISTRY` 키 목록을 반환하는지 확인
- 프런트엔드는 기존 프로젝트 관례(단위테스트 없음, 수동 확인)를 따름: dev 서버 기동 후 브라우저로 폼 제출 → `/backtests/{run_id}` 이동 및 결과 렌더링까지 수동 확인.

## Self-Review 결과

- **스펙 커버리지**: 사용자가 요청한 코인/전략/기간 선택 → 온디맨드 실행 → 결과 확인의 전 과정을 아키텍처/인터페이스/데이터흐름/에러처리/테스트 각 섹션에서 다룸.
- **캔들 200개 제한 관련 사용자 질문 반영**: `upbit_data_service._fetch_range()`가 이미 페이지네이션을 처리한다는 점을 확인하고, 그로 인한 응답 지연 가능성을 에러 처리·화면 안내 문구에 반영.
- **범위에서 의도적으로 제외한 것**: 리스크 설정 커스터마이징, 전체 마켓 동적 조회, `sweep_history` 반영, 실행 이력 목록 — 모두 스코프 섹션에 명시.
