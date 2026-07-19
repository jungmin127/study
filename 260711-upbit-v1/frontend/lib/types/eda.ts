import type { ConditionGroup } from './strategy';

export interface SweepResult {
  run_id: string;
  signal_set_name: string;
  is_combined: boolean;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  swept_at: string;
}

export interface Combo {
  signal_set_name: string;
  is_combined: boolean;
  market: string;
  timeframe: string;
}

export interface EquityPoint {
  timestamp: string;
  value: number;
}

export interface Trade {
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  returnRate: number;
  holdingPeriod: number;
  pnl: number;
  forceClosed: boolean;
}

export interface BacktestDetail {
  final_value: number;
  sharpe: number | null;
  max_drawdown: number | null;
  equity_curve: EquityPoint[];
  trades: Trade[];
}

export interface Market {
  market: string;
  korean_name: string;
  english_name: string;
}

export interface IndicatorParamDef {
  key: string;
  label: string;
  default: number;
}

export interface IndicatorCatalogItem {
  value: string;
  label: string;
  category: string;
  params: IndicatorParamDef[];
  description: string;
  example: string;
}

export interface RunBacktestRequest {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  initial_capital: number;
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
  title?: string | null;
  description?: string | null;
}

export interface RunBacktestResponse {
  run_id: string;
}

export interface ValidateBacktestResponse {
  valid: boolean;
  errors: string[];
}

export interface BacktestRunSummary {
  run_id: string;
  title: string | null;
  description: string | null;
  market: string;
  timeframe: string;
  start: string;
  end: string;
  created_at: string;
  final_value: number;
  return_rate: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
}
