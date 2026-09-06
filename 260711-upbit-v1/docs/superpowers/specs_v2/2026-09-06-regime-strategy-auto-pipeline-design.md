# 코인별 장세 전략 자동 발굴 파이프라인 — 설계 스펙

## 배경

사용자가 지금까지 수동으로 반복해온 과정:

1. `/regime` 탭에서 메이저 코인 20개의 현재 장세를 확인
2. 코인 하나(예: 이더리움)를 골라, 가장 최근의 연속된 상승/횡보/하락 구간을 눈으로 찾는다
   (구간이 너무 짧으면 무용지물이므로 기간을 스스로 조정)
3. 해당 기간에 grid search를 돌려 수익률이 가장 높은 전략을 찾는다(단, 거래횟수가
   너무 적은 결과는 제외)
4. 결과 제목에 프리픽스(상승/횡보/하락)를 달아 저장
5. `/strategy-library`에서 해당 코인의 해당 슬롯에 그 결과를 매핑
6. 최종 확인 후 라이브 전략을 만들거나 자동스왑을 켠다

이 스펙은 1~5단계(장세 탐지 → 기간 조정 → grid search → 후보 선정 → TP/SL 부착 →
`regime_strategy_library` 매핑)를 코인 하나를 지정하면 끝까지 자동으로 수행하는
CLI 스크립트 + 재사용 가능한 스킬 래퍼를 만든다. 6단계(라이브 배포)는 실거래
개입이라 의도적으로 자동화 범위 밖에 둔다 — 브레인스토밍에서 사용자가 확정.

**트레일링 스탑(수익 +N% 달성 시 손절가를 본절 이상으로 올리는 기능)은 이 스펙의
범위가 아니다** — 정적 TP/SL과 달리 "고점 대비 상태"를 추적해야 하는 새로운
스테이트풀 기능이라 성격이 다르고, 실거래 개입 리스크가 높아 별도 세션에서
브레인스토밍하기로 확정.

**이미 있어서 재사용하는 것**:
- `backend/regime_adx_service.compute_adx_regime_history()` — 코인의 전체 기간
  ADX 상승/하락/횡보 연속 구간(segments) 계산(2단계, `/regime` 탭이 쓰는 것과 동일)
- `scripts/grid_search.py`의 `build_condition_grid()`/`compute_grid_results_parallel()`/
  `_fetch_backtest_dataframe()`/`_check_candle_warmup()` — grid search 연산 자체
- `engine/grid_search_pool.py`의 `INDICATOR_POOL_SPECS`(6개 카테고리)/`SELL_ONLY`
  (STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS — 이미 조건트리 지표로 존재,
  포지션 진입가 대비 수익률로 실시간 ticker 손절/익절 루프에서 평가됨)
- `engine/cache.run_backtest_cached()` — 결과 저장(제목/설명 포함)
- `trading/db.py`의 `upsert_regime_strategy_mapping()` — 3단계(전략 라이브러리)가
  이미 구현한 매핑 저장 함수

## 목표

1. `scripts/regime_strategy_pipeline.py --market KRW-ETH --history-start 2026-01-01`
   실행 한 번으로 하락/횡보/상승 3개 장세 각각에 대해:
   - 가장 최근 연속 구간을 찾고, 너무 짧으면 기간을 자동으로 넓히고
   - 오실레이터를 포함한 6개 카테고리 전체로 grid search를 돌리고
   - 거래횟수가 충분한 후보 중 수익률이 가장 높은 것을 고르고
   - 거기에 손절 5%/익절 8% OR 조건을 부착해 재검증하고
   - 최종 결과를 저장하고 `regime_strategy_library`에 매핑
2. 다른 코인에도 `--market`만 바꿔 재사용 가능하게 만든다(재사용 가능한 스크립트 +
   "코인명만 주면 실행해주는" 얇은 Claude 스킬)

## 비범위

- 트레일링 스탑(위에서 설명) — 별도 세션
- 라이브 전략 생성/자동스왑 토글 — 사용자가 `/strategy-library`와 라이브 전략 관리
  화면에서 최종 확인 후 수동으로 진행(최종 게이트 유지)
