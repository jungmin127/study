# 백테스트 결과 — 최대 단일 거래 기여도(%) 표시

## 배경 / 문제

백테스트 결과 목록(`/backtests`)과 상세 페이지는 총 수익률만 보여준다. 같은 총 수익률이라도 "여러 거래가 고르게 기여했는지" 또는 "극단적으로 높은 수익을 낸 거래 한두 건이 전체를 견인했는지"는 구분할 수 없다. 후자는 전략의 재현성·강건성을 판단할 때 위험 신호일 수 있다.

## 목표

각 백테스트 결과에 "최대 단일 거래 기여도(%)" — 총 이익(gross profit) 중 가장 큰 단일 거래가 차지하는 비중 — 를 계산해, 결과 목록 표와 상세 페이지 지표 카드에 표시한다.

## 비목표

- 표준편차(σ) 등 다른 분포 지표 (브레인스토밍 중 최대 단일 거래 기여도로 확정)
- 목록 표에서 이 컬럼 기준 정렬 (표시만, 정렬은 범위 밖)
- 라이브 전략(매매일지) 쪽 동일 지표 — 이번 스펙은 백테스트 결과에 한정

## 설계

### 계산 로직

`engine/metrics.py`에 헬퍼 함수 추가:

```python
def top_trade_contribution_pct(trades: list[dict]) -> float | None:
    """총 이익(gross profit) 중 가장 큰 단일 거래의 pnl이 차지하는 비중(%).
    이긴 거래가 없으면 None. 분모를 총수익률이 아니라 gross_profit으로 잡아,
    전략이 순손실이어도 '이긴 거래들 중 쏠림 정도'를 안정적으로 보여준다."""
    wins = [float(t.get("pnl", 0.0)) for t in trades if t.get("pnl", 0.0) > 0]
    if not wins:
        return None
    gross_profit = sum(wins)
    return max(wins) / gross_profit * 100.0 if gross_profit > 0 else None
```

### 상세 페이지 (`calculate_metrics()` 경유)

`calculate_metrics()`(engine/metrics.py:36)의 `if trades:` 블록에서 이미 `wins`/`gross_profit`을 계산하고 있으므로, 그 값을 재사용해 반환 dict에 `top_trade_contribution_pct` 필드를 추가한다. `_empty_metrics()`에도 `None`으로 추가.

`frontend/lib/types/eda.ts`의 `BacktestMetrics`에 `top_trade_contribution_pct: number | null` 추가.

`frontend/app/backtests/[runId]/page.tsx`의 `MetricsGrid` 타일 배열에 새 항목 추가:
```
{
  label: '최대거래 기여도',
  value: metrics.top_trade_contribution_pct != null ? `${metrics.top_trade_contribution_pct.toFixed(1)}%` : '-',
  tooltip: '총 이익 중 가장 큰 단일 거래가 차지하는 비중입니다. 높을수록 소수 거래에 수익이 쏠렸다는 뜻입니다.',
  icon: Percent,
}
```

### 목록 페이지

`backend/main.py::get_backtest_runs()`(509행)는 미청산 포지션 재평가를 위해 이미 각 run의 `r["trades"]` 전체를 메모리에 로드해 두고 있다. 별도 캐시 컬럼이나 스키마 변경 없이, 같은 루프 안에서 `top_trade_contribution_pct(r["trades"])`를 호출해 응답 dict에 필드로 추가하면 된다.

`frontend/lib/types/eda.ts`의 `BacktestRunSummary`에 `top_trade_contribution_pct: number | null` 추가.

`frontend/components/BacktestRunsTable.tsx`: "수익률(%)" 컬럼 바로 옆에 "최대거래 기여도(%)" 컬럼 추가, `run.top_trade_contribution_pct`를 `${value.toFixed(1)}%`로 표시하고 null이면 "-".

`frontend/components/BacktestRunCard.tsx`(모바일 카드 뷰): 같은 필드를 카드 안에 표시해 데스크톱 표와 정보 동등성을 유지한다.

## 테스트 계획

- `tests/test_metrics.py`(또는 기존 metrics 테스트 파일)에 `top_trade_contribution_pct()` 단위 테스트: 이긴 거래 없음 → None, 균등 분포 거래들 → 낮은 값, 한 거래가 압도적인 케이스 → 90%대 값.
- `tests/test_backend.py`: `GET /api/v1/backtests` 응답에 `top_trade_contribution_pct` 필드가 포함되는지 확인.
