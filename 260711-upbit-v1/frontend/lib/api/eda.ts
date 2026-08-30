import { apiFetch } from './client';
import type {
  BacktestDetail,
  BacktestRunSummary,
  Combo,
  GridSearchEstimate,
  GridSearchIndicatorPoolCatalog,
  GridSearchJob,
  GridSearchJobRequest,
  IndicatorCatalogItem,
  IndicatorPool,
  Market,
  MlCurrentPrediction,
  RegimeFactAnalysis,
  RegimeMlJob,
  RegimeMlModelSummary,
  RunBacktestRequest,
  RunBacktestResponse,
  SegmentSizeEntry,
  SweepResult,
  TrendSegmentAnalysis,
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

export function getBacktestRuns(market?: string): Promise<BacktestRunSummary[]> {
  const query = market ? `?market=${encodeURIComponent(market)}` : '';
  return apiFetch<BacktestRunSummary[]>(`/api/v1/backtests${query}`);
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

export function updateBacktestRun(
  runId: string,
  req: { title: string | null; description: string | null },
): Promise<{ title: string | null; description: string | null; created_at: string }> {
  return apiFetch(`/api/v1/backtests/${runId}`, {
    method: 'PATCH',
    body: JSON.stringify(req),
  });
}

export function refreshBacktestRun(runId: string): Promise<{ run_id: string }> {
  return apiFetch<{ run_id: string }>(`/api/v1/backtests/${runId}/refresh`, {
    method: 'POST',
  });
}

export function getSegmentSizeAnalysis(): Promise<SegmentSizeEntry[]> {
  return apiFetch<SegmentSizeEntry[]>('/api/v1/analysis/segments/size');
}

export function getTrendSegments(market: string): Promise<TrendSegmentAnalysis> {
  return apiFetch<TrendSegmentAnalysis>(`/api/v1/analysis/trend-segments/${market}`);
}

export function refreshTrendSegments(market: string): Promise<TrendSegmentAnalysis> {
  return apiFetch<TrendSegmentAnalysis>(`/api/v1/analysis/trend-segments/${market}/refresh`, {
    method: 'POST',
  });
}

export function createGridSearchJob(req: GridSearchJobRequest): Promise<GridSearchJob> {
  return apiFetch<GridSearchJob>('/api/v1/grid-search/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export function getGridSearchIndicatorPool(): Promise<GridSearchIndicatorPoolCatalog> {
  return apiFetch<GridSearchIndicatorPoolCatalog>('/api/v1/grid-search/indicator-pool');
}

export function getGridSearchEstimate(pool: IndicatorPool, market?: string): Promise<GridSearchEstimate> {
  const params = new URLSearchParams({
    categories: pool.categories.join(','),
    exclude_indicators: pool.excluded_indicators.join(','),
    ...(market ? { market } : {}),
  });
  return apiFetch<GridSearchEstimate>(`/api/v1/grid-search/estimate?${params.toString()}`);
}

export function getGridSearchJobs(): Promise<GridSearchJob[]> {
  return apiFetch<GridSearchJob[]>('/api/v1/grid-search/jobs');
}

export function cancelGridSearchJob(jobId: string): Promise<{ status: string }> {
  return apiFetch(`/api/v1/grid-search/jobs/${jobId}/cancel`, { method: 'POST' });
}

export function resetGridSearchActiveJob(): Promise<{ reset_job_id: string | null }> {
  return apiFetch('/api/v1/grid-search/jobs/reset', { method: 'POST' });
}

export function deleteGridSearchResult(jobId: string, runId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}/results/${runId}`, {
    method: 'DELETE',
  });
}

export function deleteGridSearchJob(jobId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/api/v1/grid-search/jobs/${jobId}`, {
    method: 'DELETE',
  });
}

export function getRegimeMlCurrentPrediction(params: {
  market: string;
  timeframe: string;
}): Promise<MlCurrentPrediction> {
  const query = new URLSearchParams(params);
  return apiFetch<MlCurrentPrediction>(`/api/v1/regime/ml-current-prediction?${query.toString()}`);
}

export function getRegimeFactSegments(params: {
  market: string;
  timeframe: string;
}): Promise<RegimeFactAnalysis> {
  const query = new URLSearchParams(params);
  return apiFetch<RegimeFactAnalysis>(`/api/v1/regime/fact-segments?${query.toString()}`);
}

export function getRegimeMlTrainEnabled(): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>('/api/v1/regime/ml-train-enabled');
}

export function startRegimeMlTrainJob(): Promise<RegimeMlJob> {
  return apiFetch<RegimeMlJob>('/api/v1/regime/ml-train', { method: 'POST' });
}

export function getRegimeMlTrainJobs(): Promise<RegimeMlJob[]> {
  return apiFetch<RegimeMlJob[]>('/api/v1/regime/ml-train/jobs');
}

export function getRegimeMlModels(): Promise<RegimeMlModelSummary[]> {
  return apiFetch<RegimeMlModelSummary[]>('/api/v1/regime/ml-models');
}

export function deployRegimeMlModel(
  modelTimestamp: string,
): Promise<{ deployed: boolean; model_timestamp: string }> {
  return apiFetch('/api/v1/regime/ml-deploy', {
    method: 'POST',
    body: JSON.stringify({ model_timestamp: modelTimestamp }),
  });
}
