# 코인별 장세 전략 라이브러리 UI — 설계 스펙

## 배경

[[upbit-v1-regime-strategy-pivot-adx-autoswap]]에서 확정한 4단계 피벗 계획 중
2단계(ADX 기반 장세 판별 엔진 + `/regime` 탭 재구축,
`docs/superpowers/specs_v2/2026-09-06-adx-regime-engine-design.md`)는 완료됐다.
이 스펙은 3단계: 코인별로 하락/횡보/상승 장세에 각각 어떤 백테스트 전략을
쓸지 미리 매핑해두는 관리 화면을 다룬다. 4단계(daemon 자동 스왑 루프)가
이 매핑 데이터를 읽어 코인별 현재 장세에 맞는 전략으로 라이브 전략을
자동 교체하게 된다.

**이미 있어서 재사용하는 것**: "전략 교체"(2026-08-18,
`trading_db.replace_live_strategy_strategy` + `POST
/api/v1/live-strategies/{id}/replace-strategy`)가 "같은 market의 다른 백테스트
결과로 조건을 바꿔치기"하는 로직과 검증(시간봉 지원 여부, 미지원 지표, 빈
조건, `getBacktestRuns(market)` 후보 리스트를 보여주는 피커 UI)을 이미 갖고
있다. 이번 3단계는 이 패턴을 "라이브 전략 자체를 바꾸는" 것이 아니라 "나중에
쓸 전략을 미리 저장해두는" 용도로 재사용한다.

## 목표

1. `engine/regime_adx_constants.MAJOR_MARKETS`(20개 코인) 각각에 대해 하락/
   횡보/상승/기본 4개 슬롯에 백테스트 결과를 매핑해 저장하는 신규 테이블+API
2. 새 탭 `/strategy-library`("전략 라이브러리")에서 20×4 매핑을 표로 보고
   각 슬롯을 설정/변경/제거
3. 각 코인 행에 현재 ADX 장세 판정과, 그 코인에 실제로 돌고 있는 라이브
   전략이 매핑과 동기화됐는지 여부를 함께 표시(정보 제공용 — 이 표시가
   실제로 라이브 전략을 바꾸지는 않는다)

**"기본" 슬롯의 의도**: 장세와 무관하게 장기간(2개월 이상) 꾸준히 성과가
난 백테스트 전략을 매핑해두는 용도(사용자 확정 요구사항). 4단계에서
정확히 언제 이 슬롯이 자동 선택되는지(미분류 장세일 때 / 코인별 자동스왑을
꺼뒀을 때)는 4단계 설계에서 결정한다. 이번 3단계는 이 슬롯을 다른 3개와
동일한 방식으로 저장/조회할 수 있게만 만든다.

## 비범위

- 4단계(daemon 자동 스왑 루프) — 별도 세션, "기본" 슬롯이 정확히 언제
  선택되는지의 로직도 여기 포함
- `MAJOR_MARKETS` 외 마켓, `minutes60` 외 타임프레임 지원
- 매핑에 쓰인 백테스트 결과가 삭제됐을 때 매핑을 자동으로 무효화하거나
  알림을 주는 기능(스냅샷 방식이라 동작 자체는 영향 없음 — "설계" 절 참고)
- 라이브 전략 상태 뱃지에서 감지한 "불일치"를 자동으로 교정하는 기능(그건
  4단계의 역할)

## 설계

### 1. 데이터 모델 — `trading/db.py`

`_SCHEMA`에 신규 테이블 추가(다른 테이블과 마찬가지로 `trading.db`,
AWS 실거래 DB와 동일 파일 — 새 테이블이라 기존 행에 영향 없음, ALTER
불필요):

```sql
CREATE TABLE IF NOT EXISTS regime_strategy_library (
    market                TEXT NOT NULL,
    regime                TEXT NOT NULL CHECK (regime IN ('하락', '횡보', '상승', '기본')),
    source_run_id         TEXT NOT NULL,
    timeframe             TEXT NOT NULL,
    buy_conditions_json   TEXT NOT NULL,
    sell_conditions_json  TEXT NOT NULL,
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (market, regime)
);
```

`live_strategies`와 동일하게 **저장 시점에 스냅샷**한다(`source_run_id`뿐
아니라 `timeframe`/`buy_conditions_json`/`sell_conditions_json`도 복사). 백테스트
결과가 나중에 삭제되거나 수정돼도 매핑이 깨지지 않고, 4단계가 스왑할 때마다
백테스트 설정을 재조회/재검증할 필요가 없다. 부분 매핑을 허용하므로(코인당
0~4개 슬롯) 행이 없으면 그냥 "미설정"이다.

