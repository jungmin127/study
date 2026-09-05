'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getRegimeAdxHistory } from '@/lib/api/eda';
import RegimeAdxChart from '@/components/RegimeAdxChart';
import RegimeAdxSegmentTable from '@/components/RegimeAdxSegmentTable';
import { InfoPopover } from '@/components/ui/info-popover';
import type { RegimeAdxHistory } from '@/lib/types/eda';
import { MAJOR_MARKETS, TIMEFRAME } from '@/lib/constants/regime';

export default function RegimeAdxDetailView({
  market, onMarketChange,
}: { market: string; onMarketChange: (market: string) => void }) {
  const [data, setData] = useState<RegimeAdxHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeAdxHistory({ market, timeframe: TIMEFRAME })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'ADX 장세 구간을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [market]);

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="flex items-center gap-1 text-sm font-semibold">
          ADX 장세 구간 (상승/하락/횡보)
          <InfoPopover>
            <p>
              1시간봉마다 최근 14봉 기준 ADX(추세 강도)와 +DI/-DI(상승·하락 방향성
              우위)를 계산합니다. ADX가 25 이하면 뚜렷한 추세가 없다고 보고
              횡보로, 25를 넘으면 +DI와 -DI 중 더 큰 쪽 방향(상승/하락)으로
              판정합니다. 라벨이 바뀌는 지점은 ADX가 25선을 넘나들거나, 추세
              중 +DI·-DI 우위가 뒤바뀌는 순간입니다.
            </p>
            <p className="mt-2">
              예: 이번 봉만 보면 +DM 6,000·-DM 0으로 상승 쪽이어도, 최근 14봉
              누적 평활값 기준 +DI 21.7·-DI 20.8로 차이가 작고 ADX도
              15.3(25 미만)이면 방향 판단 없이 횡보로 분류됩니다.
            </p>
          </InfoPopover>
        </h2>
        <select
          value={market}
          onChange={(e) => onMarketChange(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-sm"
        >
          {MAJOR_MARKETS.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>
      {loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div className="space-y-4">
          <RegimeAdxChart bars={data.bars} />
          <RegimeAdxSegmentTable segments={data.segments} market={data.market} timeframe={data.timeframe} />
        </div>
      ) : null}
    </div>
  );
}
