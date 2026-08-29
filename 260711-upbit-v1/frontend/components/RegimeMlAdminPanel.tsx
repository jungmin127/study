'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { ApiError } from '@/lib/api/client';
import {
  deployRegimeMlModel,
  getRegimeMlModels,
  getRegimeMlTrainEnabled,
  getRegimeMlTrainJobs,
  startRegimeMlTrainJob,
} from '@/lib/api/eda';
import { formatDateTime } from '@/lib/format';
import { useVisiblePolling } from '@/lib/hooks/useVisiblePolling';
import type { RegimeMlJob, RegimeMlModelSummary } from '@/lib/types/eda';

const POLL_INTERVAL_MS = 3000;

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : value.toFixed(3);
}

export default function RegimeMlAdminPanel() {
  const [enabled, setEnabled] = useState(false);
  const [jobs, setJobs] = useState<RegimeMlJob[]>([]);
  const [models, setModels] = useState<RegimeMlModelSummary[]>([]);
  const [startError, setStartError] = useState<string | null>(null);
  const [deployTarget, setDeployTarget] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  useEffect(() => {
    getRegimeMlTrainEnabled()
      .then((r) => setEnabled(r.enabled))
      .catch(() => setEnabled(false));
  }, []);

  const refreshJobs = useCallback(async () => {
    try {
      const data = await getRegimeMlTrainJobs();
      setJobs(data);
    } catch {
      // 폴링 실패는 조용히 다음 주기에 재시도한다.
    }
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      const data = await getRegimeMlModels();
      setModels(data);
    } catch {
      // 모델 목록 조회 실패는 조용히 다음 새로고침에 재시도한다.
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    refreshJobs();
    refreshModels();
  }, [enabled, refreshJobs, refreshModels]);

  const runningJob = jobs.find((j) => j.status === 'running') ?? null;
  const latestJob = jobs[0] ?? null;
  useVisiblePolling(refreshJobs, POLL_INTERVAL_MS, enabled && runningJob !== null);

  const wasRunningRef = useRef(false);
  useEffect(() => {
    if (wasRunningRef.current && runningJob === null) {
      refreshModels();
    }
    wasRunningRef.current = runningJob !== null;
  }, [runningJob, refreshModels]);

  async function handleStart() {
    setStartError(null);
    try {
      const job = await startRegimeMlTrainJob();
      setJobs((prev) => [job, ...prev]);
    } catch (err) {
      setStartError(err instanceof ApiError ? err.message : '학습 시작 중 오류가 발생했습니다.');
    }
  }

  async function handleConfirmDeploy() {
    if (!deployTarget) return;
    setDeploying(true);
    setDeployError(null);
    try {
      await deployRegimeMlModel(deployTarget);
      setDeployTarget(null);
      await refreshModels();
    } catch (err) {
      setDeployError(err instanceof ApiError ? err.message : '배포 중 오류가 발생했습니다.');
    } finally {
      setDeploying(false);
    }
  }

  if (!enabled) return null;

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">ML 재학습 관리자 패널</h2>
      <div className="mb-4 flex items-center gap-3">
        <Button type="button" size="sm" onClick={handleStart} disabled={runningJob !== null}>
          {runningJob ? '학습 중...' : '학습 시작'}
        </Button>
        {startError && <p className="text-xs text-destructive">{startError}</p>}
        {latestJob !== null && latestJob.status === 'failed' && (
          <p className="text-xs text-destructive">마지막 학습 실패: {latestJob.error_message}</p>
        )}
      </div>
      {models.length === 0 ? (
        <p className="text-sm text-muted-foreground">학습된 모델이 없습니다.</p>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>학습시각</TableHead>
                <TableHead className="text-right">상관계수(구)</TableHead>
                <TableHead className="text-right">macro F1(신)</TableHead>
                <TableHead className="text-right">weighted κ(신)</TableHead>
                <TableHead>상태</TableHead>
                <TableHead className="text-right">배포</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model) => (
                <TableRow key={model.model_timestamp}>
                  <TableCell>{formatDateTime(model.trained_at)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatScore(model.performance?.pooled_correlation)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatScore(model.performance?.pooled?.macro_f1)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatScore(model.performance?.pooled?.weighted_kappa)}
                  </TableCell>
                  <TableCell>
                    {model.is_deployed && <Badge variant="default">현재 배포됨</Badge>}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => setDeployTarget(model.model_timestamp)}
                    >
                      배포
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <AlertDialog open={deployTarget !== null} onOpenChange={(open) => { if (!open) { setDeployTarget(null); setDeployError(null); } }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>이 모델을 AWS 라이브로 배포하시겠습니까?</AlertDialogTitle>
            <AlertDialogDescription>
              실거래 대시보드가 참조하는 ML 예측 모델이 즉시 교체됩니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deployError && <p className="text-sm text-destructive">{deployError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmDeploy} disabled={deploying}>
              {deploying ? '배포 중...' : '배포'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
