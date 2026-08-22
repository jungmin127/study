'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ApiError } from '@/lib/api/client';
import {
  cancelGridSearchJob,
  createGridSearchJob,
  getGridSearchJobs,
  resetGridSearchActiveJob,
} from '@/lib/api/eda';
import { Button } from '@/components/ui/button';
import type { GridSearchJob, GridSearchJobRequest } from '@/lib/types/eda';
import GridSearchForm from '@/components/GridSearchForm';
import GridSearchProgress from '@/components/GridSearchProgress';
import GridSearchHistory from '@/components/GridSearchHistory';
import { useVisiblePolling } from '@/lib/hooks/useVisiblePolling';

const POLL_INTERVAL_MS = 3000;

export default function GridSearchPage() {
  const searchParams = useSearchParams();
  const [jobs, setJobs] = useState<GridSearchJob[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitErrorStatus, setSubmitErrorStatus] = useState<number | null>(null);
  const [resetting, setResetting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getGridSearchJobs();
      setJobs(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '이력을 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const runningJob = jobs.find((j) => j.status === 'running') ?? null;
  const isJobRunning = runningJob !== null;

  useVisiblePolling(refresh, POLL_INTERVAL_MS, isJobRunning);

  async function handleSubmit(request: GridSearchJobRequest) {
    setSubmitError(null);
    setSubmitErrorStatus(null);
    try {
      const job = await createGridSearchJob(request);
      setJobs((prev) => [job, ...prev]);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'grid search 시작 중 오류가 발생했습니다.');
      setSubmitErrorStatus(err instanceof ApiError ? err.status : null);
      throw err;
    }
  }

  async function handleResetActiveJob() {
    setResetting(true);
    try {
      await resetGridSearchActiveJob();
      setSubmitError(null);
      setSubmitErrorStatus(null);
      await refresh();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '초기화 중 오류가 발생했습니다.');
    } finally {
      setResetting(false);
    }
  }

  async function handleCancel() {
    if (!runningJob) return;
    try {
      await cancelGridSearchJob(runningJob.id);
      await refresh();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '취소 중 오류가 발생했습니다.');
    }
  }

  return (
    <div className="space-y-6">
      <GridSearchForm
        initial={{
          market: searchParams.get('market') ?? '',
          timeframe: searchParams.get('timeframe') ?? 'minutes60',
          capital: searchParams.get('capital') ?? '1000000',
          start: searchParams.get('start') ?? '',
          end: searchParams.get('end') ?? '',
          topN: searchParams.get('topN') ?? '20',
        }}
        disabled={runningJob !== null}
        onSubmit={handleSubmit}
      />
      {submitError && (
        <div className="flex items-center gap-3">
          <p className="text-sm text-destructive">{submitError}</p>
          {submitErrorStatus === 409 && (
            <Button variant="outline" size="sm" onClick={handleResetActiveJob} disabled={resetting}>
              {resetting ? '초기화하는 중...' : '초기화'}
            </Button>
          )}
        </div>
      )}
      {runningJob && <GridSearchProgress job={runningJob} onCancel={handleCancel} />}
      {loadError && <p className="text-sm text-destructive">{loadError}</p>}
      <GridSearchHistory jobs={jobs} onRefresh={refresh} onSubmit={handleSubmit} />
    </div>
  );
}