`trading/db.py` 신규 함수 3개(기존 `replace_live_strategy_strategy` 바로
아래):

```python
def upsert_regime_strategy_mapping(
    market: str,
    regime: str,
    source_run_id: str,
    timeframe: str,
    buy_conditions_json: str,
    sell_conditions_json: str,
) -> None:
    """market+regime 슬롯을 있으면 덮어쓰고 없으면 새로 만든다."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO regime_strategy_library "
            "(market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(market, regime) DO UPDATE SET "
            "source_run_id=excluded.source_run_id, timeframe=excluded.timeframe, "
            "buy_conditions_json=excluded.buy_conditions_json, "
            "sell_conditions_json=excluded.sell_conditions_json, updated_at=excluded.updated_at",
            (market, regime, source_run_id, timeframe, buy_conditions_json, sell_conditions_json),
        )
        conn.commit()
    finally:
        conn.close()


def delete_regime_strategy_mapping(market: str, regime: str) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute(
            "DELETE FROM regime_strategy_library WHERE market = ? AND regime = ?",
            (market, regime),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def list_regime_strategy_mappings() -> list[dict]:
    """설정된 슬롯만 반환한다(미설정 슬롯은 행 자체가 없음)."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM regime_strategy_library").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

`TABLE_NAMES` 튜플에 `"regime_strategy_library"` 추가(테스트/정리 스크립트가
이 튜플로 전체 테이블을 순회하는 곳이 있으면 자동 포함되도록).

### 2. 백엔드 API — `backend/main.py`

기존 `replace_live_strategy_endpoint`의 검증 로직(시간봉 지원 여부, 지원하지
않는 백테스트 결과, 미지원 지표, 빈 조건)을 공용 헬퍼로 추출해 두 엔드포인트가
같이 쓴다:

```python
def _validate_backtest_config_for_market(config: dict, market: str) -> None:
    """백테스트 결과가 실거래(라이브 전략 교체 / 라이브러리 매핑)에 쓰일 수
    있는지 검증한다. config는 get_run_config()의 반환값."""
    if config["strategy_name"] != "ConditionTreeStrategy":
        raise HTTPException(status_code=400, detail="지원하지 않는 백테스트 결과입니다")
    if config["market"] != market:
        raise HTTPException(status_code=400, detail="선택한 백테스트 결과의 마켓이 일치하지 않습니다")
    if config["timeframe"] not in VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 봉데이터입니다: {config['timeframe']}")
    if is_empty(config["buy_conditions"]) or is_empty(config["sell_conditions"]):
        raise HTTPException(status_code=400, detail="매수/매도 조건이 비어 있는 백테스트 결과입니다")
    unknown = sorted(
        set(find_unknown_indicators(config["buy_conditions"]))
        | set(find_unknown_indicators(config["sell_conditions"]))
    )
    if unknown:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 지표입니다: {', '.join(unknown)}")
```

`replace_live_strategy_endpoint`는 검증 6줄을
`_validate_backtest_config_for_market(config, strategy["market"])` 한 줄로
교체한다(동작 변화 없음, `market` 인자만 `strategy["market"]`로 넘김).

신규 엔드포인트:

```python
REGIME_LIBRARY_SLOTS = ("하락", "횡보", "상승", "기본")


class UpsertRegimeStrategyMappingRequest(BaseModel):
    source_run_id: str


@app.get("/api/v1/regime-strategy-library")
def get_regime_strategy_library_endpoint() -> list[dict]:
    return trading_db.list_regime_strategy_mappings()


@app.put("/api/v1/regime-strategy-library/{market}/{regime}")
def upsert_regime_strategy_mapping_endpoint(
    market: str, regime: str, req: UpsertRegimeStrategyMappingRequest
) -> dict:
    if market not in MAJOR_MARKETS:
        raise HTTPException(status_code=400, detail=f"{market}은(는) 지원하지 않는 마켓입니다.")
    if regime not in REGIME_LIBRARY_SLOTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 슬롯입니다: {regime}")

    config = get_run_config(req.source_run_id)
    if config is None:
        raise HTTPException(status_code=404, detail="해당 run_id의 백테스트 설정을 찾을 수 없습니다")
    _validate_backtest_config_for_market(config, market)

    trading_db.upsert_regime_strategy_mapping(
        market,
        regime,
        source_run_id=req.source_run_id,
        timeframe=config["timeframe"],
        buy_conditions_json=json.dumps(config["buy_conditions"]),
        sell_conditions_json=json.dumps(config["sell_conditions"]),
    )
    return {"market": market, "regime": regime, "source_run_id": req.source_run_id}


