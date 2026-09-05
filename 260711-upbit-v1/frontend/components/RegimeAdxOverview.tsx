'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api/client';
import { getMarkets, getRegimeAdxOverview } from '@/lib/api/eda';
import type { Market, RegimeAdxLabel, RegimeAdxOverviewItem } from '@/lib/types/eda';
import { MAJOR_MARKETS, TIMEFRAME } from '@/lib/constants/regime';

const LABEL_BG_CLASS: Record<RegimeAdxLabel, string> = {
  상승: 'bg-[color:var(--regime-surge-up)]/15 border-[color:var(--regime-surge-up)]/40',
  하락: 'bg-[color:var(--regime-surge-down)]/15 border-[color:var(--regime-surge-down)]/40',
  횡보: 'bg-[color:var(--marker-boundary)]/15 border-[color:var(--marker-boundary)]/40',
};

const UNCLASSIFIED_BG_CLASS = 'bg-[color:var(--trend-unclassified)]/15 border-[color:var(--trend-unclassified)]/40';

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
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-5">
          {MAJOR_MARKETS.map((market) => {
            const label = labelFor(market);
            const bgClass = label ? LABEL_BG_CLASS[label] : UNCLASSIFIED_BG_CLASS;
            const isSelected = market === selectedMarket;
            return (
              <button
                key={market}
                type="button"
                onClick={() => onSelectMarket(market)}
                className={`rounded-lg border p-3 text-left transition ${bgClass} ${isSelected ? 'ring-2 ring-primary' : ''}`}
              >
                <div className="text-xs font-medium">{koreanNameFor(market)}</div>
                <div className="text-xs text-muted-foreground">{market.replace('KRW-', '')}</div>
                <div className="mt-1 text-sm font-semibold">{label ?? '미분류'}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
