# 매도 조건 — 진입가 대비 손절/익절 라인 설계

- 작성일: 2026-07-21
- 상태: 승인 대기 (사용자 리뷰 전)
- 참고: `C:\Users\jungm\project\backtesting_1`의 리스크 관리 필드(`stop_loss`/`take_profit`)는 백엔드에서 실제로 소비되지 않는 미구현 통과 필드였음을 확인함. 이 스펙은 그 방식(별도 리스크 설정 폼)을 따르지 않고, 사용자가 요청한 대로 **기존 매도 조건 트리(AND/OR 빌더)에 포함되는 조건 블록**으로 구현한다.

## 배경 및 목적

현재 매수/매도 조건은 `engine/condition_tree.py`의 `eval_group()`이 처리하며, 모든 조건 블록은 `{indicator, params, operator, threshold}` 형태로 `bt.Indicator`(캔들 데이터에서 미리 계산되는 지표)만 참조한다. 사용자는 "포지션 진입가 대비 수익률이 ±N%일 때 매도"하는 손절/익절 조건을 매도 조건 선택박스에 추가하고 싶어한다.

이 조건은 기존 지표와 달리 **포지션이 열려야만 계산 가능한 런타임 상태**(진입가)가 필요하다는 점에서 다른 지표들과 근본적으로 다르다. 카테고리 라벨(기타/손익/포지션 등)의 문제가 아니라 평가 방식 자체가 다른 새로운 종류의 조건 블록임을 확인했고, 아래와 같이 설계한다.

## 카테고리 및 지표 정의

기존 카테고리(추세/오실레이터/거래량)와 나란히 **"손익"** 카테고리를 새로 추가하고, 그 안에 지표 2개를 둔다. 사용자가 연산자를 직접 고를 필요 없이 값만 입력하도록, 각 지표는 연산자가 고정된다.

| value | label | 고정 연산자 | 기본 임계값 | 의미 |
|---|---|---|---|---|
| `STOP_LOSS_PCT` | 손절라인 (%) | `<=` | `-5` | 포지션 수익률이 이 값 이하로 내려가면 매도 |
| `TAKE_PROFIT_PCT` | 익절라인 (%) | `>=` | `10` | 포지션 수익률이 이 값 이상으로 오르면 매도 |

두 지표 모두 `params: []`(파라미터 없음), `sellOnly: true`(매수 조건에는 노출하지 않음).

**주의**: UI 프리뷰 단계에서 "= -5"처럼 표기한 적이 있었으나 이는 표시용 예시였을 뿐, 실제 비교 연산자는 반드시 `<=`(손절)/`>=`(익절)로 고정한다. `==`를 쓰면 가격이 임계값에 정확히 일치하는 극히 드문 순간에만 트리거되어 사실상 동작하지 않는다.

## 타입/카탈로그 변경

### `frontend/lib/types/eda.ts` — `IndicatorCatalogItem`

```ts
export interface IndicatorCatalogItem {
  value: string;
  label: string;
  category: string;
  params: IndicatorParamDef[];
  description: string;
  example: string;
  fixedOperator?: ComparisonOperator; // 있으면 연산자 select 대신 고정 배지 표시
  sellOnly?: boolean;                 // true면 매수 조건 카탈로그에서 제외
}
```

(`ComparisonOperator`는 `./strategy`에서 import)

### `backend/main.py` — `INDICATOR_CATALOG`

`STOP_LOSS_PCT`, `TAKE_PROFIT_PCT` 두 항목을 `category: "손익"`으로 추가. `description`/`example`은 기존 항목들과 같은 톤으로 작성(포지션 진입가 대비 수익률 개념 설명 + 숫자 예시).

## 프론트엔드 UI 변경 (`StrategyConditionBuilder.tsx`)

- `CATEGORY_ORDER`에 `'손익'` 추가(추세/오실레이터/거래량 뒤, 시장 심리 앞 또는 뒤 — 순서는 구현 시 자연스러운 위치로).
- `CATEGORY_DOT_COLOR`에 `손익: 'bg-orange-500'` 추가.
- `ConditionBlockEditor`: `catalogItem.fixedOperator`가 있으면
  - 연산자 `<select>` 대신 고정 연산자 기호(`OPERATOR_SYMBOLS[fixedOperator]`)를 읽기 전용 배지로 표시.
  - `handleIndicatorChange`에서 해당 지표로 바뀌는 순간 `block.operator`를 `fixedOperator`로 강제 설정.
