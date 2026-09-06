import { apiFetch } from './client';
import type {
  BacktestConfig,
  CreateLiveStrategyRequest,
  LiveStrategy,
} from '@/lib/types/liveStrategies';

export function getBacktestConfig(runId: string): Promise<BacktestConfig> {
  return apiFetch<BacktestConfig>(`/api/v1/backtests/${runId}/config`);
}

export function createLiveStrategy(req: CreateLiveStrategyRequest): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>('/api/v1/live-strategies', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function getLiveStrategies(): Promise<LiveStrategy[]> {
  return apiFetch<LiveStrategy[]>('/api/v1/live-strategies');
}

export function approveLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/approve`, { method: 'POST' });
}

export function pauseLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/pause`, { method: 'POST' });
}

export function resumeLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/resume`, { method: 'POST' });
}

export function stopLiveStrategy(id: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/stop`, { method: 'POST' });
}

export function deleteLiveStrategy(id: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/live-strategies/${id}`, { method: 'DELETE' });
}

export function updateLiveStrategyCapital(id: string, newCapital: number): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/capital`, {
    method: 'PATCH',
    body: JSON.stringify({ new_capital: newCapital }),
  });
}

export function replaceLiveStrategyStrategy(id: string, sourceRunId: string): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/replace-strategy`, {
    method: 'POST',
    body: JSON.stringify({ source_run_id: sourceRunId }),
  });
}

export function setLiveStrategyAutoSwap(id: string, enabled: boolean): Promise<LiveStrategy> {
  return apiFetch<LiveStrategy>(`/api/v1/live-strategies/${id}/auto-swap`, {
    method: 'PATCH',
    body: JSON.stringify({ enabled }),
  });
}

export interface RegimeSwapLogEntry {
  id: string;
  market: string;
  occurred_at: string;
  event: 'swap_success' | 'swap_skipped_open_position' | 'swap_skipped_no_mapping' | 'manual_override_ack';
  from_regime: string | null;
  to_regime: string;
  detail: string | null;
}

export function getRegimeSwapLog(id: string): Promise<RegimeSwapLogEntry[]> {
  return apiFetch<RegimeSwapLogEntry[]>(`/api/v1/live-strategies/${id}/regime-swap-log`);
}
