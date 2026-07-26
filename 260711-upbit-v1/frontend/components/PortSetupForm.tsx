'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { CalendarRange, Play, TriangleAlert, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import StrategyConditionBuilder from '@/components/StrategyConditionBuilder';
import type { ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem, Market } from '@/lib/types/eda';
import { getIndicatorCatalog, getMarkets, runBacktest, validateBacktest } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';

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

function parsePreset(searchParams: URLSearchParams) {
  function parseConditionGroup(raw: string | null): ConditionGroup {
    if (!raw) return EMPTY_CONDITION_GROUP;
    try {
      return JSON.parse(raw) as ConditionGroup;
    } catch {
      return EMPTY_CONDITION_GROUP;
    }
  }

  return {
    market: searchParams.get('market') ?? '',
    timeframe: searchParams.get('timeframe') ?? CANDLE_UNITS[0].timeframe,
    startDate: searchParams.get('start') ?? defaultDate(90),
    startTime: searchParams.get('startTime') ?? '00:00',
    endDate: searchParams.get('end') ?? defaultDate(0),
    endTime: searchParams.get('endTime') ?? '00:00',
    buyConditions: parseConditionGroup(searchParams.get('buy')),
    sellConditions: parseConditionGroup(searchParams.get('sell')),
  };
}

export default function PortSetupForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [preset] = useState(() => parsePreset(searchParams));

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState(preset.market);

  const [catalog, setCatalog] = useState<IndicatorCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [buyConditions, setBuyConditions] = useState<ConditionGroup>(preset.buyConditions);
  const [sellConditions, setSellConditions] = useState<ConditionGroup>(preset.sellConditions);
  const [capital, setCapital] = useState('1000000');
  const [timeframe, setTimeframe] = useState(preset.timeframe);
  const [startDate, setStartDate] = useState(preset.startDate);
  const [startTime, setStartTime] = useState(preset.startTime);
  const [endDate, setEndDate] = useState(preset.endDate);
  const [endTime, setEndTime] = useState(preset.endTime);

  const [submitting, setSubmitting] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[] | null>(null);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
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

  const selectedMarketPrice = markets.find((m) => m.market === market)?.price ?? null;

  return (
    <div className="max-w-5xl space-y-6 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-[1fr_1fr_4fr]">
        <div>
          <label className="mb-1.5 block text-sm font-medium">포트 제목</label>
          <Input
            type="text"
            placeholder="포트폴리오 제목을 입력해 주세요."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">
            포트 설명 <span className="font-normal text-muted-foreground">(선택사항)</span>
          </label>
          <Input
            type="text"
            placeholder="포트폴리오에 대한 설명을 100자 이내로 남겨주세요."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && (
            <p className="mt-1 flex items-center gap-1 text-xs text-destructive">
              <TriangleAlert className="size-3.5" />
              {marketsError}
            </p>
          )}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">전략 선택</h2>
        {catalogError && (
          <p className="mb-2 flex items-center gap-1 text-xs text-destructive">
            <TriangleAlert className="size-3.5" />
            {catalogError}
          </p>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-md border">
            <StrategyConditionBuilder
              label="매수 조건"
              group={buyConditions}
              catalog={catalog.filter((c) => !c.sellOnly)}
              currentPrice={selectedMarketPrice}
              onChange={setBuyConditions}
            />
          </div>
          <div className="rounded-md border">
            <StrategyConditionBuilder
              label="매도 조건"
              group={sellConditions}
              catalog={catalog}
              currentPrice={selectedMarketPrice}
              onChange={setSellConditions}
            />
          </div>
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">기본 조건</h2>
        <div className="grid grid-cols-1 divide-y rounded-md border sm:grid-cols-[1fr_1fr_3fr] sm:divide-x sm:divide-y-0">
          <div>
            <div className={SECTION_HEADER_CLASS}>운용자금</div>
            <div className="flex items-center gap-2 p-4">
              <Input
                type="text"
                inputMode="numeric"
                value={formatCapital(capital)}
                onChange={(e) => setCapital(e.target.value.replace(/[^0-9]/g, ''))}
              />
              <span className="text-sm text-muted-foreground">원</span>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>봉데이터 선택</div>
            <div className="space-y-2 p-4">
              <Select value={timeframe} onValueChange={(value) => value !== null && setTimeframe(value)}>
                <SelectTrigger className="w-full">
                  <SelectValue>
                    {(value: string | null) => CANDLE_UNITS.find((u) => u.timeframe === value)?.label ?? ''}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {CANDLE_UNITS.map((u) => (
                    <SelectItem key={u.timeframe} value={u.timeframe}>
                      {u.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>운용기간</div>
            <div className="space-y-2 p-4">
              <div className="flex flex-nowrap items-center gap-2 overflow-x-auto">
                <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                <Input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
                <span className="text-sm text-muted-foreground">~</span>
                <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                <Input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <p className="text-xs text-muted-foreground">기간이 길고 봉타입이 짧을수록 최초 조회 시 시간이 걸릴 수 있습니다.</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setStartDate(defaultDate(90));
                    setEndDate(defaultDate(0));
                  }}
                >
                  <CalendarRange className="size-3.5" />
                  최근 최대 기간 설정
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t pt-4">
        <Button type="button" variant="outline" onClick={() => console.log('cancel (mock)')}>
          <X className="size-4" />
          취소
        </Button>
        <Button type="button" onClick={handleRun} disabled={submitting || !market}>
          <Play className="size-4" />
          {submitting ? '검증 중...' : '백테스트 실행'}
        </Button>
      </div>

      <AlertDialog open={!!validationErrors} onOpenChange={(open) => !open && setValidationErrors(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-1.5 text-destructive">
              <TriangleAlert className="size-4" />
              백테스트를 실행할 수 없습니다
            </AlertDialogTitle>
          </AlertDialogHeader>
          <ul className="mb-4 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {(validationErrors ?? []).map((error, i) => (
              <li key={i}>{error}</li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setValidationErrors(null)}>확인</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
