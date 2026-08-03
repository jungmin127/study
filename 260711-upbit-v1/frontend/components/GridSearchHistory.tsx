'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import type { GridSearchJob } from '@/lib/types/eda';

const STATUS_LABEL: Record<GridSearchJob['status'], string> = {
  running: '진행중',
  completed: '완료',
  failed: '실패',
  canceled: '취소',
};

const STATUS_VARIANT: Record<GridSearchJob['status'], 'secondary' | 'default' | 'destructive' | 'outline'> = {
  running: 'secondary',
  completed: 'default',
  failed: 'destructive',
  canceled: 'outline',
};

function formatElapsedMinutes(seconds: number | null): string {
  if (seconds === null) return '-';
  return `${(seconds / 60).toFixed(1)}분`;
}

interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
}

export default function GridSearchHistory({ jobs }: GridSearchHistoryProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 실행한 grid search가 없습니다.</p>;
  }

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">요청 이력</h2>
      <div className="space-y-2">
        {jobs.map((job) => {
          const results = job.result_json ?? [];
          const isExpanded = expanded.has(job.id);
          const visibleResults = isExpanded ? results : results.slice(0, 1);
          return (
            <div key={job.id} className="rounded-md border p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABEL[job.status]}</Badge>
                <span className="font-medium">{job.market}</span>
                <span className="text-muted-foreground">{formatTimeframe(job.timeframe)}</span>
                <span className="text-muted-foreground">
                  {job.start}~{job.end}
                </span>
                <span className="text-muted-foreground">상위{job.top_n}</span>
                {job.elapsed_sec !== null && (
                  <span className="text-muted-foreground">{formatElapsedMinutes(job.elapsed_sec)}</span>
                )}
                <span className="ml-auto text-xs text-muted-foreground">{formatDateTime(job.started_at)}</span>
              </div>

              {job.status === 'failed' && job.error_message && (
                <p className="mt-2 text-sm text-destructive">{job.error_message}</p>
              )}

              {results.length > 0 && (
                <div className="mt-2 space-y-1">
                  {visibleResults.map((r) => (
                    <div key={r.run_id} className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">{r.rank}위</span>
                      <span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>
                      <Link href={`/backtests/${r.run_id}`} className="truncate underline">
                        {r.title}
                      </Link>
                    </div>
                  ))}
                  {results.length > 1 && (
                    <Button variant="link" size="sm" className="px-0" onClick={() => toggle(job.id)}>
                      {isExpanded ? '접기' : `나머지 ${results.length - 1}개 보기`}
                    </Button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
