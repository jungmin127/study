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
