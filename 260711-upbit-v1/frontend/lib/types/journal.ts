import type { LiveStrategyStatus } from '@/lib/types/liveStrategies';

export interface JournalEquityPoint {
  trading_date: string;
  value: number;
}

export interface JournalStrategyCard {
  id: string;
  market: string;
  timeframe: string;
  status: LiveStrategyStatus;
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  trade_count: number;
}

export interface JournalSummary {
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  mdd_pct: number;
  win_rate_pct: number;
  equity_curve: JournalEquityPoint[];
  strategies: JournalStrategyCard[];
}

export interface JournalTradeLogEntry {
  position_id: string;
  entry_time: string;
  entry_price: number;
  entry_qty: number;
  exit_time: string;
  exit_price: number;
  exit_qty: number;
  realized_pnl: number;
  realized_pnl_pct: number;
  close_reason: string;
}

export interface JournalMetricSet {
  win_rate_pct: number;
  avg_return_pct: number;
  mdd_pct: number;
  trade_count: number;
}

export interface JournalBacktestComparison {
  backtest: JournalMetricSet;
  live: JournalMetricSet;
  sample_size_warning: boolean;
}

export interface JournalDailyCell {
  trading_date: string;
  pnl: number;
  pnl_pct: number;
  cumulative: number;
}

export interface JournalMarketDetail {
  market: string;
  timeframes: string[];
  statuses: LiveStrategyStatus[];
  cumulative_pnl: number;
  cumulative_pnl_pct: number;
  mdd_pct: number;
  win_rate_pct: number;
  avg_slippage_pct: number | null;
  max_slippage_pct: number | null;
  trade_count: number;
  backtest_comparison: JournalBacktestComparison | null;
  trade_log: JournalTradeLogEntry[];
  daily: JournalDailyCell[];
}
