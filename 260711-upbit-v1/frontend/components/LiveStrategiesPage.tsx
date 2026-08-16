'use client';

import { useCallback, useState } from 'react';
import { Check, Pause, Play, Square, Trash2, X } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import {
  approveLiveStrategy,
  deleteLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
} from '@/lib/api/liveStrategies';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';
import { returnRateColor } from '@/lib/return-rate-color';
import { useVisiblePolling } from '@/lib/hooks/useVisiblePolling';

const POLL_INTERVAL_MS = 5000;

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function Stat({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex-1 border-l border-border pl-2.5 first:border-l-0 first:pl-0">
      <p className="text-[0.68rem] text-muted-foreground">{label}</p>
      <p className={`text-sm font-semibold tabular-nums ${valueClassName ?? ''}`}>{value}</p>
    </div>
  );
}

export default function LiveStrategiesPage() {
  const [strategies, setStrategies] = useState<LiveStrategy[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getLiveStrategies();
      setStrategies(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '전략 목록을 불러오지 못했습니다.');
    }
  }, []);

  useVisiblePolling(refresh, POLL_INTERVAL_MS);

  async function runAction<T>(id: string, action: (id: string) => Promise<T>) {
    setActionError(null);
    setPendingId(id);
    try {
      await action(id);
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '요청 처리 중 오류가 발생했습니다.');
    } finally {
      setPendingId(null);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (strategies.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        등록된 라이브 전략이 없습니다. 백테스트 상세 페이지에서 시작하세요.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {actionError && <p className="text-sm text-destructive">{actionError}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3">
        {strategies.map((s) => (
          <Card
            key={s.id}
            className={`gap-2 overflow-hidden border-l-[3px] py-3 md:gap-3 md:py-4 ${
              s.open_position ? 'border-l-red-500' : 'border-l-transparent'
            }`}
          >
            <div className="flex items-center justify-between gap-2 px-4">
              <span className="min-w-0 truncate text-sm font-semibold">
                {s.market} · {formatTimeframe(s.timeframe)}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                <Badge variant={s.status === 'running' ? 'default' : 'secondary'}>{s.status}</Badge>
                {s.status === 'draft' && (
                  <>
                    <Button
                      size="icon-lg"
                      aria-label="승인"
                      title="승인"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, approveLiveStrategy)}
                    >
                      <Check />
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="outline"
                      aria-label="취소"
                      title="취소"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      <X />
                    </Button>
                  </>
                )}
                {s.status === 'running' && (
                  <>
                    <Button
                      size="icon-lg"
                      variant="outline"
                      aria-label="일시정지"
                      title="일시정지"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, pauseLiveStrategy)}
                    >
                      <Pause />
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="destructive"
                      aria-label="중지"
                      title="중지"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      <Square />
                    </Button>
                  </>
                )}
                {s.status === 'paused' && (
                  <>
                    <Button
                      size="icon-lg"
                      aria-label="재개"
                      title="재개"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, resumeLiveStrategy)}
                    >
                      <Play />
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="destructive"
                      aria-label="중지"
                      title="중지"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      <Square />
                    </Button>
                  </>
                )}
                {s.status === 'stopped' && (
                  <AlertDialog>
                    {/* AlertDialogTrigger has no asChild in this project's base-ui-backed
                        shadcn style; apply Button's own class-variance styles directly
                        (same pattern as BacktestRunsTable.tsx). */}
                    <AlertDialogTrigger
                      type="button"
                      className={buttonVariants({ variant: 'destructive', size: 'icon-lg' })}
                      aria-label="삭제"
                      title="삭제"
                      disabled={pendingId === s.id}
                    >
                      <Trash2 />
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>이 전략을 삭제하시겠습니까?</AlertDialogTitle>
                        <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>취소</AlertDialogCancel>
                        <AlertDialogAction onClick={() => runAction(s.id, deleteLiveStrategy)}>
                          삭제
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
            </div>

            <div className="flex px-4">
              {s.current_capital !== null && (
                <Stat label="현재 자금" value={`${Math.round(s.current_capital).toLocaleString()}원`} />
              )}
              {s.open_position ? (
                <>
                  <Stat label="진입가" value={Math.round(s.open_position.entry_price).toLocaleString()} />
                  <Stat
                    label="손익"
                    value={
                      s.open_position.unrealized_pnl_pct !== null
                        ? fmtPct(s.open_position.unrealized_pnl_pct)
                        : '-'
                    }
                    valueClassName={returnRateColor(s.open_position.unrealized_pnl_pct)}
                  />
                </>
              ) : (
                s.status !== 'draft' && <Stat label="포지션" value={s.status === 'stopped' ? '중지됨' : '없음'} />
              )}
            </div>

            {s.status === 'stopped' && s.stopped_at && (
              <p className="px-4 text-xs text-muted-foreground">중지 시각: {s.stopped_at}</p>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
