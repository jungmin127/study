import { apiFetch } from './client';
import type { JournalStrategyDetail, JournalSummary } from '@/lib/types/journal';

export function getJournalSummary(): Promise<JournalSummary> {
  return apiFetch<JournalSummary>('/api/v1/journal/summary');
}

export function getJournalStrategyDetail(id: string): Promise<JournalStrategyDetail> {
  return apiFetch<JournalStrategyDetail>(`/api/v1/journal/strategies/${id}`);
}
