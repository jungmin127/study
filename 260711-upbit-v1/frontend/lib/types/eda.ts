import type { ComparisonOperator, ConditionGroup } from './strategy';

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

export interface BacktestMetrics {
  total_return: number;
  cagr: number;
  mdd: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  win_rate: number;
  profit_factor: number;
  avg_holding_period: number;
  max_consecutive_loss: number;
  buy_and_hold_return: number;
  total_trades: number;
}

export interface OhlcvPoint {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface BacktestDetail {
  market: string;
  timeframe: string;
  start: string;
  end: string;
  initial_capital: number;
  final_value: number;
  metrics: BacktestMetrics;
  ohlcv: OhlcvPoint[];
  trades: Trade[];
}

export interface Market {
  market: string;
  korean_name: string;
  english_name: string;
  price: number | null;
  change_rate: number | null;
  change_price: number | null;
  trade_price_24h: number | null;
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
  fixedOperator?: ComparisonOperator; // 있으면 연산자 select 대신 고정 배지로 표시
  sellOnly?: boolean; // true면 매수 조건 카탈로그에서 제외
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
