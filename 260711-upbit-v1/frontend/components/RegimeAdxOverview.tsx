'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getMarkets, getRegimeAdxOverview } from '@/lib/api/eda';
import type { Market, RegimeAdxLabel, RegimeAdxOverviewItem } from '@/lib/types/eda';
import {
  MAJOR_MARKETS,
  REGIME_LABEL_BG_CLASS as LABEL_BG_CLASS,
  REGIME_UNCLASSIFIED_BG_CLASS as UNCLASSIFIED_BG_CLASS,
  TIMEFRAME,
} from '@/lib/constants/regime';

export default function RegimeAdxOverview({
  selectedMarket, onSelectMarket,
}: { selectedMarket: string; onSelectMarket: (market: string) => void }) {
  const [overview, setOverview] = useState<RegimeAdxOverviewItem[] | null>(null);
  const [markets, setMarkets] = useState<Market[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    Promise.allSettled([getRegimeAdxOverview(TIMEFRAME), getMarkets()]).then(
      ([overviewResult, marketsResult]) => {
        if (ignore) return;
        // getMarkets()는 한글명 표시용 장식 정보일 뿐이고 koreanNameFor에
        // 이미 폴백(market.replace('KRW-', ''))이 있으므로, 이 호출만
        // 실패해도 오버뷰 자체가 정상 로드됐다면 에러로 처리하지 않는다.
        if (marketsResult.status === 'fulfilled') {
          setMarkets(marketsResult.value);
        }
        if (overviewResult.status === 'fulfilled') {
          setOverview(overviewResult.value);
        } else {
          const err = overviewResult.reason;
          setError(err instanceof ApiError ? err.message : '오버뷰를 불러오지 못했습니다.');
        }
      },
    );
    return () => {
      ignore = true;
    };
  }, []);

  function koreanNameFor(market: string): string {
    return markets?.find((m) => m.market === market)?.korean_name ?? market.replace('KRW-', '');
  }

  function labelFor(market: string): RegimeAdxLabel | null {
    return overview?.find((o) => o.market === market)?.label ?? null;
  }

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-4 text-sm font-semibold">메이저 코인 20 현재 장세</h2>
      {error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : !overview ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : (
        <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10">
          {MAJOR_MARKETS.map((market) => {
            const label = labelFor(market);
            const bgClass = label ? LABEL_BG_CLASS[label] : UNCLASSIFIED_BG_CLASS;
            const isSelected = market === selectedMarket;
            return (
              <button
                key={market}
                type="button"
                onClick={() => onSelectMarket(market)}
                className={`rounded-md border px-2 py-1.5 text-left transition ${bgClass} ${isSelected ? 'ring-2 ring-primary' : ''}`}
              >
                <div className="truncate text-[11px] font-medium leading-tight">{koreanNameFor(market)}</div>
                <div className="mt-0.5 flex items-center justify-between gap-1">
                  <span className="text-[10px] text-muted-foreground">{market.replace('KRW-', '')}</span>
                  <span className="text-[11px] font-semibold">{label ?? '미분류'}</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
