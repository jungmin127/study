'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { GridSearchJob } from '@/lib/types/eda';

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}분 ${s}초`;
}

interface GridSearchProgressProps {
  job: GridSearchJob;
  onCancel: () => void;
}

export default function GridSearchProgress({ job, onCancel }: GridSearchProgressProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const elapsedSec = Math.max(0, (now - new Date(job.started_at).getTime()) / 1000);
  const pct = job.total_combos ? (job.done_combos / job.total_combos) * 100 : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          진행 중: {job.market} · {job.timeframe} · {job.start}~{job.end}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="h-3 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {job.total_combos
              ? `${pct.toFixed(1)}% (${job.done_combos.toLocaleString()} / ${job.total_combos.toLocaleString()}건)`
              : '계산 준비 중...'}
          </span>
          <span>경과 {formatElapsed(elapsedSec)}</span>
        </div>
        <Button variant="destructive" size="sm" onClick={onCancel}>
          취소
        </Button>
      </CardContent>
    </Card>
  );
}
