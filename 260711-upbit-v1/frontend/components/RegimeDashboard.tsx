'use client';

import { useEffect, useState } from 'react';
import RegimeMlCurrentPrediction, { TRAINED_MARKETS } from '@/components/RegimeMlCurrentPrediction';
import RegimeMlAdminPanel from '@/components/RegimeMlAdminPanel';
import { sortMarkets } from '@/components/CoinSelect';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME = 'minutes60';

export default function RegimeDashboard() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const trained = sortMarkets(
          data.filter((m) => TRAINED_MARKETS.includes(m.market)),
          'change_rate',
          'desc'
        );
        if (trained[0]) setMarket((prev) => prev || trained[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  const trainedMarkets = sortMarkets(
    markets.filter((m) => TRAINED_MARKETS.includes(m.market)),
    'change_rate',
    'desc'
  );

  return (
    <div className="space-y-4">
      <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택 (1시간봉 ML 학습 대상)</label>
          <div className="flex flex-wrap gap-2">
            {trainedMarkets.map((m) => (
              <Button
                key={m.market}
                type="button"
                variant={market === m.market ? 'default' : 'outline'}
                size="sm"
                onClick={() => setMarket(m.market)}
              >
                {m.korean_name}
              </Button>
            ))}
          </div>
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
      </div>
      {market && <RegimeMlCurrentPrediction market={market} timeframe={TIMEFRAME} />}
      <RegimeMlAdminPanel />
    </div>
  );
}