- "기본" 슬롯(장세 무관 장기 우수 전략) 자동 생성 — 사용자의 원래 수동 절차(1~7단계)에
  없던 항목이라 이번 자동화에도 포함하지 않는다. 필요해지면 후속 요청으로 처리
- 중단된 실행의 재개(resume) — 중단되면 처음부터 다시 실행(코인당 실행이 자주
  반복되는 워크플로우가 아니라 저장해둘 실익이 낮음, YAGNI)
- `MAJOR_MARKETS`(20개) 외 마켓, `minutes60` 외 타임프레임 — 기존 ADX 엔진/전략
  라이브러리와 동일한 제약을 그대로 물려받는다

## 설계

### 1. 진입점 — `scripts/regime_strategy_pipeline.py` (신규)

```
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
     --market KRW-ETH --history-start 2026-01-01 \
     [--capital 10000000] [--min-days 10] [--stop-loss-pct -5] [--take-profit-pct 8] \
     [--candidate-pool 20]
```

인자:
- `--market`(필수): `engine.regime_adx_constants.MAJOR_MARKETS`에 속해야 함
- `--history-start`(필수): 이 날짜 이후에 시작하는 세그먼트만 고려. 기간 조정 시에도
  이 날짜보다 앞으로는 당기지 않는다
- `--capital`(기본 `10_000_000`, `engine.sweep.DEFAULT_RISK_CONFIG`와 동일값)
- `--min-days`(기본 `10`): 세그먼트가 이보다 짧으면 시작일을 당겨 채운다
- `--stop-loss-pct`(기본 `-5`)/`--take-profit-pct`(기본 `8`): TP/SL 증강에 쓸 고정값
- `--candidate-pool`(기본 `20`): 거래횟수 필터를 통과한 후보 중 TP/SL 증강을
  시도해볼 상위 개수(수익률 내림차순)

### 2. 장세 세그먼트 탐지 + 기간 조정

```python
TIMEFRAME = "minutes60"

def select_target_segments(market: str, history_start: datetime) -> dict[str, dict | None]:
    """라벨(하락/횡보/상승) -> 가장 최근 세그먼트({"start", "end", ...}) 매핑.
    history_start 이후 시작하는 세그먼트가 없는 라벨은 None."""
    history = compute_adx_regime_history(market, TIMEFRAME)
    by_label: dict[str, dict] = {}
    for seg in history["segments"]:
        if datetime.fromisoformat(seg["start"]) < history_start:
            continue
        label = seg["label"]
        if label not in by_label or seg["end"] > by_label[label]["end"]:
            by_label[label] = seg
    return {label: by_label.get(label) for label in ("하락", "횡보", "상승")}


def adjust_window(seg: dict, min_days: int, history_start: datetime) -> tuple[datetime, datetime]:
    """세그먼트 길이가 min_days 미만이면 start를 당겨 채운다. history_start보다
    앞으로는 당기지 않는다. end는 항상 세그먼트의 end 그대로."""
    end = datetime.fromisoformat(seg["end"])
    start = datetime.fromisoformat(seg["start"])
    min_start = end - timedelta(days=min_days)
    if start > min_start:
        start = max(min_start, history_start)
    return start, end
```

### 3. Grid search 실행 (기존 함수 재사용)

```python
from scripts.grid_search import (
    build_condition_grid, compute_grid_results_parallel,
    _check_candle_warmup, _wrap_condition,
)
from backend.main import _fetch_backtest_dataframe
from engine.sweep import DEFAULT_RISK_CONFIG

ALL_CATEGORIES = ["오실레이터", "추세", "가격대", "거래량", "거래대금", "시장 심리"]

def run_grid_for_window(market: str, start: datetime, end: datetime, capital: float) -> dict:
    """grid search를 실행하고, 이후 단계(후보 재검증/최종 저장)가 그대로 재사용할
    df/risk_config까지 함께 반환한다(같은 df로 캔들을 두 번 조회하지 않기 위함)."""
    pool = {"categories": ALL_CATEGORIES, "excluded_indicators": []}
    buy_conditions, sell_conditions = build_condition_grid(pool, market=market)
    df = _fetch_backtest_dataframe(
        market, TIMEFRAME, start, end,
        {"type": "AND", "conditions": buy_conditions},
        {"type": "AND", "conditions": sell_conditions},
    )
    _check_candle_warmup(df, buy_conditions, sell_conditions)
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": capital}
    results = compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config)
    return {"df": df, "risk_config": risk_config, "results": results}
```

