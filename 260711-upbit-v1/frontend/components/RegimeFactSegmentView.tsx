'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getRegimeFactSegments } from '@/lib/api/eda';
import RegimeFactChart from '@/components/RegimeFactChart';
import RegimeFactSegmentTable from '@/components/RegimeFactSegmentTable';
import type { RegimeFactAnalysis } from '@/lib/types/eda';

export default function RegimeFactSegmentView({ market, timeframe }: { market: string; timeframe: string }) {
  const [data, setData] = useState<RegimeFactAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!market) return;
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeFactSegments({ market, timeframe })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'fact 장세 구간을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [market, timeframe]);

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold">fact 장세 구간 (하락/하락아님)</h2>
      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div className="space-y-4">
          <RegimeFactChart bars={data.bars} />
          <RegimeFactSegmentTable segments={data.segments} market={data.market} timeframe={data.timeframe} />
        </div>
      ) : null}
    </div>
  );
}
