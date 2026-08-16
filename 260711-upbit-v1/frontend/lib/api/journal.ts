import { apiFetch } from './client';
import type { JournalMarketDetail, JournalSummary } from '@/lib/types/journal';

export function getJournalSummary(): Promise<JournalSummary> {
  return apiFetch<JournalSummary>('/api/v1/journal/summary');
}

export function getMarketJournal(market: string): Promise<JournalMarketDetail> {
  return apiFetch<JournalMarketDetail>(`/api/v1/journal/markets/${encodeURIComponent(market)}`);
}