`_fetch_backtest_dataframe`가 `HTTPException`을 던질 수 있고(캔들/보조데이터 조회
실패), `_check_candle_warmup`이 `SystemExit`를 던질 수 있다(워밍업 부족) — 둘 다
호출부(라벨 단위 루프)에서 잡아 해당 슬롯만 스킵한다.

### 4. 최소 거래횟수 필터 + 후보 선정

```python
import math

def min_trades_for_days(period_days: float) -> int:
    return max(3, math.ceil(period_days / 5))


def top_candidates(results: list[dict], min_trades: int, pool_size: int) -> list[dict]:
    """거래횟수 미달 결과를 먼저 버리고, 남은 것 중 수익률 내림차순 상위 pool_size개.
    dedup_top_results와 동일한 거래시퀀스 dedup 로직을 재사용하되 필터를 먼저 적용."""
    filtered = [r for r in results if len(r["trades"]) >= min_trades]
    return dedup_top_results(filtered, pool_size)  # scripts.grid_search.dedup_top_results
```

### 5. TP/SL 증강 + 후보 순회

```python
def augment_with_tp_sl(sell_group: dict, stop_loss_pct: float, take_profit_pct: float) -> dict:
    return {
        "type": "OR",
        "conditions": [
            sell_group,
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": stop_loss_pct},
            {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": take_profit_pct},
        ],
    }


def pick_final_strategy(
    df, candidates: list[dict], risk_config: dict, min_trades: int,
    stop_loss_pct: float, take_profit_pct: float,
) -> dict | None:
    """후보를 수익률 내림차순으로 순회하며 TP/SL 증강 후에도 거래횟수를 만족하는
    첫 번째를 채택한다. 전부 실패하면 None."""
    for cand in candidates:
        buy_group = _wrap_condition(cand["buy_block"], None, "AND")
        base_sell_group = _wrap_condition(cand["sell_block"], None, "AND")
        augmented_sell = augment_with_tp_sl(base_sell_group, stop_loss_pct, take_profit_pct)
        result = run_backtest(
            df, ConditionTreeStrategy, risk_config,
            {"buy_conditions": buy_group, "sell_conditions": augmented_sell},
        )
        if len(result["trades"]) >= min_trades:
            return_pct = (result["final_value"] - risk_config["initial_capital"]) / risk_config["initial_capital"] * 100
            return {
                "buy_conditions": buy_group, "sell_conditions": augmented_sell,
                "return_pct": return_pct, "trades": result["trades"],
                "raw_return_pct": cand["return_pct"], "raw_trade_count": len(cand["trades"]),
            }
    return None
```

### 6. 저장 + 라이브러리 매핑

```python
def save_and_map(
    market: str, regime: str, start: datetime, end: datetime, final: dict,
    df, risk_config: dict, stop_loss_pct: float, take_profit_pct: float,
) -> str:
    title = f"[{regime}] {market} {start.date()}~{end.date()} 그리드+TP{take_profit_pct}%/SL{abs(stop_loss_pct)}%"
    description = (
        f"regime_strategy_pipeline - {market}/{TIMEFRAME}/{start.date()}~{end.date()}, "
        f"원본 수익률 {final['raw_return_pct']:+.2f}%({final['raw_trade_count']}건) -> "
        f"TP/SL 부착 후 {final['return_pct']:+.2f}%({len(final['trades'])}건)"
    )
    saved = run_backtest_cached(
        df=df, strategy_cls=ConditionTreeStrategy, risk_config=risk_config,
        market=market, timeframe=TIMEFRAME, start=start, end=end,
        strategy_params={"buy_conditions": final["buy_conditions"], "sell_conditions": final["sell_conditions"]},
        title=title, description=description,
    )
    trading_db.upsert_regime_strategy_mapping(
        market, regime, source_run_id=saved["run_id"], timeframe=TIMEFRAME,
        buy_conditions_json=json.dumps(final["buy_conditions"]),
        sell_conditions_json=json.dumps(final["sell_conditions"]),
    )
    return saved["run_id"]
```

