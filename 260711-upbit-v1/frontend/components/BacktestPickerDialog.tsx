'use client';

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getBacktestRuns } from '@/lib/api/eda';
import { returnRateColor } from '@/lib/return-rate-color';
import type { BacktestRunSummary } from '@/lib/types/eda';

export default function BacktestPickerDialog({
  market,
  title,
  description,
  excludeRunId,
  trigger,
  emptyText = '선택할 수 있는 백테스트 결과가 없습니다.',
  confirmText = '확인',
  onSelect,
}: {
  market: string;
  title: string;
  description?: string;
  excludeRunId?: string | null;
  trigger: React.ReactElement;
  emptyText?: string;
  confirmText?: string;
  onSelect: (runId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<BacktestRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function closeAndReset() {
    setOpen(false);
    setCandidates([]);
    setSelectedRunId(null);
    setLoadError(null);
    setSubmitError(null);
  }

  async function loadCandidates() {
    setLoading(true);
    setLoadError(null);
    try {
      const runs = await getBacktestRuns(market);
      setCandidates(excludeRunId ? runs.filter((r) => r.run_id !== excludeRunId) : runs);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '백테스트 결과를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit() {
    if (!selectedRunId) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSelect(selectedRunId);
      closeAndReset();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '저장에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setOpen(true);
          loadCandidates();
        } else {
          closeAndReset();
        }
      }}
    >
      <DialogTrigger render={trigger} />
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
          {loading && <p className="text-muted-foreground">불러오는 중...</p>}
          {loadError && <p className="text-destructive">{loadError}</p>}
          {!loading && !loadError && candidates.length === 0 && (
            <p className="rounded-md bg-muted/50 p-3 text-muted-foreground">{emptyText}</p>
          )}
          {!loading && candidates.length > 0 && (
            <div className="space-y-2">
              {candidates.map((run) => (
                <label
                  key={run.run_id}
                  className={`flex cursor-pointer items-start gap-2 rounded-md border p-3 ${
                    selectedRunId === run.run_id ? 'border-primary bg-muted/50' : 'border-border'
                  }`}
                >
                  <input
                    type="radio"
                    name="backtest-picker-candidate"
                    className="mt-1"
                    checked={selectedRunId === run.run_id}
                    onChange={() => setSelectedRunId(run.run_id)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">
                        {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                      </span>
                      <span className={returnRateColor(run.return_rate)}>
                        수익률 {run.return_rate?.toFixed(2) ?? '-'}%
                      </span>
                    </div>
                    {run.description && (
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">{run.description}</p>
                    )}
                  </div>
                </label>
              ))}
            </div>
          )}
          {submitError && <p className="text-destructive">{submitError}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={closeAndReset} disabled={submitting}>
            취소
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !selectedRunId}>
            {confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