@app.delete("/api/v1/regime-strategy-library/{market}/{regime}")
def delete_regime_strategy_mapping_endpoint(market: str, regime: str) -> dict:
    if regime not in REGIME_LIBRARY_SLOTS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 슬롯입니다: {regime}")
    trading_db.delete_regime_strategy_mapping(market, regime)
    return {"deleted": True}
```

`GET`은 있는 슬롯만 배열로 반환한다(20×4=80칸 전부가 아니라 실제 설정된
것만) — 프론트가 `MAJOR_MARKETS × REGIME_LIBRARY_SLOTS`로 빈 칸을 채운다.
`DELETE`는 없는 슬롯을 지워도 그냥 조용히 성공 처리한다(idempotent, 멱등
삭제는 다른 삭제 엔드포인트들과 다른 관례지만 — 여기선 "이미 미설정"과
"방금 지워짐"을 구분해 얻을 실익이 없어 단순하게 간다).

### 3. 프론트엔드

**`NavTabs.tsx`**: `/regime` 바로 다음에 탭 추가.

```typescript
{ href: '/strategy-library', title: '전략 라이브러리', icon: Library },
```

**타입** (`frontend/lib/types/regimeLibrary.ts`, 신규):

```typescript
export type RegimeLibrarySlot = '하락' | '횡보' | '상승' | '기본';

export interface RegimeStrategyMapping {
  market: string;
  regime: RegimeLibrarySlot;
  source_run_id: string;
  timeframe: string;
  updated_at: string;
}
```

**API** (`frontend/lib/api/regimeLibrary.ts`, 신규):

```typescript
export function getRegimeStrategyLibrary(): Promise<RegimeStrategyMapping[]> {
  return apiFetch<RegimeStrategyMapping[]>('/api/v1/regime-strategy-library');
}

export function upsertRegimeStrategyMapping(
  market: string, regime: RegimeLibrarySlot, sourceRunId: string,
): Promise<RegimeStrategyMapping> {
  return apiFetch(`/api/v1/regime-strategy-library/${market}/${encodeURIComponent(regime)}`, {
    method: 'PUT',
    body: JSON.stringify({ source_run_id: sourceRunId }),
  });
}

export function deleteRegimeStrategyMapping(market: string, regime: RegimeLibrarySlot): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/v1/regime-strategy-library/${market}/${encodeURIComponent(regime)}`, { method: 'DELETE' });
}
```

**공용 피커 다이얼로그 추출**: `LiveStrategiesPage.tsx`의
`StrategySwapDialog`(같은 market의 `getBacktestRuns(market)` 후보를 불러와
목록에서 고르는 다이얼로그)를 `frontend/components/BacktestPickerDialog.tsx`로
추출한다. 시그니처:

```typescript
function BacktestPickerDialog({
  market,
  title,
  excludeRunId,
  trigger,
  onSelect,
}: {
  market: string;
  title: string;
  excludeRunId?: string | null;
  trigger: React.ReactNode;
  onSelect: (runId: string) => Promise<void>;
}) { /* candidates 로드/선택/제출 상태는 기존 StrategySwapDialog와 동일 */ }
```

`LiveStrategiesPage.tsx`의 `StrategySwapDialog`는 이 컴포넌트를
`onSelect={(runId) => replaceLiveStrategyStrategy(strategy.id, runId)}`로 감싸는
얇은 래퍼로 교체한다(기존 UX/문구 변화 없음). 새 라이브러리 화면은
`onSelect={(runId) => upsertRegimeStrategyMapping(market, regime, runId)}`로 쓴다.

**`frontend/app/strategy-library/page.tsx`** + **`RegimeStrategyLibraryPage.tsx`**
(신규): 20행(코인) × 컬럼(현재장세 | 라이브전략 상태 | 하락 | 횡보 | 상승 | 기본)
테이블.

- 마운트 시 3개를 병렬로 불러온다: `getRegimeStrategyLibrary()`,
  `getRegimeAdxOverview(TIMEFRAME)`(기존, 2단계에서 구현됨),
  `getLiveStrategies()`(기존).
