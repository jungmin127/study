'use client';

import { useState } from 'react';
import RegimeBacktestForm, { type RegimeBacktestParams } from '@/components/RegimeBacktestForm';
import RegimeCurrentPrediction from '@/components/RegimeCurrentPrediction';
import RegimeMlCurrentPrediction from '@/components/RegimeMlCurrentPrediction';
import RegimeChart from '@/components/RegimeChart';
import RegimeAccuracyReport from '@/components/RegimeAccuracyReport';
import { ApiError } from '@/lib/api/client';
import { getRegimeBacktest } from '@/lib/api/eda';
import type { RegimeBacktestResult } from '@/lib/types/eda';

export default function RegimeDashboard() {
  const [result, setResult] = useState<RegimeBacktestResult | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(params: RegimeBacktestParams) {
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const data = await getRegimeBacktest(params);
      setMarket(params.market);
      setTimeframe(params.timeframe);
      setResult(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '장세 판별 결과를 불러오지 못했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <RegimeBacktestForm submitting={submitting} onSubmit={handleSubmit} />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {result && result.candles.length === 0 && (
        <p className="text-sm text-muted-foreground">선택한 기간에 데이터가 부족합니다.</p>
      )}
      {result && result.candles.length > 0 && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <RegimeCurrentPrediction result={result} market={market} timeframe={timeframe} />
            <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />
          </div>
          <RegimeChart candles={result.candles} timeframe={timeframe} />
          <RegimeAccuracyReport report={result} />
        </>
      )}
    </div>
  );
}
