# 라이브 전략 "전략 교체" (같은 코인 유지, 시간봉/전략만 교체) 설계

날짜: 2026-08-18

## 배경

현재는 같은 코인을 다른 시간봉/전략으로 라이브 운용하려면 기존 전략을 중지 → 삭제 → 새로 생성해야 한다. 저널의 코인별 손익 집계(`get_market_journal`)는 전략 단위가 아니라 market(코인) 단위로 그 코인에 속한 모든 라이브 전략(상태 불문)을 자동 합산하므로 "중지"만으로는 이력이 사라지지 않지만, "삭제"를 누르면 그 전략의 거래·포지션·손익 기록이 DB에서 영구 삭제되어 저널에서도 사라진다.

이 기능은 삭제·재생성 없이, 열린 포지션이 없는 라이브 전략을 같은 코인의 다른 백테스트 결과로 **제자리 교체**하는 "전략 교체" 버튼을 추가한다. 하락장 전략을 횡보장 전략으로 바꾸는 식의 운용을 염두에 둔다.

## 결정 사항 (Q&A로 확정)

- **교체 방식**: 새 `live_strategy` 행을 만들지 않고 기존 행을 그대로 갱신한다. 같은 `id`를 유지하므로 저널/캘린더 집계가 끊기지 않는다.
- **노출 상태**: `running`/`paused`/`stopped` 전부에서 노출한다(공통 조건: 열린 포지션 없음). `draft`는 승인 전이라 교체 대상에서 제외 — 애초에 재생성하는 게 자연스럽다.
- **교체 범위**: `timeframe`, `buy_conditions`, `sell_conditions`, `source_run_id`만 새 백테스트 결과 값으로 덮어쓴다. `market`, `current_capital`, **`risk_config`(자금관리: 포지션 사이징·주문 실행 방식·일일 손실 한도·연속 손실 한도)는 기존 값을 그대로 유지**한다.
  - 정정 기록: 초안 설계 단계에서는 "손절/익절·포지션 사이징도 백테스트 값으로 교체"를 고려했으나, 코드 확인 결과 `risk_config`는 백테스트 결과에 아예 존재하지 않는 라이브 전용 입력값(`NewLiveStrategyPage.tsx`가 항상 `DEFAULT_RISK_CONFIG`에서 시작해 사용자가 직접 채우는 폼)이었다. 손절/익절도 별도 필드가 아니라 `sell_conditions` 트리 안에서 표현되므로, "매도 조건 교체"에 이미 포함된다. 따라서 백테스트로부터 넘어올 수 있는 필드는 timeframe/buy_conditions/sell_conditions/source_run_id뿐이다.
- **circuit breaker 리셋**: 교체 시 `circuit_breaker_state`의 `tripped`/`consecutive_losses`/`tripped_reason`/`tripped_at`을 초기화한다. 이전 전략의 손실로 트립된 상태가 새 전략의 매수를 막지 않도록 한다.
- **`last_processed_candle_time` 리셋**: timeframe이 바뀌면 이전 봉 기준 타임스탬프가 새 timeframe 기준으로 봤을 때 최신 봉을 건너뛰게 만들 수 있어(예: 30분→1시간 교체 시 최대 한 사이클 지연), NULL로 리셋한다.
- **팝업 목록 범위**: 같은 market의 백테스트 결과 중 **현재 적용 중인 `source_run_id`는 제외**, **최신순** 정렬. 목록이 비어 있으면 교체 불가 안내와 함께 확인 버튼을 비활성화한다.
- **팝업에 표시할 정보**: 라디오 버튼 + 제목 + 설명 + 수익률만 (항상 1개만 선택 가능).

## 백엔드 변경

### `trading/db.py`

- `replace_live_strategy_strategy(live_strategy_id, source_run_id, timeframe, buy_conditions_json, sell_conditions_json) -> bool` 신설
  - `positions` 테이블에서 `live_strategy_id`와 `status='open'`인 행이 있으면 즉시 `False` 반환 (`stop_live_strategy_if_no_open_position`과 동일한 오픈 포지션 체크 재사용)
  - `UPDATE live_strategies SET source_run_id=?, timeframe=?, buy_conditions_json=?, sell_conditions_json=?, last_processed_candle_time=NULL WHERE id=? AND status IN ('running','paused','stopped')` — `rowcount == 0`이면 `False`
  - 이어서 `UPDATE circuit_breaker_state SET tripped=0, consecutive_losses=0, tripped_reason=NULL, tripped_at=NULL WHERE live_strategy_id=?` (해당 전략의 circuit_breaker_state 행이 없으면 no-op)
  - 하나의 커넥션/트랜잭션으로 묶고 성공 시 `True` 반환
  - `market`, `current_capital`, `risk_config_json`, `baseline_qty`, `status`, `approved_at`/`started_at`/`stopped_at`은 건드리지 않는다

### `backend/main.py`

