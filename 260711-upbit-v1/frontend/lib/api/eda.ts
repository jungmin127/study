import { apiFetch } from './client';
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  IndicatorCatalogItem,
  Market,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  ValidateBacktestResponse,
} from '@/lib/types/eda';

export function getHeatmap(): Promise<SweepResult[]> {
  return apiFetch<SweepResult[]>('/api/v1/eda/heatmap');
}

export function getRanking(): Promise<SweepResult[]> {
  return apiFetch<SweepResult[]>('/api/v1/eda/ranking');
}

export function getCombos(): Promise<Combo[]> {
  return apiFetch<Combo[]>('/api/v1/eda/combos');
}

export function getHistory(combo: Combo): Promise<SweepResult[]> {
  const params = new URLSearchParams({
    signal_set_name: combo.signal_set_name,
    market: combo.market,
    timeframe: combo.timeframe,
    is_combined: String(combo.is_combined),
  });
  return apiFetch<SweepResult[]>(`/api/v1/eda/history?${params.toString()}`);
}

export function getBacktestDetail(runId: string): Promise<BacktestDetail> {
  return apiFetch<BacktestDetail>(`/api/v1/backtests/${runId}`);
}

export function getBacktestRuns(): Promise<BacktestRunSummary[]> {
  return apiFetch<BacktestRunSummary[]>('/api/v1/backtests');
}

export function getMarkets(): Promise<Market[]> {
  return apiFetch<Market[]>('/api/v1/markets');
}

export function getIndicatorCatalog(): Promise<IndicatorCatalogItem[]> {
  return apiFetch<IndicatorCatalogItem[]>('/api/v1/indicators/catalog');
}

export function validateBacktest(req: RunBacktestRequest): Promise<ValidateBacktestResponse> {
  return apiFetch<ValidateBacktestResponse>('/api/v1/backtests/validate', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function runBacktest(req: RunBacktestRequest): Promise<RunBacktestResponse> {
  return apiFetch<RunBacktestResponse>('/api/v1/backtests/run', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function deleteBacktestRun(runId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/backtests/${runId}`, {
    method: 'DELETE',
  });
}

export function getSegmentSizeAnalysis(): Promise<SegmentSizeEntry[]> {
  return apiFetch<SegmentSizeEntry[]>('/api/v1/analysis/segments/size');
}
