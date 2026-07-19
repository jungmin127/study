'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const MARKETS = ['KRW-BTC', 'KRW-ETH'];

const TIMEFRAMES = [
  { value: 'days', label: '일봉' },
  { value: 'minutes240', label: '4시간봉' },
  { value: 'minutes60', label: '1시간봉' },
  { value: 'minutes15', label: '15분봉' },
];

const DUMMY_SIGNALS = ['macd_cross', 'rsi_zone', 'sma_cross', 'bollinger_band'];

const SELECT_CLASS =
  'h-10 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-ring';

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export default function BacktestRunForm() {
  const [market, setMarket] = useState(MARKETS[0]);
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[0].value);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([DUMMY_SIGNALS[0]]);
  const [start, setStart] = useState(defaultDate(90));
  const [end, setEnd] = useState(defaultDate(0));

  function toggleSignal(key: string) {
    setSelectedSignals((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      <Card className="shadow-sm">
        <CardHeader className="border-b bg-slate-50 py-3 dark:bg-slate-800">
          <CardTitle className="text-sm font-semibold">기본 설정</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 pt-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium">코인</label>
            <select className={SELECT_CLASS} value={market} onChange={(e) => setMarket(e.target.value)}>
              {MARKETS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">봉타입</label>
            <select
              className={SELECT_CLASS}
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf.value} value={tf.value}>
                  {tf.label}
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      <Card className="shadow-sm">
        <CardHeader className="border-b bg-slate-50 py-3 dark:bg-slate-800">
          <CardTitle className="text-sm font-semibold">전략 선택</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2 pt-4">
          {DUMMY_SIGNALS.map((key) => {
            const selected = selectedSignals.includes(key);
            return (
              <button
                key={key}
                type="button"
                onClick={() => toggleSignal(key)}
                className={
                  selected
                    ? 'rounded-full border-2 border-primary bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground shadow-sm'
                    : 'rounded-full border-2 border-border bg-background px-4 py-1.5 text-sm font-medium text-foreground hover:bg-muted'
                }
              >
                {key}
              </button>
            );
          })}
        </CardContent>
      </Card>

      <Card className="shadow-sm">
        <CardHeader className="border-b bg-slate-50 py-3 dark:bg-slate-800">
          <CardTitle className="text-sm font-semibold">운용 기간</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 pt-4">
          <div className="flex items-center gap-2">
            <input
              type="date"
              className="h-10 rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-ring"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
            <span className="text-sm text-muted-foreground">~</span>
            <input
              type="date"
              className="h-10 rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-ring"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            기간이 길고 봉타입이 짧을수록 최초 조회 시 시간이 걸릴 수 있습니다.
          </p>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          선택한 조건으로 백테스트를 실행합니다.
        </p>
        <Button
          type="button"
          size="lg"
          className="px-6 shadow-sm"
          onClick={() =>
            console.log('run backtest (mock)', { market, timeframe, selectedSignals, start, end })
          }
        >
          실행
        </Button>
      </div>

      <p className="invisible text-sm text-red-600 dark:text-red-400">에러 자리</p>
    </div>
  );
}
