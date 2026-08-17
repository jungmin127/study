# 라이브 전략 매수/매도 설정 조회 (정보 버튼)

## 배경 / 문제

라이브 전략 관리 화면(`frontend/components/LiveStrategiesPage.tsx`)의 전략 카드는 market/timeframe/status/현재 자본/보유 포지션만 보여준다. 어떤 매수·매도 지표 조합과 리스크 관리 설정으로 전략이 돌아가고 있는지는 화면 어디서도 확인할 수 없다. 값 자체는 이미 DB에 저장돼 있지만 API 응답에 포함되지 않는다.

## 목표

전략 카드에 정보(물음표) 버튼을 추가해, 클릭 시 해당 전략의 **현재** 매수 조건 / 매도 조건 / 리스크 관리 설정을 모달로 보여준다. 조회 전용이며 변경 이력은 다루지 않는다.

## 비목표

- 설정 변경/편집 (읽기 전용)
- 설정 변경 이력 표시
- 백테스트 상세 페이지의 조건 표시 방식 변경 (이미 존재하는 표기를 그대로 재사용할 뿐)

## 설계

### 데이터 흐름

`trading_db.get_live_strategy()`가 반환하는 dict에는 `buy_conditions_json`, `sell_conditions_json`, `risk_config_json` (모두 JSON 문자열)이 이미 들어 있다. 그러나 `backend/main.py`의 `_live_strategy_response()`(1167행)가 이 필드들을 응답 dict 구성에서 빠뜨리고 있다.

`_live_strategy_response()`를 수정해 세 필드를 `json.loads()`로 파싱해 응답에 추가한다:

```python
def _live_strategy_response(strategy: dict, position: dict | None, current_price: float | None) -> dict:
    return {
        ...기존 필드...,
        "buy_conditions": json.loads(strategy["buy_conditions_json"]),
        "sell_conditions": json.loads(strategy["sell_conditions_json"]),
        "risk_config": json.loads(strategy["risk_config_json"]),
    }
```

이 함수는 목록 API(`GET /api/v1/live-strategies`)와 단건 조회(`_full_live_strategy_response`) 양쪽에서 공용으로 쓰이므로, 목록 화면에서 별도 API 호출 없이 바로 모달을 채울 수 있다.

### 프론트엔드 타입

`frontend/lib/types/liveStrategies.ts`의 `LiveStrategy` 인터페이스에 필드 추가:

```typescript
export interface LiveStrategy {
  ...기존 필드...,
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
}
```

(`ConditionGroup`, `LiveStrategyRiskConfig` 모두 같은 파일/인접 파일에 이미 정의돼 있음.)

### UI

`LiveStrategiesPage.tsx`의 전략 카드에 Info 아이콘 버튼을 추가한다. 클릭 시 이 프로젝트에 이미 있는 비파괴적 모달 컴포넌트(`frontend/components/ui/dialog.tsx` — 삭제 확인에 쓰는 `AlertDialog`와는 별개)를 사용해 다음을 표시:

1. **매수 조건**: `summarizeGroup(strategy.buy_conditions)` — `frontend/lib/condition-summary.ts`에 이미 있고, 백테스트 카드(`BacktestRunCard.tsx`, `BacktestRunsTable.tsx`)에서 조건 요약에 쓰이는 것과 동일한 함수. `RSI(period=14)>70 and ...` 형태 텍스트로 렌더링해 기존 표기 관례와 통일한다.
2. **매도 조건**: `summarizeGroup(strategy.sell_conditions)` — 동일 방식. (손절/익절 조건은 `STOP_LOSS_PCT`/`TAKE_PROFIT_PCT` 지표 블록으로 이미 sell_conditions 안에 포함되므로 별도 처리 불필요.)
3. **리스크 관리**: `risk_config`의 각 필드를 한글 라벨의 key-value 목록으로 표시.
   - `position_sizing_mode`/`position_sizing_value` → "포지션 사이징: 고정 금액 / 자본 비율(%)"
   - `max_position_per_market` → "코인당 최대 포지션"
   - `order_execution_mode`/`order_timeout_sec` → "주문 방식"
   - `manual_intervention_policy` → "수동 개입 정책"
   - `daily_loss_limit_pct` → "일일 손실 한도(%)"
   - `consecutive_loss_limit` → "연속 손실 한도"

모달은 현재 값만 보여주며, 변경 이력이나 편집 기능은 없다.

## 테스트 계획

- 백엔드: `_live_strategy_response()`가 buy_conditions/sell_conditions/risk_config를 올바르게 파싱해 반환하는지 단위 테스트 (`tests/test_backend.py`에 기존 라이브 전략 응답 테스트가 있다면 그 옆에 추가).
- 프론트: 수동 확인 — 실행 중인 전략 카드에서 정보 버튼 클릭 시 모달이 뜨고, 생성 시 입력한 조건/리스크 설정과 일치하는지 확인.
