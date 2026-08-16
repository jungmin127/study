'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import {
  approveLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
} from '@/lib/api/liveStrategies';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';

const POLL_INTERVAL_MS = 5000;

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
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

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  async function runAction(id: string, action: (id: string) => Promise<LiveStrategy>) {
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
          <Card key={s.id} className="py-3 gap-3 md:py-4 md:gap-4">
            <CardHeader>
              <CardTitle className="flex items-center justify-between max-md:text-sm">
                <span>{s.market} · {formatTimeframe(s.timeframe)}</span>
                <Badge variant={s.status === 'running' ? 'default' : 'secondary'}>{s.status}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 md:space-y-2">
              {s.current_capital !== null && (
                <p className="text-xs md:text-sm">현재 자금: {Math.round(s.current_capital).toLocaleString()}원</p>
              )}
              {s.open_position && (
                <div className="rounded-md bg-muted/50 p-1.5 text-xs md:p-2 md:text-sm">
                  <p>열린 포지션: 진입가 {Math.round(s.open_position.entry_price).toLocaleString()}</p>
                  <p>
                    수량 {s.open_position.entry_qty} · 손익{' '}
                    {s.open_position.unrealized_pnl_pct !== null ? fmtPct(s.open_position.unrealized_pnl_pct) : '-'}
                  </p>
                </div>
              )}
              <div className="flex flex-wrap gap-2 pt-1.5 md:pt-2">
                {s.status === 'draft' && (
                  <>
                    <Button
                      size="sm"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, approveLiveStrategy)}
                    >
                      승인
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      취소
                    </Button>
                  </>
                )}
                {s.status === 'running' && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, pauseLiveStrategy)}
                    >
                      일시정지
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      중지
                    </Button>
                  </>
                )}
                {s.status === 'paused' && (
                  <>
                    <Button
                      size="sm"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, resumeLiveStrategy)}
                    >
                      재개
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      중지
                    </Button>
                  </>
                )}
                {s.status === 'stopped' && (
                  <p className="text-xs text-muted-foreground">
                    중지됨{s.stopped_at ? ` (${s.stopped_at})` : ''}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
