import { apiFetch } from './client';
import type { RegimeLibrarySlot, RegimeStrategyMapping } from '@/lib/types/regimeLibrary';

export function getRegimeStrategyLibrary(): Promise<RegimeStrategyMapping[]> {
  return apiFetch<RegimeStrategyMapping[]>('/api/v1/regime-strategy-library');
}

export function upsertRegimeStrategyMapping(
  market: string,
  regime: RegimeLibrarySlot,
  sourceRunId: string,
): Promise<{ market: string; regime: string; source_run_id: string }> {
  return apiFetch(`/api/v1/regime-strategy-library/${market}/${encodeURIComponent(regime)}`, {
    method: 'PUT',
    body: JSON.stringify({ source_run_id: sourceRunId }),
  });
}

export function deleteRegimeStrategyMapping(
  market: string,
  regime: RegimeLibrarySlot,
): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/v1/regime-strategy-library/${market}/${encodeURIComponent(regime)}`, {
    method: 'DELETE',
  });
}
