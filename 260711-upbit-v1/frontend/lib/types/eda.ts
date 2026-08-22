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
  top_trade_contribution_pct: number | null;
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
  live_price_as_of: string | null;
  title: string | null;
  description: string | null;
  created_at: string;
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

export interface SegmentSizeEntry {
  market: string;
  korean_name: string;
  segment: 'large' | 'mid' | 'junk';
  trade_value_24h: number | null;
  volatility_30d: number | null;
  trade_value_percentile: number | null;
  volatility_percentile: number | null;
  is_caution: boolean;
  computed_at: string;
}

export type TrendDirection = 'up' | 'down' | 'sideways';

export interface TrendSegment {
  start_date: string;
  end_date: string;
  days: number;
  return_pct: number;
  trend: TrendDirection;
  first_half_trend: TrendDirection;
  second_half_trend: TrendDirection;
  pattern_label: string;
}

export interface TrendSegmentAnalysis {
  market: string;
  threshold_pct: number;
  computed_at: string;
  segments: TrendSegment[];
  ohlcv: OhlcvPoint[];
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
  top_trade_contribution_pct: number | null;
  trade_count: number;
  candle_count: number | null;
  is_live: boolean;
  last_trade_status: 'open' | 'closed' | 'none';
  buy_conditions: ConditionGroup;
  sell_conditions: ConditionGroup;
}

export interface IndicatorPool {
  categories: string[];
  excluded_indicators: string[];
}

export interface GridSearchJobRequest {
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
  indicator_pool?: IndicatorPool;
  base_run_id?: string;
  combinator?: 'AND' | 'OR';
}

export interface GridSearchSavedResult {
  rank: number;
  run_id: string;
  return_pct: number;
  title: string;
  trade_count?: number;
  candle_count?: number;
  max_drawdown_pct?: number | null;
  win_rate_pct?: number | null;
}

export interface GridSearchJob {
  id: string;
  market: string;
  timeframe: string;
  capital: number;
  start: string;
  end: string;
  top_n: number;
  status: 'running' | 'completed' | 'failed' | 'canceled';
  total_combos: number | null;
  done_combos: number;
  started_at: string;
  finished_at: string | null;
  elapsed_sec: number | null;
  error_message: string | null;
  result_json: GridSearchSavedResult[] | null;
  indicator_pool: IndicatorPool | null;
  base_run_id: string | null;
  combinator: 'AND' | 'OR' | null;
}

export interface GridSearchEstimate {
  buy_count: number;
  sell_count: number;
  total_combos: number;
  estimated_seconds: number;
}
