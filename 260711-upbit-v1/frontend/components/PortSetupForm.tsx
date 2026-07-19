'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';

const MARKETS = ['KRW-BTC', 'KRW-ETH'];

const DUMMY_SIGNALS = ['macd_cross', 'rsi_zone', 'sma_cross', 'bollinger_band'];

const CANDLE_UNITS = ['15분', '30분', '1시간', '1일'];

const INPUT_CLASS =
  'h-10 rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-ring';

const SELECT_CLASS = `${INPUT_CLASS} w-full`;

const SECTION_HEADER_CLASS =
  'border-b bg-slate-50 px-4 py-2 text-sm font-medium dark:bg-slate-800';

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function StrategyConditionBox({
  label,
  selected,
  thresholds,
  onToggle,
  onThresholdChange,
}: {
  label: string;
  selected: string[];
  thresholds: Record<string, string>;
  onToggle: (key: string) => void;
  onThresholdChange: (key: string, value: string) => void;
}) {
  return (
    <div>
      <div className={SECTION_HEADER_CLASS}>{label}</div>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap gap-2">
          {DUMMY_SIGNALS.map((key) => {
            const isSelected = selected.includes(key);
            return (
              <button
                key={key}
                type="button"
                onClick={() => onToggle(key)}
                className={
                  isSelected
                    ? 'rounded-full border-2 border-primary bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground shadow-sm'
                    : 'rounded-full border-2 border-border bg-background px-4 py-1.5 text-sm font-medium text-foreground hover:bg-muted'
                }
              >
                {key}
              </button>
            );
          })}
        </div>
        {selected.length > 0 && (
          <div className="space-y-2 border-t pt-3">
            {selected.map((key) => (
              <div key={key} className="flex items-center gap-2">
                <span className="w-32 shrink-0 text-sm text-muted-foreground">{key}</span>
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="threshold"
                  className={`${INPUT_CLASS} w-32`}
                  value={thresholds[key] ?? ''}
                  onChange={(e) => onThresholdChange(key, e.target.value)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function PortSetupForm() {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [market, setMarket] = useState(MARKETS[0]);
  const [buySignals, setBuySignals] = useState<string[]>([DUMMY_SIGNALS[0]]);
  const [buyThresholds, setBuyThresholds] = useState<Record<string, string>>({});
  const [sellSignals, setSellSignals] = useState<string[]>([]);
  const [sellThresholds, setSellThresholds] = useState<Record<string, string>>({});
  const [capital, setCapital] = useState('1000000');
  const [candleUnit, setCandleUnit] = useState(CANDLE_UNITS[0]);
  const [tickVerification, setTickVerification] = useState(false);
  const [startDate, setStartDate] = useState(defaultDate(90));
  const [startTime, setStartTime] = useState('00:00');
  const [endDate, setEndDate] = useState(defaultDate(0));
  const [endTime, setEndTime] = useState('00:00');

  function toggleBuySignal(key: string) {
    setBuySignals((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  function toggleSellSignal(key: string) {
    setSellSignals((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  function updateBuyThreshold(key: string, value: string) {
    setBuyThresholds((prev) => ({ ...prev, [key]: value }));
  }

  function updateSellThreshold(key: string, value: string) {
    setSellThresholds((prev) => ({ ...prev, [key]: value }));
  }

  function handleRun() {
    console.log('run backtest (mock)', {
      title,
      description,
      market,
      buySignals,
      buyThresholds,
      sellSignals,
      sellThresholds,
      capital,
      candleUnit,
      tickVerification,
      startDate,
      startTime,
      endDate,
      endTime,
    });
  }

  return (
    <div className="max-w-4xl space-y-6 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-3 gap-6">
        <div>
          <label className="mb-1.5 block text-sm font-medium">포트 제목</label>
          <input
            type="text"
            placeholder="포트폴리오 제목을 입력해 주세요."
            className={`${INPUT_CLASS} w-full`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">
            포트 설명 <span className="font-normal text-muted-foreground">(선택사항)</span>
          </label>
          <input
            type="text"
            placeholder="포트폴리오에 대한 설명을 100자 이내로 남겨주세요."
            className={`${INPUT_CLASS} w-full`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <select className={SELECT_CLASS} value={market} onChange={(e) => setMarket(e.target.value)}>
            {MARKETS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">전략 선택</h2>
        <div className="grid grid-cols-2 divide-x rounded-md border">
          <StrategyConditionBox
            label="매수 조건"
            selected={buySignals}
            thresholds={buyThresholds}
            onToggle={toggleBuySignal}
            onThresholdChange={updateBuyThreshold}
          />
          <StrategyConditionBox
            label="매도 조건"
            selected={sellSignals}
            thresholds={sellThresholds}
            onToggle={toggleSellSignal}
            onThresholdChange={updateSellThreshold}
          />
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">기본 조건</h2>
        <div className="grid grid-cols-[1fr_1fr_2fr] divide-x rounded-md border">
          <div>
            <div className={SECTION_HEADER_CLASS}>운용자금</div>
            <div className="flex items-center gap-2 p-4">
              <input
                type="text"
                inputMode="numeric"
                className={`${INPUT_CLASS} w-full`}
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
              <span className="text-sm text-muted-foreground">원</span>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>봉데이터 선택</div>
            <div className="space-y-2 p-4">
              <select
                className={SELECT_CLASS}
                value={candleUnit}
                onChange={(e) => setCandleUnit(e.target.value)}
              >
                {CANDLE_UNITS.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={tickVerification}
                  onChange={(e) => setTickVerification(e.target.checked)}
                />
                틱 데이터 검증
              </label>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>운용기간</div>
            <div className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="date"
                  className={INPUT_CLASS}
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
                <input
                  type="time"
                  className={INPUT_CLASS}
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                />
                <span className="text-sm text-muted-foreground">부터</span>
                <input
                  type="date"
                  className={INPUT_CLASS}
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
                <input
                  type="time"
                  className={INPUT_CLASS}
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                />
                <span className="text-sm text-muted-foreground">까지</span>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <p className="text-xs text-muted-foreground">기간 설정 후 다음 단계에서 상세 계산 결과가 표시됩니다.</p>
                <button
                  type="button"
                  className="whitespace-nowrap rounded-md border border-input bg-background px-2 py-1 text-xs hover:bg-slate-50 dark:hover:bg-slate-800"
                  onClick={() => {
                    setStartDate(defaultDate(90));
                    setEndDate(defaultDate(0));
                  }}
                >
                  최근 최대 기간 설정
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t pt-4">
        <Button type="button" variant="outline" onClick={() => console.log('cancel (mock)')}>
          취소
        </Button>
        <Button type="button" onClick={handleRun}>
          백테스트 실행
        </Button>
      </div>
    </div>
  );
}
