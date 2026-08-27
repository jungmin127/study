'use client';

import { useEffect, useState } from 'react';
import RegimeMlCurrentPrediction from '@/components/RegimeMlCurrentPrediction';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));

export default function RegimeDashboard() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  return (
    <div className="space-y-4">
      <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
        <div>
          <div className={SECTION_HEADER_CLASS}>봉데이터</div>
          <div className="flex flex-wrap gap-2 p-3">
            {TIMEFRAME_OPTIONS.map((opt) => (
              <Button
                key={opt.timeframe}
                type="button"
                variant={timeframe === opt.timeframe ? 'default' : 'outline'}
                size="sm"
                onClick={() => setTimeframe(opt.timeframe)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
      {market && <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />}
    </div>
  );
}