- `ReplaceLiveStrategyRequest(BaseModel)`: `source_run_id: str`
- `POST /api/v1/live-strategies/{strategy_id}/replace-strategy`
  1. `trading_db.get_live_strategy(strategy_id)` 없으면 404
  2. `strategy["status"] == "draft"`면 409 ("draft 상태의 전략은 교체할 수 없습니다")
  3. `get_run_config(req.source_run_id)`(기존 `get_backtest_config_endpoint`가 쓰는 것과 동일 함수) 없으면 404
  4. `config["market"] != strategy["market"]`이면 400 (방어적 재검증 — 팝업이 이미 같은 market만 보여주지만 클라이언트를 신뢰하지 않는다)
  5. `config["timeframe"] not in VALID_TIMEFRAMES`면 400
  6. `trading_db.replace_live_strategy_strategy(...)` 호출, `False`면 409 ("포지션이 열려 있어 교체할 수 없습니다")
  7. 200: 기존 `_full_live_strategy_response(strategy_id)` 재사용해 갱신된 전략 반환
- `_live_strategy_response`에 `"source_run_id": strategy["source_run_id"]` 필드 추가 (현재 응답에 빠져 있음 — 프론트가 팝업에서 현재 적용 중인 결과를 제외하려면 필요)
- `GET /api/v1/backtests`에 `market: str | None = Query(None)` 파라미터 추가, `list_backtest_runs(market=market)`로 전달

### `engine/cache.py`

- `list_backtest_runs(strategy_name: str = "ConditionTreeStrategy", limit: int = 1000, market: str | None = None)` — `market`이 주어지면 `WHERE r.strategy_name = ? AND r.market = ?`로 필터. 정렬은 이미 `created_at DESC, rowid DESC`(최신순)라 별도 변경 불필요.

## 프론트엔드 변경

### `frontend/lib/types/liveStrategies.ts`

- `LiveStrategy`에 `source_run_id: string | null` 추가

### `frontend/lib/api/liveStrategies.ts`

- `replaceLiveStrategyStrategy(id: string, sourceRunId: string): Promise<LiveStrategy>` 추가 (POST `/api/v1/live-strategies/{id}/replace-strategy`, body `{source_run_id: sourceRunId}`)

### 백테스트 목록 조회 API 클라이언트 (`frontend/lib/api/eda.ts` 등 기존 `listBacktestRuns` 위치)

- `market` 옵션 파라미터 추가해 쿼리스트링으로 전달

### `frontend/components/LiveStrategiesPage.tsx`

- `running`(349행)/`paused`(373행)/`stopped`(396행) 각 분기에 "교체" 아이콘 버튼(`RefreshCw` 등) 추가. `s.open_position`이 있으면 `disabled` + 툴팁으로 사유 안내("포지션이 없을 때만 교체할 수 있습니다").
- 클릭 시 새 다이얼로그(`StrategySwapDialog` 등, 라디오 선택이 필요하므로 기존 `AlertDialog` 확인창이 아니라 `Dialog` 기반 신규 컴포넌트) 오픈.

### 신규 컴포넌트 `frontend/components/StrategySwapDialog.tsx`

- open 시 `GET /api/v1/backtests?market={s.market}` 호출, 응답에서 `run.run_id === s.source_run_id`인 항목 제외 (API가 이미 최신순 정렬)
- 목록이 비어 있으면 "교체 가능한 백테스트 결과가 없습니다" 안내 + 확인 버튼 비활성화
- 각 항목: 라디오 버튼(단일 선택) + 제목(없으면 `BacktestMetaEditor`와 동일한 "(제목 없음)" placeholder) + 설명 + 수익률(기존 `return_rate` 포맷 유틸 재사용)
- 확인 클릭 시 `replaceLiveStrategyStrategy(s.id, selectedRunId)` 호출 → 성공 시 다이얼로그 닫고 목록 `refresh()`, 실패 시 에러 메시지 표시(기존 `runAction` 에러 처리 패턴과 동일하게)

## 저널/이력 영향

전략 교체는 같은 `live_strategy_id`를 유지하므로 저널의 market 단위 집계, 코인별 캘린더, 누적 손익 라인 모두 자동으로 이어진다. `get_market_journal` 등 저널 관련 코드는 수정 불필요.

## 테스트 계획

- `tests/test_trading_db.py`
  - `replace_live_strategy_strategy`: 정상 교체 시 timeframe/buy_conditions_json/sell_conditions_json/source_run_id/last_processed_candle_time(NULL)이 갱신되고 market/current_capital/risk_config_json/baseline_qty는 그대로인지 확인
  - 열린 포지션이 있으면 `False` 반환하고 아무것도 갱신되지 않는지 확인
  - draft 상태에서는 `False` 반환(WHERE 절 가드)
  - circuit_breaker_state가 tripped=1이던 상태에서 교체 후 tripped=0/consecutive_losses=0으로 리셋되는지 확인 (circuit_breaker_state 행이 아예 없는 경우에도 에러 없이 통과하는지)
- `tests/test_backend.py`
  - `POST /api/v1/live-strategies/{id}/replace-strategy`: 404(전략 없음/run_id 없음), 409(draft 상태, 오픈 포지션 있음), 400(market 불일치, 미지원 timeframe), 200(정상 — 응답에 새 buy_conditions/sell_conditions/timeframe/source_run_id 반영 확인)
  - `GET /api/v1/live-strategies`(또는 detail) 응답에 `source_run_id` 필드가 포함되는지 확인
  - `GET /api/v1/backtests?market=...`: 다른 market 결과가 제외되는지, 정렬이 최신순인지 확인
