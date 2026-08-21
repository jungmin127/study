'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getJournalSummary, getMarketJournal } from '@/lib/api/journal';
import type { JournalMarketDetail, JournalSummary } from '@/lib/types/journal';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { InfoPopover } from '@/components/ui/info-popover';
import { formatTimeframe } from '@/lib/format';
import JournalCalendar from '@/components/JournalCalendar';
import JournalMarketDetailView from '@/components/JournalMarketDetail';

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
  const [selectedMarket, setSelectedMarket] = useState<string | null>(null);
  const [detail, setDetail] = useState<JournalMarketDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refreshSummary = useCallback(async () => {
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
    refreshSummary();
  }, [refreshSummary]);

  const markets = useMemo(() => {
    if (!summary) return [];
    return Array.from(new Set(summary.strategies.map((s) => s.market))).sort();
  }, [summary]);

  useEffect(() => {
    if (selectedMarket === null && markets.length > 0) {
      setSelectedMarket(markets[0]);
    }
  }, [markets, selectedMarket]);

  const loadDetail = useCallback(async (market: string) => {
    setDetailError(null);
    setDetailLoading(true);
    try {
      const data = await getMarketJournal(market);
      setDetail(data);
    } catch (err) {
      setDetail(null);
      setDetailError(err instanceof ApiError ? err.message : '코인별 매매일지를 불러오지 못했습니다.');
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedMarket) loadDetail(selectedMarket);
  }, [selectedMarket, loadDetail]);

  async function refreshAll() {
    await refreshSummary();
    if (selectedMarket) await loadDetail(selectedMarket);
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (!summary) return <p className="text-sm text-muted-foreground">불러오는 중...</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">계좌 전체 요약</h2>
        <Button size="sm" variant="outline" className="max-md:min-h-9" disabled={loading} onClick={refreshAll}>
          새로고침
        </Button>
      </div>

      {summary.strategies.length === 0 ? (
        <p className="text-sm text-muted-foreground">아직 실거래 이력이 없습니다.</p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2 sm:gap-4">
            <Card className="gap-1 py-2 sm:gap-4 sm:py-4">
              <CardHeader className="px-2 sm:px-4">
                <CardTitle className="flex items-center gap-1 text-xs font-medium sm:text-sm">
                  누적손익
                  <InfoPopover>
                    처음 투입한 원금(시드) 대비 손익입니다. 거래로 번 돈을 재투자해도 기준
                    원금 자체는 바뀌지 않습니다.
                  </InfoPopover>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-2 text-xs font-semibold sm:px-4 sm:text-lg">
                {fmtKrw(summary.cumulative_pnl)} ({fmtPct(summary.cumulative_pnl_pct)})
              </CardContent>
            </Card>
            <Card className="gap-1 py-2 sm:gap-4 sm:py-4">
              <CardHeader className="px-2 sm:px-4">
                <CardTitle className="flex items-center gap-1 text-xs font-medium sm:text-sm">
                  MDD
                  <InfoPopover>
                    일별 잔고가 이전 최고점 대비 가장 크게 빠졌던 낙폭(%)입니다. 보유
                    중인(미청산) 포지션의 평가손실은 반영되지 않습니다.
                  </InfoPopover>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-2 text-xs font-semibold sm:px-4 sm:text-lg">
                {fmtPct(summary.mdd_pct)}
              </CardContent>
            </Card>
            <Card className="gap-1 py-2 sm:gap-4 sm:py-4">
              <CardHeader className="px-2 sm:px-4">
                <CardTitle className="flex items-center gap-1 text-xs font-medium sm:text-sm">
                  승률
                  <InfoPopover>
                    청산된 거래 중 손익이 0 이상인 비율입니다. 보유 중인 포지션은
                    포함되지 않습니다.
                  </InfoPopover>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-2 text-xs font-semibold sm:px-4 sm:text-lg">
                {summary.win_rate_pct.toFixed(1)}%
              </CardContent>
            </Card>
          </div>

          <JournalCalendar daily={summary.daily} />

          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">코인별 매매일지</h2>
            <select
              className="rounded-md border bg-background px-3 py-2 text-sm max-md:min-h-9"
              value={selectedMarket ?? ''}
              onChange={(e) => setSelectedMarket(e.target.value)}
            >
              {markets.map((market) => (
                <option key={market} value={market}>
                  {market}
                </option>
              ))}
            </select>
          </div>

          {detailLoading && <p className="text-sm text-muted-foreground">불러오는 중...</p>}
          {detailError && <p className="text-sm text-destructive">{detailError}</p>}
          {detail && (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                {detail.timeframes.map(formatTimeframe).join(', ')} · {detail.statuses.join(', ')}
              </p>

              <JournalCalendar daily={detail.daily} />

              <JournalMarketDetailView detail={detail} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
