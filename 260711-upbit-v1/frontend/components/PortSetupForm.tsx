'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import StrategyConditionBuilder from '@/components/StrategyConditionBuilder';
import type { ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem, Market } from '@/lib/types/eda';
import { getIndicatorCatalog, getMarkets, runBacktest, validateBacktest } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';
import { INPUT_CLASS, SELECT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';

const CANDLE_UNITS = [
  { label: '15분', timeframe: 'minutes15' },
  { label: '30분', timeframe: 'minutes30' },
  { label: '1시간', timeframe: 'minutes60' },
  { label: '1일', timeframe: 'days' },
];

const EMPTY_CONDITION_GROUP: ConditionGroup = { type: 'AND', conditions: [] };

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function formatCapital(digits: string): string {
  if (!digits) return '';
  return Number(digits).toLocaleString('ko-KR');
}

export default function PortSetupForm() {
  const router = useRouter();

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');

  const [catalog, setCatalog] = useState<IndicatorCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [buyConditions, setBuyConditions] = useState<ConditionGroup>(EMPTY_CONDITION_GROUP);
  const [sellConditions, setSellConditions] = useState<ConditionGroup>(EMPTY_CONDITION_GROUP);
  const [capital, setCapital] = useState('1000000');
  const [timeframe, setTimeframe] = useState(CANDLE_UNITS[0].timeframe);
  const [tickVerification, setTickVerification] = useState(false);
  const [startDate, setStartDate] = useState(defaultDate(90));
  const [startTime, setStartTime] = useState('00:00');
  const [endDate, setEndDate] = useState(defaultDate(0));
  const [endTime, setEndTime] = useState('00:00');

  const [submitting, setSubmitting] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[] | null>(null);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        const sorted = [...data].sort((a, b) => a.korean_name.localeCompare(b.korean_name, 'ko'));
        setMarkets(sorted);
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));

    getIndicatorCatalog()
      .then(setCatalog)
      .catch((err) => setCatalogError(err instanceof ApiError ? err.message : '지표 목록을 불러오지 못했습니다.'));
  }, []);

  async function handleRun() {
    const request = {
      market,
      timeframe,
      start: startDate,
      end: endDate,
      initial_capital: Number(capital),
      buy_conditions: buyConditions,
      sell_conditions: sellConditions,
      title: title || null,
      description: description || null,
    };

    setSubmitting(true);
    try {
      const validation = await validateBacktest(request);
      if (!validation.valid) {
        setValidationErrors(validation.errors);
        return;
      }

      const { run_id } = await runBacktest(request);
      router.push(`/backtests/${run_id}`);
    } catch (err) {
      setValidationErrors([err instanceof ApiError ? err.message : '백테스트 실행 중 오류가 발생했습니다.']);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-5xl space-y-6 rounded-xl border p-6 shadow-sm">
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
          <select
            className={SELECT_CLASS}
            value={market}
            onChange={(e) => setMarket(e.target.value)}
            disabled={markets.length === 0}
          >
            {markets.length === 0 && <option value="">불러오는 중...</option>}
            {markets.map((m) => (
              <option key={m.market} value={m.market}>
                {m.korean_name} ({m.market})
              </option>
            ))}
          </select>
          {marketsError && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{marketsError}</p>}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">전략 선택</h2>
        {catalogError && <p className="mb-2 text-xs text-red-600 dark:text-red-400">{catalogError}</p>}
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-md border">
            <StrategyConditionBuilder
              label="매수 조건"
              group={buyConditions}
              catalog={catalog}
              onChange={setBuyConditions}
            />
          </div>
          <div className="rounded-md border">
            <StrategyConditionBuilder
              label="매도 조건"
              group={sellConditions}
              catalog={catalog}
              onChange={setSellConditions}
            />
          </div>
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">기본 조건</h2>
        <div className="grid grid-cols-[1fr_1fr_3fr] divide-x rounded-md border">
          <div>
            <div className={SECTION_HEADER_CLASS}>운용자금</div>
            <div className="flex items-center gap-2 p-4">
              <input
                type="text"
                inputMode="numeric"
                className={`${INPUT_CLASS} w-full`}
                value={formatCapital(capital)}
                onChange={(e) => setCapital(e.target.value.replace(/[^0-9]/g, ''))}
              />
              <span className="text-sm text-muted-foreground">원</span>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>봉데이터 선택</div>
            <div className="space-y-2 p-4">
              <select
                className={SELECT_CLASS}
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
              >
                {CANDLE_UNITS.map((u) => (
                  <option key={u.timeframe} value={u.timeframe}>
                    {u.label}
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
              <div className="flex flex-nowrap items-center gap-2">
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
                <span className="text-sm text-muted-foreground">~</span>
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
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <p className="text-xs text-muted-foreground">기간이 길고 봉타입이 짧을수록 최초 조회 시 시간이 걸릴 수 있습니다.</p>
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
        <Button type="button" onClick={handleRun} disabled={submitting || !market}>
          {submitting ? '검증 중...' : '백테스트 실행'}
        </Button>
      </div>

      {validationErrors && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg border bg-background p-6 shadow-lg">
            <h3 className="mb-3 text-sm font-semibold text-red-600 dark:text-red-400">
              백테스트를 실행할 수 없습니다
            </h3>
            <ul className="mb-4 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {validationErrors.map((error, i) => (
                <li key={i}>{error}</li>
              ))}
            </ul>
            <div className="flex justify-end">
              <Button type="button" onClick={() => setValidationErrors(null)}>
                확인
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
