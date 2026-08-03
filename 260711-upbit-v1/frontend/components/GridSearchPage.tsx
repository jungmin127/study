'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ApiError } from '@/lib/api/client';
import { cancelGridSearchJob, createGridSearchJob, getGridSearchJobs } from '@/lib/api/eda';
import type { GridSearchJob, GridSearchJobRequest } from '@/lib/types/eda';
import GridSearchForm from '@/components/GridSearchForm';
import GridSearchProgress from '@/components/GridSearchProgress';

const POLL_INTERVAL_MS = 3000;

export default function GridSearchPage() {
  const searchParams = useSearchParams();
  const [jobs, setJobs] = useState<GridSearchJob[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

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

  useEffect(() => {
    if (!runningJob) return;
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [runningJob, refresh]);

  async function handleSubmit(request: GridSearchJobRequest) {
    setSubmitError(null);
    try {
      const job = await createGridSearchJob(request);
      setJobs((prev) => [job, ...prev]);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'grid search 시작 중 오류가 발생했습니다.');
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
      {submitError && <p className="text-sm text-destructive">{submitError}</p>}
      {runningJob && <GridSearchProgress job={runningJob} onCancel={handleCancel} />}
      {loadError && <p className="text-sm text-destructive">{loadError}</p>}
      <div className="text-sm text-muted-foreground">요청 이력은 다음 단계에서 추가됩니다.</div>
    </div>
  );
}
