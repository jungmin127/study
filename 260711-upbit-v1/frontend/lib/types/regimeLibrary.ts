export type RegimeLibrarySlot = '하락' | '횡보' | '상승' | '기본';

export interface RegimeStrategyMapping {
  market: string;
  regime: RegimeLibrarySlot;
  source_run_id: string;
  timeframe: string;
  updated_at: string;
}
