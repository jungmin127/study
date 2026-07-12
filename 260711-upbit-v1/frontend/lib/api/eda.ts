import { apiFetch } from './client';
import type { BacktestDetail, Combo, SweepResult } from '@/lib/types/eda';

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
