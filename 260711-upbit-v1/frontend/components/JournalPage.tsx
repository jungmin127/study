'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { ApiError } from '@/lib/api/client';
import { getJournalStrategyDetail, getJournalSummary } from '@/lib/api/journal';
import type { JournalStrategyDetail, JournalSummary } from '@/lib/types/journal';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { formatTimeframe } from '@/lib/format';
import JournalStrategyDetailView from '@/components/JournalStrategyDetail';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function fmtKrw(value: number): string {
  return `${Math.round(value).toLocaleString()}원`;
}

export default function JournalPage() {
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<JournalStrategyDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getJournalSummary();
      setSummary(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '매매일지를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function selectStrategy(id: string) {
    if (selectedId === id) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    setSelectedId(id);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const data = await getJournalStrategyDetail(id);
      setDetail(data);
    } catch (err) {
      setDetailError(err instanceof ApiError ? err.message : '전략 상세를 불러오지 못했습니다.');
    } finally {
      setDetailLoading(false);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (!summary) return <p className="text-sm text-muted-foreground">불러오는 중...</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">계좌 전체 요약</h2>
        <Button size="sm" variant="outline" className="max-md:min-h-9" disabled={loading} onClick={refresh}>
          새로고침
        </Button>
      </div>

      {summary.strategies.length === 0 ? (
        <p className="text-sm text-muted-foreground">아직 실거래 이력이 없습니다.</p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">누적손익</CardTitle>
              </CardHeader>
              <CardContent className="text-lg font-semibold">
                {fmtKrw(summary.cumulative_pnl)} ({fmtPct(summary.cumulative_pnl_pct)})
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">MDD</CardTitle>
              </CardHeader>
              <CardContent className="text-lg font-semibold">
                {fmtPct(summary.mdd_pct)}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">승률</CardTitle>
              </CardHeader>
              <CardContent className="text-lg font-semibold">
                {summary.win_rate_pct.toFixed(1)}%
              </CardContent>
            </Card>
          </div>

          {summary.equity_curve.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              아직 청산된 거래가 없어 그래프를 표시할 수 없습니다.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={summary.equity_curve}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="trading_date" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="var(--color-primary)"
                  name="계좌 총자산"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}

          <h2 className="text-base font-semibold">전략별 매매일지</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {summary.strategies.map((s) => (
              <Card
                key={s.id}
                className={`cursor-pointer ${selectedId === s.id ? 'border-primary' : ''}`}
                onClick={() => selectStrategy(s.id)}
              >
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    <span>
                      {s.market} · {formatTimeframe(s.timeframe)}
                    </span>
                    <Badge variant="secondary">{s.status}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-1 text-sm">
                  <p>
                    누적손익: {fmtKrw(s.cumulative_pnl)} ({fmtPct(s.cumulative_pnl_pct)})
                  </p>
                  <p>거래횟수: {s.trade_count}건</p>
                </CardContent>
              </Card>
            ))}
          </div>

          {selectedId && (
            <div>
              {detailLoading && <p className="text-sm text-muted-foreground">불러오는 중...</p>}
              {detailError && <p className="text-sm text-destructive">{detailError}</p>}
              {detail && <JournalStrategyDetailView detail={detail} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}