### 7. 전체 오케스트레이션 + 출력

```python
def main() -> None:
    args = parse_args()
    history_start = datetime.strptime(args.history_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    segments = select_target_segments(args.market, history_start)

    summary = []
    for regime, seg in segments.items():
        if seg is None:
            summary.append({"regime": regime, "status": "skipped", "reason": "탐지된 구간 없음"})
            continue
        try:
            start, end = adjust_window(seg, args.min_days, history_start)
            period_days = (end - start).days
            min_trades = min_trades_for_days(period_days)

            grid = run_grid_for_window(args.market, start, end, args.capital)
            candidates = top_candidates(grid["results"], min_trades, args.candidate_pool)
            if not candidates:
                summary.append({"regime": regime, "status": "skipped", "reason": "거래횟수 조건을 만족하는 후보 없음"})
                continue

            final = pick_final_strategy(
                grid["df"], candidates, grid["risk_config"], min_trades,
                args.stop_loss_pct, args.take_profit_pct,
            )
            if final is None:
                summary.append({"regime": regime, "status": "skipped", "reason": "TP/SL 부착 후 거래횟수 조건을 만족하는 후보 없음"})
                continue

            run_id = save_and_map(
                args.market, regime, start, end, final,
                grid["df"], grid["risk_config"], args.stop_loss_pct, args.take_profit_pct,
            )
            summary.append({
                "regime": regime, "status": "mapped", "run_id": run_id,
                "period": f"{start.date()}~{end.date()}",
                "return_pct": round(final["return_pct"], 2),
                "trade_count": len(final["trades"]),
            })
        except (HTTPException, SystemExit) as exc:
            summary.append({"regime": regime, "status": "failed", "reason": str(exc)})

    print_summary_table(summary)
    print(f"RESULT_JSON: {json.dumps({'market': args.market, 'segments': summary}, ensure_ascii=False)}")
```

라벨 단위 `try/except`로 한 라벨의 실패가 나머지 라벨 처리를 막지 않는다(daemon.py/
`regime_autoswap.py`가 이미 쓰는 "예외는 로그만, 나머지는 계속" 원칙과 동일한 패턴).

### 8. 스킬 래퍼 — `.claude/skills/regime-strategy-pipeline/SKILL.md` (신규, 한국어)

```markdown
---
name: regime-strategy-pipeline
description: 코인 하나를 지정하면 장세(하락/횡보/상승)별 grid search를 자동으로
  돌려 TP/SL을 부착한 최종 전략을 전략 라이브러리에 매핑한다. "장세전략 파이프라인
  <코인>", "<코인> 전략 자동발굴" 같은 요청에 사용한다.
---

1. 사용자가 준 코인명을 마켓코드(`KRW-XXX`)로 변환한다(기존 마켓 조회 API/
   `MAJOR_MARKETS` 참고). `MAJOR_MARKETS`에 없는 코인이면 지원하지 않는다고 안내한다.
2. 로컬에서 이미 실행 중인 grid search(웹 탭 `/grid-search`의 job 큐)가 있는지
   사용자에게 확인한다 — 있으면 리소스가 겹치니 끝난 뒤 실행하라고 안내한다.
3. `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py
   --market <마켓코드> --history-start <사용자가 지정한 날짜, 기본 이번 달 1일>`을
   `run_in_background: true`로 실행한다(코인 하나당 수 시간~십수 시간 소요될 수 있음).
4. 완료되면 `RESULT_JSON:` 라인을 파싱해 라벨별 결과(매핑됨/스킵됨/실패, 수익률,
   거래횟수, 기간)를 표로 정리해 보여준다.
5. 마지막에 반드시 안내: "라이브 배포는 자동화 범위 밖입니다 — `/strategy-library`에서
   결과를 확인하고, 만족스러우면 라이브 전략을 만들거나 기존 전략에 자동스왑을
   켜주세요."
```

## 에러 처리 / 엣지 케이스

- **history_start 이후 해당 라벨 세그먼트가 아예 없음**: 그 슬롯만 `skipped`로
  요약에 기록, 나머지 라벨은 계속 처리
