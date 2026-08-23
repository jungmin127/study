"""
scripts/regime_backtest.py

engine.regime_detector.compute_regime_probs_series()가 실제로 쓸모 있는지 과거 캔들로
검증한다. 규칙기반 결정론적 함수라 학습 없이 지금 바로 확인 가능. 평가 로직 자체는
backend/regime_service.py의 evaluate_market()로 이전됐다(GET /api/v1/regime/backtest
웹 API와 공유). 설계 문서:
docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md,
docs/superpowers/specs/2026-08-23-regime-detector-web-dashboard-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.regime_service import evaluate_market
from engine.regime_detector import CATEGORY_REFERENCE_SCORES, half_life_bars_for_timeframe

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
TIMEFRAME = "minutes60"
VALIDATION_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
VALIDATION_END = datetime.now(timezone.utc)


def main() -> None:
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    print(f"half_life_bars={half_life_bars:.1f}, timeframe={TIMEFRAME}")

    for market in MARKETS:
        print(f"\n=== {market} ({TIMEFRAME}) ===")
        result = evaluate_market(market, TIMEFRAME, VALIDATION_START, VALIDATION_END)
        confusion = result["confusion"]
        actual_totals = result["actual_totals"]
        correlation = result["correlation"]
        print(f"  n_bars={result['n_bars']}")

        print("  [예측 카테고리별 hit-rate]")
        for label in CATEGORY_REFERENCE_SCORES:
            row = confusion[label]
            total = sum(row.values())
            if total == 0:
                print(f"    {label}: 샘플 없음")
                continue
            hit = row[label]
            hit_rate = hit / total * 100
            print(f"    {label}: {hit}/{total} 적중 ({hit_rate:.1f}%)")

        if correlation is None:
            print("  [확률벡터-실현수익률 상관계수] 계산 불가(샘플 부족)")
        else:
            print(f"  [확률벡터-실현수익률 상관계수] {correlation:.3f}")

        print("  [confusion matrix] 행=예측, 열=실제")
        header = "    " + "예측\\실제".ljust(10) + "".join(label.ljust(10) for label in CATEGORY_REFERENCE_SCORES)
        print(header)
        for predicted_label in CATEGORY_REFERENCE_SCORES:
            row = confusion[predicted_label]
            row_str = "    " + predicted_label.ljust(10) + "".join(
                str(row[actual_label]).ljust(10) for actual_label in CATEGORY_REFERENCE_SCORES
            )
            print(row_str)

        total_samples = sum(actual_totals.values())
        print(f"  [실제 카테고리 분포(전체 샘플 {total_samples}건 기준)]")
        for label in CATEGORY_REFERENCE_SCORES:
            n = actual_totals[label]
            pct = n / total_samples * 100 if total_samples else 0.0
            print(f"    {label}: {n} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