- `recommendedThreshold()`에 분기 추가: `STOP_LOSS_PCT` → `-5`, `TAKE_PROFIT_PCT` → `10` (코인 가격과 무관한 고정값이므로 `currentPrice` 인자 불필요).

### `PortSetupForm.tsx`

매수 조건 쪽 `StrategyConditionBuilder`에 넘기는 `catalog`를 `catalog.filter((c) => !c.sellOnly)`로 필터링. 매도 조건 쪽은 기존 그대로 전체 `catalog` 전달.

## 엔진 평가 로직 변경

### `engine/condition_tree.py`

```python
POSITION_RELATIVE_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT"}
```

- `eval_group(group, indicators, position_return_pct=None)`: 블록의 `indicator`가 `POSITION_RELATIVE_INDICATORS`에 속하면, `indicators` 딕셔너리 조회 대신 `position_return_pct` 값을 사용해 `apply_operator(position_return_pct, item["operator"], float(item["threshold"]))`로 평가. `position_return_pct`가 `None`이면(포지션 없음) 해당 블록은 `False` 처리.
- `find_unknown_indicators`: `INDICATOR_FACTORY`에 없어도 `POSITION_RELATIVE_INDICATORS`에 속하면 "알려진 지표"로 인정하도록 조건 추가.
- `max_required_period`: 변경 불필요 — 이 블록들은 `params`가 비어 있어 자연스럽게 워밍업 기간에 영향을 주지 않는다.

### `engine/condition_strategy.py`

- `_ensure_indicator`: 블록의 `indicator`가 `POSITION_RELATIVE_INDICATORS`에 속하면 `bt.Indicator` 생성을 건너뛰고 즉시 반환(팩토리 조회 안 함).
- `next()`:
  ```python
  def next(self) -> None:
      if not self.position:
          if eval_group(self._buy_cond, self._buy_inds):
              self.buy()
      else:
          entry_price = self.position.price
          position_return_pct = (
              (self.data.close[0] - entry_price) / entry_price * 100
              if entry_price else None
          )
          if eval_group(self._sell_cond, self._sell_inds, position_return_pct=position_return_pct):
              self.sell()
  ```
- 매수 조건(`_buy_cond`) 평가 시엔 `position_return_pct`를 넘기지 않음(기본값 `None`) — 애초에 프론트에서 `sellOnly`로 막아두지만, 혹시 매수 조건에 이 지표가 잘못 섞여 들어와도 조용히 `False`로 처리되어 에러 없이 안전하게 무시된다.

## 범위 밖

- 계좌 단위(총자본 대비) 손절/서킷브레이커 — 이전 대화에서 사용자가 불필요하다고 확인함.
- 트레일링 스탑(추적 손절) — 이번 요청 범위 밖. 필요 시 별도 지표(`TRAILING_STOP_PCT` 등)로 후속 스펙에서 다룸.
- `backtesting_1`의 `risk_config` 스타일 폼(초기자본과 별개인 손절 설정 UI) 도입 — 이번엔 조건 트리 방식으로만 구현.

## Self-Review 결과

- **스펙 커버리지**: 사용자가 승인한 세 가지 결정(매도 조건에만 노출, "손익" 카테고리명, 손절라인/익절라인 두 지표로 분리)이 각각 카테고리 정의, UI 변경, 프론트 필터링 섹션에 반영됨.
- **내부 정합성 확인**: 프리뷰 단계에서 보여준 "= -5" 표기와 실제 고정 연산자(`<=`/`>=`)가 다르다는 점을 "카테고리 및 지표 정의" 섹션에 명시적으로 적어 모순을 없앰.
- **범위 확인**: 계좌 단위 리스크 관리, 트레일링 스탑은 범위 밖으로 명시. `backtesting_1`의 미구현 `risk_config` 패턴을 따르지 않는다는 점도 배경 섹션에 명시.
- **대상 파일 목록**: `frontend/lib/types/eda.ts`, `backend/main.py`, `frontend/components/StrategyConditionBuilder.tsx`, `frontend/components/PortSetupForm.tsx`, `engine/condition_tree.py`, `engine/condition_strategy.py`, 그리고 `tests/test_condition_tree.py`(신규 케이스 추가 필요 — TDD로 구현 시 writing-plans 단계에서 다룸).