- **워밍업 부족**(조정된 기간의 캔들 수가 그리드 최대 요구 봉수보다 적음):
  `_check_candle_warmup`의 `SystemExit`를 잡아 그 슬롯만 실패 처리
- **캔들/보조데이터 조회 실패**(`_fetch_backtest_dataframe`의 `HTTPException`):
  그 슬롯만 실패 처리, 나머지 라벨 계속
- **거래횟수 필터 통과 후보가 0개**, 또는 **TP/SL 부착 후 전부 거래횟수 미달**:
  그 슬롯만 `skipped`, 사유를 요약에 남긴다
- **동시 실행**: 웹 탭 grid search job 큐(`backend/grid_search_service.py`)와
  이 스크립트는 서로 다른 실행 경로(subprocess job 큐 vs in-process)라 서로를
  막지는 않지만, 같은 머신에서 멀티프로세싱 워커(6개)를 동시에 두 번 띄우면
  리소스가 겹친다 — 스킬이 사전에 사용자에게 확인하는 절차로 완화(강제 차단은
  하지 않음, YAGNI)
- **중단 후 재실행**: 이미 매핑된 라벨도 재실행 시 덮어쓴다(`upsert_regime_strategy_mapping`이
  기존에 이미 idempotent upsert로 구현돼 있음) — 재개 로직 불필요

## 테스트 전략

- **`tests/test_regime_strategy_pipeline.py`**(신규):
  - `adjust_window`: 세그먼트가 `min_days` 이상(변화 없음)/미만(시작일이 당겨짐)/
    당긴 결과가 `history_start`보다 이전이 되는 경우(`history_start`로 클램프) 각각
  - `min_trades_for_days`: 경계값(정확히 5일 배수, 5일 미만은 최소 3 유지)
  - `select_target_segments`: `compute_adx_regime_history`를 monkeypatch해 (a)
    라벨별 최신 세그먼트를 정확히 고르는지(같은 라벨 세그먼트가 여러 개일 때 가장
    최근 것), (b) `history_start` 이전 세그먼트는 제외되는지, (c) 특정 라벨이
    아예 없으면 `None`
  - `augment_with_tp_sl`: 원래 sell_group이 보존되고 새 블록 2개가 OR로 붙는지
  - `pick_final_strategy`: `run_backtest`를 monkeypatch해 (a) 첫 후보가 증강 후에도
    거래횟수 통과 → 채택 후 나머지 후보 시도 안 함, (b) 첫 후보 탈락 → 다음 후보로
    넘어감, (c) 전부 탈락 → `None`
  - `main()`의 라벨 단위 예외 격리: 한 라벨에서 `SystemExit`/`HTTPException`이 나도
    나머지 라벨이 계속 처리되는지(무거운 실제 계산은 monkeypatch로 대체)
- 실제 grid search 연산(`compute_grid_results_parallel` 등)은 기존
  `tests/test_grid_search.py`가 이미 커버 — 이 스펙에서는 재검증하지 않는다
- 무거운 실제 실행(코인 1개 전체 파이프라인)은 자동화 테스트로 돌리지 않는다
  (몇 시간 걸림) — 사용자가 실제 코인으로 CLI 수동 실행해 `/strategy-library`
  반영을 확인
- `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q` 전체 통과,
  기존 테스트 스위트 회귀 없음

## 완료 기준

- `python scripts/regime_strategy_pipeline.py --market KRW-ETH --history-start 2026-01-01`
  실행 시 하락/횡보/상승 각 라벨에 대해 세그먼트 탐지 → 기간 조정 → grid search
  (6개 카테고리) → 거래횟수 필터 → TP8%/SL5% 증강 → `regime_strategy_library` 매핑까지
  자동 완료
- 콘솔에 라벨별 요약 표 + `RESULT_JSON:` 라인 출력
- `/strategy-library` 탭에서 KRW-ETH 행의 하락/횡보/상승 슬롯에 매핑 결과가 반영된
  것을 확인 가능(브라우저 수동 검증)
- `--market`을 바꿔 다른 `MAJOR_MARKETS` 코인에도 동일하게 재사용 가능
- 신규 스킬 파일로 "코인명만 주면" 트리거 가능
- 신규 유닛 테스트 전부 통과, 기존 테스트 스위트 회귀 없음
