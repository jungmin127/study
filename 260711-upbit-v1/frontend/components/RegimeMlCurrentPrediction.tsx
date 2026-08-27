'use client';

import { useEffect, useState } from 'react';
import type { MlCurrentPrediction, RegimeCategory } from '@/lib/types/eda';
import { ApiError } from '@/lib/api/client';
import { getRegimeMlCurrentPrediction } from '@/lib/api/eda';
import { formatDateTime, formatTimeframe } from '@/lib/format';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];
const TRAINED_MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'];

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '급상승':
      return '--regime-surge-up';
    case '완만상승':
      return '--regime-mild-up';
    case '횡보':
      return '--marker-boundary';
    case '완만하락':
      return '--regime-mild-down';
    case '급하락':
      return '--regime-surge-down';
  }
}

interface RegimeMlCurrentPredictionProps {
  market: string;
  timeframe: string;
}

export default function RegimeMlCurrentPrediction({ market, timeframe }: RegimeMlCurrentPredictionProps) {
  const [data, setData] = useState<MlCurrentPrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (timeframe !== 'minutes60' || !market || !TRAINED_MARKETS.includes(market)) {
      setData(null);
      setError(null);
      return;
    }
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeMlCurrentPrediction({ market, timeframe })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'ML 예측을 불러오지 못했습니다.');
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
      <h2 className="mb-3 text-sm font-semibold">ML 현재예측</h2>
      {timeframe !== 'minutes60' ? (
        <p className="text-sm text-muted-foreground">ML은 1시간봉 전용입니다.</p>
      ) : !TRAINED_MARKETS.includes(market) ? (
        <p className="text-sm text-muted-foreground">이 모델은 {TRAINED_MARKETS.join('/')}로만 학습되어 있습니다.</p>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold">{data.predicted_category}</span>
            <span className="text-sm text-muted-foreground">
              확신도 {(data.probs[data.predicted_category] * 100).toFixed(1)}%
            </span>
          </div>
          <div className="mb-3 space-y-1.5">
            {CATEGORY_ORDER.map((label) => (
              <div key={label} className="flex items-center gap-2 text-xs">
                <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(data.probs[label] * 100).toFixed(1)}%`,
                      backgroundColor: `var(${categoryVarName(label)})`,
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right tabular-nums">
                  {(data.probs[label] * 100).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {market} {formatTimeframe(timeframe)} 기준, {formatDateTime(data.bar_time)} 봉 데이터. (모델: {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index})
          </p>
        </>
      ) : null}
    </div>
  );
}
