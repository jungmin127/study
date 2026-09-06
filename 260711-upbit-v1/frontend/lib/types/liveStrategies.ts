import type { ConditionGroup } from '@/lib/types/strategy';

export type LiveStrategyStatus = 'draft' | 'running' | 'paused' | 'stopped';
export type PositionSizingMode = 'fixed' | 'percent';
export type OrderExecutionMode = 'market' | 'limit' | 'limit_timeout';
export type ManualInterventionPolicy = 'all_stop' | 'acknowledge_and_continue';

export interface LiveStrategyRiskConfig {
  position_sizing_mode: PositionSizingMode;
  position_sizing_value: number;
  max_position_per_market: number;
  order_execution_mode: OrderExecutionMode;
  order_timeout_sec: number;
  manual_intervention_policy: ManualInterventionPolicy;
  daily_loss_limit_pct: number;
  consecutive_loss_limit: number;
}

export interface CreateLiveStrategyRequest {
  source_run_id: string | null;
  market: string;
  timeframe: string;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
}

export interface LiveStrategyOpenPosition {
  entry_price: number;
  entry_qty: number;
  entry_time: string;
  unrealized_pnl_pct: number | null;
}

export interface CapitalAdjustment {
  id: string;
  adjusted_at: string;
  previous_capital: number;
  new_capital: number;
  delta: number;
}

export interface LiveStrategy {
  id: string;
  source_run_id: string | null;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  current_capital: number | null;
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  open_position: LiveStrategyOpenPosition | null;
  last_buy_at: string | null;
  last_sell_at: string | null;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  risk_config: LiveStrategyRiskConfig;
  capital_adjustments: CapitalAdjustment[];
  auto_swap_enabled: boolean;
  active_regime: '하락' | '횡보' | '상승' | '기본' | null;
}

export interface BacktestConfig {
  market: string;
  timeframe: string;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
}
