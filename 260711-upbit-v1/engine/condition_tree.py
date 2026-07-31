"""
engine/condition_tree.py

JSON ConditionGroup 트리(재귀적 AND/OR, 중첩 괄호 묶음 지원)를 평가·검증한다.

ConditionGroup = {"type": "AND" | "OR", "conditions": [ConditionBlock | ConditionGroup, ...]}
ConditionBlock = {"indicator": str, "params": dict, "operator": str, "threshold": float}

C:\\Users\\jungm\\project\\backtesting_1의 backend/app/engine/strategy_builder.py를 참고해
포팅했다. 원본과 다른 점: 원본은 조건 트리로부터 매번 동적 bt.Strategy 클래스를 만들지만,
이 프로젝트의 캐시 키(engine/cache.compute_cache_key)는 inspect.getsource(strategy_cls)에
의존하므로 동적 클래스를 쓰면 캐싱이 깨진다. 그래서 이 모듈은 순수 평가/검증 함수만 제공하고,
실제 bt.Strategy는 engine/condition_strategy.py의 정적 클래스가 담당한다.
"""
from __future__ import annotations

import backtrader as bt

from engine.indicators import INDICATOR_FACTORY

# 캔들 데이터로 미리 계산하는 bt.Indicator가 아니라, 포지션이 열려야만 알 수 있는
# 진입가 대비 수익률(%)을 값으로 쓰는 지표. eval_group에 position_return_pct로 전달된다.
POSITION_RELATIVE_INDICATORS = {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS"}


def indicator_key(indicator: str, params: dict) -> str:
    """지표 이름 + 파라미터 조합의 고유 키 생성 (같은 지표를 여러 블록이 참조해도 한 번만 생성)."""
    sorted_params = sorted(params.items())
    return f"{indicator}__{sorted_params}"


def collect_blocks(group: dict) -> list[dict]:
    """ConditionGroup에서 모든 ConditionBlock을 재귀적으로 수집."""
    blocks: list[dict] = []
    for item in group.get("conditions", []):
        if "indicator" in item:
            blocks.append(item)
        elif "type" in item:
            blocks.extend(collect_blocks(item))
    return blocks


def get_indicator_value(indicator_name: str, obj: bt.Indicator) -> float:
    """지표 종류에 따라 현재 바 값을 추출 (다중 라인 지표는 대표 라인을 지정해야 함)."""
    if indicator_name == "MACD_line":
        return float(obj.macd[0])
    elif indicator_name == "MACD_signal":
        return float(obj.signal[0])
    elif indicator_name == "BB_upper":
        return float(obj.top[0])
    elif indicator_name == "BB_lower":
        return float(obj.bot[0])
    elif indicator_name == "BB_middle":
        return float(obj.mid[0])
    elif indicator_name == "STOCH_K":
        return float(obj.percK[0])
    elif indicator_name == "STOCH_D":
        return float(obj.percD[0])
    elif indicator_name == "PIVOT_P":
        return float(obj.lines.p[0])
    elif indicator_name == "PIVOT_R1":
        return float(obj.lines.r1[0])
    elif indicator_name == "PIVOT_S1":
        return float(obj.lines.s1[0])
    elif indicator_name == "VPVR_POC":
        return float(obj.lines.poc[0])
    elif indicator_name == "VPVR_VAH":
        return float(obj.lines.vah[0])
    elif indicator_name == "VPVR_VAL":
        return float(obj.lines.val[0])
    else:
        return float(obj[0])


def apply_operator(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    elif operator == "<":
        return value < threshold
    elif operator == ">=":
        return value >= threshold
    elif operator == "<=":
        return value <= threshold
    elif operator == "==":
        return value == threshold
    return False


def eval_group(
    group: dict,
    indicators: dict[str, bt.Indicator],
    position_return_pct: float | None = None,
    position_holding_bars: int | None = None,
) -> bool:
    """ConditionGroup을 재귀적으로 평가해 bool 반환. indicators는 indicator_key -> bt.Indicator 매핑.
    position_return_pct는 포지션 진입가 대비 현재 수익률(%)로 STOP_LOSS_PCT/TAKE_PROFIT_PCT 평가에,
    position_holding_bars는 포지션 보유 봉수로 HOLDING_PERIOD_BARS 평가에 쓰인다. 포지션이 없어
    해당 값이 None이면 그 블록은 False로 처리한다."""
    group_type = group.get("type", "AND")
    conditions = group.get("conditions", [])

    if not conditions:
        return False

    results: list[bool] = []
    for item in conditions:
        if "indicator" in item:
            if item["indicator"] == "HOLDING_PERIOD_BARS":
                if position_holding_bars is None:
                    results.append(False)
                else:
                    results.append(
                        apply_operator(position_holding_bars, item["operator"], float(item["threshold"]))
                    )
                continue
            if item["indicator"] in POSITION_RELATIVE_INDICATORS:
                if position_return_pct is None:
                    results.append(False)
                else:
                    results.append(apply_operator(position_return_pct, item["operator"], float(item["threshold"])))
                continue
            key = indicator_key(item["indicator"], item.get("params", {}))
            if key not in indicators:
                results.append(False)
                continue
            value = get_indicator_value(item["indicator"], indicators[key])
            results.append(apply_operator(value, item["operator"], float(item["threshold"])))
        elif "type" in item:
            results.append(eval_group(item, indicators, position_return_pct, position_holding_bars))

    return all(results) if group_type == "AND" else any(results)


def find_unknown_indicators(group: dict) -> list[str]:
    """INDICATOR_FACTORY와 POSITION_RELATIVE_INDICATORS 어디에도 없는 지표 키를 찾아 반환(중복 제거, 정렬)."""
    unknown = {
        b["indicator"]
        for b in collect_blocks(group)
        if b["indicator"] not in INDICATOR_FACTORY and b["indicator"] not in POSITION_RELATIVE_INDICATORS
    }
    return sorted(unknown)


def is_empty(group: dict) -> bool:
    return len(group.get("conditions", [])) == 0


def max_required_period(group: dict) -> int:
    """조건 트리에 등장하는 모든 숫자 파라미터 중 최댓값을 반환 — 지표 계산에 필요한
    최소 워밍업 봉 수의 근사치로 쓴다(예: SMA period=200이면 최소 200봉 필요)."""
    periods = [0]
    for block in collect_blocks(group):
        for value in block.get("params", {}).values():
            try:
                periods.append(int(value))
            except (TypeError, ValueError):
                continue
    return max(periods)


AUX_MARKET_INDICATORS: dict[str, str] = {
    "MARKET_TREND": "KRW-BTC",
    "BTC_CORRELATION": "KRW-BTC",
    "USDT_CORRELATION": "KRW-USDT",
    "KOREA_PREMIUM": "KRW-USDT",
}


def required_aux_markets(group: dict) -> set[str]:
    """조건 트리가 대상 마켓이 아닌 다른 마켓(KRW-BTC, KRW-USDT 등) 캔들이 필요한 지표를
    포함하는지 확인해, 필요한 마켓 코드 집합을 반환한다. backend가 이 집합을 보고 각 마켓의
    캔들을 추가로 조회해 병합할지 정한다. 여러 지표가 같은 마켓을 요구하면(예: MARKET_TREND와
    BTC_CORRELATION이 둘 다 KRW-BTC) 한 번만 등장한다."""
    return {
        AUX_MARKET_INDICATORS[b["indicator"]]
        for b in collect_blocks(group)
        if b["indicator"] in AUX_MARKET_INDICATORS
    }


__all__ = [
    "POSITION_RELATIVE_INDICATORS",
    "indicator_key",
    "collect_blocks",
    "get_indicator_value",
    "apply_operator",
    "eval_group",
    "find_unknown_indicators",
    "is_empty",
    "max_required_period",
    "AUX_MARKET_INDICATORS",
    "required_aux_markets",
]
