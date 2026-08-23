'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { defaultDate, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));

export interface RegimeBacktestParams {
  market: string;
  timeframe: string;
  start: string;
  end: string;
}

interface RegimeBacktestFormProps {
  submitting: boolean;
  onSubmit: (params: RegimeBacktestParams) => void;
}

export default function RegimeBacktestForm({ submitting, onSubmit }: RegimeBacktestFormProps) {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');
  const [timeframe, setTimeframe] = useState('minutes60');
  const [start, setStart] = useState(defaultDate(365));
  const [end, setEnd] = useState(defaultDate(0));
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  function handleSubmit() {
    setValidationError(null);
    if (start >= end) {
      setValidationError('시작일은 종료일보다 빨라야 합니다.');
      return;
    }
    onSubmit({ market, timeframe, start, end });
  }

  return (
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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-sm font-medium">시작일</label>
          <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">종료일</label>
          <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      </div>

      {validationError && <p className="text-sm text-destructive">{validationError}</p>}

      <Button onClick={handleSubmit} disabled={submitting || !market}>
        {submitting ? '조회 중...' : '조회'}
      </Button>
    </div>
  );
}