- **현재장세** 컬럼: 오버뷰 결과의 `label`을 기존 `/regime` 탭과 동일한
  색상(`--regime-surge-up`/`--regime-surge-down`/`--marker-boundary`)의 뱃지로.
  `null`이면 "미분류" 회색 뱃지.
- **라이브전략 상태** 컬럼: 해당 market의 라이브 전략 중 `status`가
  `'running'` 또는 `'paused'`인 것을 찾는다(여러 개면 `'running'` 우선,
  실무상 코인당 동시에 하나만 돈다고 가정 — 강제하는 DB 제약은 없지만 이
  화면은 표시 전용이라 문제되지 않는다). 없으면 "라이브 전략 없음" 뱃지.
  있으면, 비교 대상 슬롯을 고른다: 현재장세 라벨이 상승/하락/횡보 중
  하나면 그 라벨의 슬롯, `null`(미분류)이면 "기본" 슬롯. 그 슬롯에 매핑이
  없으면 "매핑 없음"(비교 불가), 있으면 라이브 전략의 `source_run_id`와
  비교해 "동기화됨" / "전략 교체 필요" 뱃지.
- **4개 슬롯 셀**: 비어있으면 "설정" 버튼(`BacktestPickerDialog` 트리거),
  매핑돼 있으면 `source_run_id`로 백테스트 결과 요약(제목 또는 run_id
  일부, 수익률)을 보여주고 "변경"(`BacktestPickerDialog`, `excludeRunId`로
  현재 매핑 제외)/"제거"(`deleteRegimeStrategyMapping` 호출 후
  새로고침) 버튼. 매핑 요약에 쓸 백테스트 메타(제목/수익률)는
  `getBacktestRuns()`(마켓 필터 없이 전체 1회 호출) 결과에서
  `run_id`로 찾아 붙인다 — 매핑 저장 응답 자체에는 메타가 없으므로
  화면 로드 시 한 번에 조회.
- 매핑된 백테스트가 삭제된 경우(전체 목록에서 `run_id`를 못 찾음): 요약
  텍스트를 "삭제된 백테스트 결과"로 표시하되 셀 자체는 정상 동작(스냅샷된
  조건이 DB에 남아있으므로 매핑 자체는 유효 — "변경"/"제거"는 그대로 가능).

## 테스트 전략

- **`tests/test_trading_db.py`**: `upsert_regime_strategy_mapping`(신규 삽입/
  덮어쓰기 각각), `delete_regime_strategy_mapping`(존재/미존재 각각 반환값),
  `list_regime_strategy_mappings`(빈 상태, 여러 market/regime 혼재 상태)
- **`tests/test_main.py`**(또는 동일 관례의 API 테스트 파일): 신규 3개
  엔드포인트 — 정상 매핑 저장, 존재하지 않는 run_id, market 불일치,
  미지원 시간봉/지표, `MAJOR_MARKETS`/`REGIME_LIBRARY_SLOTS` 밖의 경로
  파라미터 각각 400/404 확인
- 기존 `test_main.py`의 `replace-strategy` 엔드포인트 테스트가 있다면
  `_validate_backtest_config_for_market` 추출 후에도 그대로 통과하는지 확인
  (동작 변화 없는 순수 리팩터이므로 회귀 없어야 함)
- 프론트엔드: 기존 관례대로 컴포넌트 자동화 테스트 없음 — 구현 후
  Playwright MCP로 `/strategy-library`에서 20행 렌더링 → 빈 슬롯 "설정"
  →피커에서 백테스트 선택 → 저장 후 셀에 요약 표시 → "변경"/"제거" →
  현재장세/라이브전략 상태 뱃지가 실제 `/regime`, `/live-strategies` 데이터와
  일치하는지 수동 검증
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체 통과,
  `cd frontend && npm run build` 성공

## 완료 기준

- `/strategy-library` 탭에서 20개 코인 × 4슬롯 매핑 표가 뜬다
- 빈 슬롯에서 "설정" → 같은 마켓의 백테스트 결과를 골라 저장 → 셀에
  반영된다
- 매핑된 슬롯에서 "변경"/"제거"가 정상 동작한다
- 각 행에 현재 ADX 장세와, 라이브 전략이 있으면 그 전략이 매핑과
  동기화됐는지 뱃지로 표시된다
- 신규 백엔드 유닛 테스트 통과, 기존 테스트 스위트 회귀 없음(특히
  `replace-strategy` 엔드포인트 리팩터 후 동작 불변)
- 브라우저로 위 흐름 수동 검증
