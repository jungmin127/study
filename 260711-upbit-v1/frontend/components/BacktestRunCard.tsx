'use client';

import Link from 'next/link';
import { Copy, Eye } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { returnRateColor } from '@/lib/return-rate-color';
import { summarizeGroup } from '@/lib/condition-summary';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { buildCopyHref } from '@/components/BacktestRunsTable';
import type { BacktestRunSummary } from '@/lib/types/eda';

function LastTradeStatusBadge({ status }: { status: BacktestRunSummary['last_trade_status'] }) {
  if (status === 'none') return <span className="text-muted-foreground">-</span>;
  if (status === 'open') return <Badge variant="secondary">보유중</Badge>;
  return <Badge variant="outline">청산</Badge>;
}

interface BacktestRunCardProps {
  run: BacktestRunSummary;
  marketName?: string;
  selected: boolean;
  onToggleSelected: (checked: boolean) => void;
}

export default function BacktestRunCard({ run, marketName, selected, onToggleSelected }: BacktestRunCardProps) {
  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 flex items-start gap-2">
        <Checkbox
          checked={selected}
          onCheckedChange={(checked) => onToggleSelected(checked === true)}
          aria-label={`${run.title || run.run_id} 선택`}
          className="mt-0.5"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
          </p>
          <p className="text-xs text-muted-foreground">
            {run.market}
            {marketName ? ` · ${marketName}` : ''} · {formatTimeframe(run.timeframe)}
          </p>
          <p className="text-xs text-muted-foreground">
            {run.start.slice(0, 10)} ~ {run.end.slice(0, 10)}
          </p>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
        <span className={returnRateColor(run.return_rate)}>
          수익률 {run.return_rate?.toFixed(2) ?? '-'}%
          {run.is_live && <span className="ml-1 text-xs text-muted-foreground">(실시간)</span>}
        </span>
        <span className="text-muted-foreground">MDD {run.max_drawdown?.toFixed(2) ?? '-'}%</span>
        <LastTradeStatusBadge status={run.last_trade_status} />
      </div>

      <details className="mb-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none">매수/매도 조건 보기</summary>
        <p className="mt-1 font-mono">매수: {summarizeGroup(run.buy_conditions)}</p>
        <p className="mt-1 font-mono">매도: {summarizeGroup(run.sell_conditions)}</p>
      </details>

      <p className="mb-2 text-xs text-muted-foreground">실행 시각: {formatDateTime(run.created_at)}</p>

      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          className="max-md:min-h-9 flex-1"
          nativeButton={false}
          role="link"
          render={<Link href={`/backtests/${run.run_id}`} />}
        >
          <Eye className="size-3.5" />
          보기
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="max-md:min-h-9 flex-1"
          nativeButton={false}
          role="link"
          render={<Link href={buildCopyHref(run)} />}
        >
          <Copy className="size-3.5" />
          복사
        </Button>
      </div>
    </div>
  );
}
