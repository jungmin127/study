"""trading/db.py의 CRUD 함수를 테스트할 때 FK 제약을 만족시키기 위한 공용 픽스처."""
from __future__ import annotations

import uuid


def insert_live_strategy(db_module, **overrides) -> str:
    """유효한 live_strategies 행을 만들고 id를 반환한다. positions/circuit_breaker_state/
    daily_performance는 전부 live_strategy_id를 외래키로 참조하므로, 이 헬퍼 없이는
    그 테이블들의 CRUD 테스트를 작성할 수 없다."""
    defaults = {
        "id": str(uuid.uuid4()),
        "source_run_id": None,
        "market": "KRW-BTC",
        "timeframe": "minutes60",
        "buy_conditions_json": "{}",
        "sell_conditions_json": "{}",
        "risk_config_json": "{}",
        "current_capital": 100000.0,
        "status": "running",
        "manual_pause": 0,
        "started_at": None,
    }
    defaults.update(overrides)

    conn = db_module._connect()
    try:
        conn.execute(
            "INSERT INTO live_strategies "
            "(id, source_run_id, market, timeframe, buy_conditions_json, sell_conditions_json, "
            "risk_config_json, current_capital, status, manual_pause, started_at) "
            "VALUES (:id, :source_run_id, :market, :timeframe, :buy_conditions_json, "
            ":sell_conditions_json, :risk_config_json, :current_capital, :status, :manual_pause, "
            ":started_at)",
            defaults,
        )
        conn.commit()
    finally:
        conn.close()
    return defaults["id"]
