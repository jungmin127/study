"""
scripts/augment_search.py

기존 우승 오실레이터 전략(매수 BB_PERCENT_B(period=10)<0.2 / 매도 ATR_PCT(period=10)>1,
KRW-DOGE/1시간봉/2026-06-01~2026-08-06, run_id cfb41d8a..., 실제 수익률 +57.53%)을 베이스로
고정하고, 오실레이터 9종을 제외한 A그룹 지표 24개를 하나씩 추가 조건으로 얹어(매수/매도 양쪽,
AND/OR 양쪽) 수익률이 개선되는 조합이 있는지 탐색한다.

A그룹만 다룬다(대상마켓 OHLCV만으로 계산되는 지표 — 외부데이터/보조마켓 의존 없음, 캐시/네트워크
플레이키니스 없이 안정적으로 백그라운드 실행 가능). B그룹(펀딩비/공포탐욕/김치프리미엄/시장추세/
BTC·USDT상관계수)은 범위 밖 — 별도로 원하면 후속 스크립트로 확장.

추가 지표의 threshold는 해당 지표를 이 기간의 DOGE 캔들에 대해 직접 계산한 값 분포의
10/30/70/90 퍼센타일에서 뽑는다(10·30번째는 "<" 연산자, 70·90번째는 ">" 연산자) — 가격대/
거래대금처럼 절대 스케일이 코인마다 다른 지표를 고정 상수로 스윕할 수 없기 때문에, 이 실행
한정으로 실제 데이터 분포 기반 threshold를 쓴다(카탈로그 전체 정규화는 별도 로드맵 항목,
[[upbit-v1-catalog-normalization-roadmap]] 참고 — 이 스크립트는 그 정규화를 대신하지 않는다).

지표당 파라미터는 각 create_* 팩토리 함수의 기본값을 그대로 쓴다(params={}) — period 자체를
스윕하지는 않는다(범위를 결합방식 x threshold로 좁혀서 조합 수를 관리 가능하게 유지).

조합 수: 24개 지표 x 4개 threshold x 4개 결합방식(매수+AND/매수+OR/매도+AND/매도+OR) = 384개.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/augment_search.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from engine.cache import run_backtest_cached
from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest
from engine.sweep import DEFAULT_RISK_CONFIG
from trading.live_indicators import LIVE_INDICATOR_FACTORY
from upbit_data_service import get_candles

MARKET = "KRW-DOGE"
TIMEFRAME = "minutes60"
START = datetime(2026, 6, 1, tzinfo=timezone.utc)
END = datetime(2026, 8, 6, 23, 59, 59, tzinfo=timezone.utc)
CAPITAL = 1_000_000.0
BASELINE_RETURN_PCT = 57.53  # run_id cfb41d8a...의 실측값, 참고용 (사용자가 기억한 59.45%와는 약간 차이남)

BASE_BUY = {"indicator": "BB_PERCENT_B", "params": {"period": 10}, "operator": "<", "threshold": 0.2}
BASE_SELL = {"indicator": "ATR_PCT", "params": {"period": 10}, "operator": ">", "threshold": 1}

# 오실레이터 9종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT)과
# 포지션상대지표(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)를 제외한 A그룹 24개.
CANDIDATE_INDICATORS = [
    "SMA", "EMA", "WMA",
    "MACD_line", "MACD_signal",
    "BB_upper", "BB_lower", "BB_middle", "ATR",
    "OBV", "VOLUME_SMA", "VPIN", "TRADE_VALUE", "TRADE_VALUE_SMA",
    "MOMENTUM_PCT",
    "FIB_382", "FIB_500", "FIB_618",
    "PIVOT_P", "PIVOT_R1", "PIVOT_S1",
    "VPVR_POC", "VPVR_VAH", "VPVR_VAL",
]

QUANTILES = [(0.1, "<"), (0.3, "<"), (0.7, ">"), (0.9, ">")]


def build_threshold_candidates(df, indicator: str) -> list[dict]:
    """지표 값 분포의 10/30/70/90 퍼센타일에서 ConditionBlock 후보 4개를 만든다."""
    series = LIVE_INDICATOR_FACTORY[indicator](df).dropna()
    if series.empty:
        return []
    blocks = []
    for q, operator in QUANTILES:
        threshold = float(series.quantile(q))
        blocks.append({"indicator": indicator, "params": {}, "operator": operator, "threshold": threshold})
    return blocks


def build_variants(df) -> list[dict]:
    """지표 24개 x threshold 4개 x 결합방식 4개(매수+AND/매수+OR/매도+AND/매도+OR)의
    (buy_group, sell_group, label) 조합 리스트를 만든다."""
    variants = []
    for indicator in CANDIDATE_INDICATORS:
        for new_block in build_threshold_candidates(df, indicator):
            for side in ("buy", "sell"):
                for combine in ("AND", "OR"):
                    if side == "buy":
                        buy_group = {"type": combine, "conditions": [BASE_BUY, new_block]}
                        sell_group = {"type": "AND", "conditions": [BASE_SELL]}
                    else:
                        buy_group = {"type": "AND", "conditions": [BASE_BUY]}
                        sell_group = {"type": combine, "conditions": [BASE_SELL, new_block]}
                    label = (
                        f"{side}+{combine} {new_block['indicator']}"
                        f"{new_block['operator']}{new_block['threshold']:.4g}"
                    )
                    variants.append({"buy_group": buy_group, "sell_group": sell_group, "label": label})
    return variants


def _trade_sequence_key(trades: list[dict]) -> tuple:
    return tuple((t["entryTime"], t["exitTime"]) for t in trades)


def run_all(df, risk_config: dict, variants: list[dict]) -> list[dict]:
    results = []
    seen_sequences: set[tuple] = set()
    total = len(variants)
    for i, v in enumerate(variants):
        result = run_backtest(
            df, ConditionTreeStrategy, risk_config,
            {"buy_conditions": v["buy_group"], "sell_conditions": v["sell_group"]},
        )
        if not result["trades"]:
            continue
        key = _trade_sequence_key(result["trades"])
        if key in seen_sequences:
            continue
        seen_sequences.add(key)
        return_pct = (result["final_value"] - risk_config["initial_capital"]) / risk_config["initial_capital"] * 100
        results.append({**v, "return_pct": return_pct, "trades": result["trades"], "final_value": result["final_value"]})
        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"    진행 {i + 1}/{total}건", flush=True)
    return results


def main() -> None:
    print(f"[1] 캔들 조회: {MARKET} {TIMEFRAME} {START.date()} ~ {END.date()}", flush=True)
    df = get_candles(MARKET, TIMEFRAME, START, END)
    print(f"    캔들 수: {len(df)}", flush=True)
    if len(df) == 0:
        raise SystemExit("캔들 데이터가 없습니다.")

    print(f"[2] 베이스 조건: 매수 {BASE_BUY} / 매도 {BASE_SELL} (베이스라인 수익률 {BASELINE_RETURN_PCT:+.2f}%)", flush=True)

    variants = build_variants(df)
    print(f"[3] 조합 {len(variants)}개 생성(지표 {len(CANDIDATE_INDICATORS)}개 x threshold 4개 x 결합방식 4개)", flush=True)

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": CAPITAL}

    t0 = time.perf_counter()
    results = run_all(df, risk_config, variants)
    elapsed = time.perf_counter() - t0
    print(f"\n[4] 계산 완료: 거래 발생 {len(results)}건(dedup 후), {elapsed:.1f}초", flush=True)

    results.sort(key=lambda r: r["return_pct"], reverse=True)
    top_results = results[:15]

    print(f"\n[5] 상위 {len(top_results)}개를 백테스트 결과에 저장 중...", flush=True)
    saved_summaries = []
    for rank, r in enumerate(top_results, start=1):
        title = f"[Augment] 베이스(BB_PERCENT_B<0.2/ATR_PCT>1) + {r['label']}"
        diff = r["return_pct"] - BASELINE_RETURN_PCT
        description = (
            f"augment search - {MARKET}/{TIMEFRAME}/{START.date()}~{END.date()}, "
            f"수익률 {r['return_pct']:+.2f}% (베이스라인 {BASELINE_RETURN_PCT:+.2f}% 대비 {diff:+.2f}%p, 상위 {rank}위)"
        )
        saved = run_backtest_cached(
            df=df, strategy_cls=ConditionTreeStrategy, risk_config=risk_config,
            market=MARKET, timeframe=TIMEFRAME, start=START, end=END,
            strategy_params={"buy_conditions": r["buy_group"], "sell_conditions": r["sell_group"]},
            title=title, description=description,
        )
        print(f"  {rank:2d}. {r['return_pct']:+.2f}%  {r['label']}  run_id={saved['run_id'][:12]}...", flush=True)
        saved_summaries.append(
            {"rank": rank, "run_id": saved["run_id"], "return_pct": round(r["return_pct"], 2), "label": r["label"]}
        )

    result_json = {
        "baseline_return_pct": BASELINE_RETURN_PCT,
        "total_variants": len(variants),
        "variants_with_trades": len(results),
        "elapsed_sec": round(elapsed, 1),
        "saved": saved_summaries,
    }
    print("\n완료.")
    print(f"RESULT_JSON: {json.dumps(result_json, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
