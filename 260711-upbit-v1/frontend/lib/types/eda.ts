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
