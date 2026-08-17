'use client';

import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import CoinSelect from '@/components/CoinSelect';
import TrendPatternLegend from '@/components/TrendPatternLegend';
import TrendSegmentChart from '@/components/TrendSegmentChart';
import TrendSegmentTable from '@/components/TrendSegmentTable';
import { getTrendSegments, refreshTrendSegments } from '@/lib/api/eda';
import type { Market, TrendSegmentAnalysis } from '@/lib/types/eda';

export default function TrendSegmentView({ markets }: { markets: Market[] }) {
  const [selectedMarket, setSelectedMarket] = useState(markets[0]?.market ?? '');
  const [data, setData] = useState<TrendSegmentAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedMarketRef = useRef(selectedMarket);

  useEffect(() => {
    selectedMarketRef.current = selectedMarket;
  }, [selectedMarket]);

  useEffect(() => {
    if (!selectedMarket) return;
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getTrendSegments(selectedMarket)
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch(() => {
        if (!ignore) setError('구간 분석을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [selectedMarket]);

  function handleRefresh() {
    if (!selectedMarket) return;
    const requestedMarket = selectedMarket;
    setRefreshing(true);
    setError(null);
    refreshTrendSegments(requestedMarket)
      .then((d) => {
        if (selectedMarketRef.current === requestedMarket) setData(d);
      })
      .catch(() => {
        if (selectedMarketRef.current === requestedMarket) setError('갱신에 실패했습니다.');
      })
      .finally(() => setRefreshing(false));
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="max-w-md flex-1">
          <CoinSelect markets={markets} value={selectedMarket} onChange={setSelectedMarket} />
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={!selectedMarket || loading || refreshing}
          className="flex shrink-0 items-center gap-1.5 rounded-md border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
        >
          <RefreshCw className={`size-4 ${refreshing ? 'animate-spin' : ''}`} />
          갱신
        </button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {loading && (
        <p className="text-muted-foreground">
          계산 중입니다. 상장 기간이 긴 코인은 수 초 걸릴 수 있어요...
        </p>
      )}

      {!loading && data && (
        <>
          <p className="text-xs text-muted-foreground">
            적용 임계값: {data.threshold_pct.toFixed(1)}% · 계산 시각:{' '}
            {new Date(data.computed_at).toLocaleString('ko-KR')}
          </p>
          <TrendSegmentChart ohlcv={data.ohlcv} segments={data.segments} />
          <TrendPatternLegend />
          <TrendSegmentTable segments={data.segments} market={selectedMarket} />
        </>
      )}
    </div>
  );
}
