'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import { ApiError } from '@/lib/api/client';
import { getGridSearchEstimate, getMarkets } from '@/lib/api/eda';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { defaultDate, formatCapital, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import type { GridSearchEstimate, GridSearchJobRequest } from '@/lib/types/eda';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME_OPTIONS = TIMEFRAME_CODES.map((timeframe) => ({
  label: formatTimeframe(timeframe),
  timeframe,
}));

const POOL_CATEGORIES = ['오실레이터', '추세', '가격대', '거래량', '거래대금', '시장 심리'] as const;
const DEFAULT_CATEGORIES: string[] = ['오실레이터'];

export interface GridSearchFormInitial {
  market: string;
  timeframe: string;
  capital: string;
  start: string;
  end: string;
  topN: string;
}

interface GridSearchFormProps {
  initial: GridSearchFormInitial;
  disabled: boolean;
  onSubmit: (request: GridSearchJobRequest) => Promise<void>;
}

export default function GridSearchForm({ initial, disabled, onSubmit }: GridSearchFormProps) {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState(initial.market);
  const [timeframe, setTimeframe] = useState(initial.timeframe);
  const [capital, setCapital] = useState(initial.capital.replace(/[^0-9]/g, ''));
  const [start, setStart] = useState(initial.start || defaultDate(60));
  const [end, setEnd] = useState(initial.end || defaultDate(0));
  const [topN, setTopN] = useState(initial.topN);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedCategories, setSelectedCategories] = useState<string[]>(DEFAULT_CATEGORIES);
  const [estimate, setEstimate] = useState<GridSearchEstimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  useEffect(() => {
    getGridSearchEstimate({ categories: selectedCategories, excluded_indicators: [] })
      .then((data) => {
        setEstimate(data);
        setEstimateError(null);
      })
      .catch(() => {
        setEstimateError('예상 조합수를 불러오지 못했습니다.');
        setEstimate(null);
      });
  }, [selectedCategories]);

  function toggleCategory(category: string, checked: boolean) {
    setSelectedCategories((prev) =>
      checked ? [...prev, category] : prev.filter((c) => c !== category)
    );
  }

  async function handleSubmit() {
    setValidationError(null);
    if (start >= end) {
      setValidationError('시작일은 종료일보다 빨라야 합니다.');
      return;
    }
    const topNValue = Number(topN);
    if (!Number.isInteger(topNValue) || topNValue < 1 || topNValue > 50) {
      setValidationError('상위N개는 1~50 사이의 정수여야 합니다.');
      return;
    }
    if (selectedCategories.length === 0) {
      setValidationError('지표 카테고리를 최소 1개 이상 선택하세요.');
      return;
    }

    setSubmitting(true);
    try {
      await onSubmit({
        market, timeframe, capital: Number(capital), start, end, top_n: topNValue,
        indicator_pool: { categories: selectedCategories, excluded_indicators: [] },
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">운용자금</label>
          <div className="flex items-center gap-2">
            <Input
              type="text"
              inputMode="numeric"
              value={formatCapital(capital)}
              onChange={(e) => setCapital(e.target.value.replace(/[^0-9]/g, ''))}
            />
            <span className="text-sm text-muted-foreground">원</span>
          </div>
        </div>
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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[2fr_2fr_1fr]">
        <div>
          <label className="mb-1.5 block text-sm font-medium">시작일</label>
          <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">종료일</label>
          <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">상위N개</label>
          <Input type="number" min={1} max={50} value={topN} onChange={(e) => setTopN(e.target.value)} />
        </div>
      </div>

      <div>
        <label className="mb-1.5 block text-sm font-medium">지표 풀 선택</label>
        <div className="flex flex-wrap gap-3">
          {POOL_CATEGORIES.map((category) => (
            <label key={category} className="flex items-center gap-1.5 text-sm">
              <Checkbox
                checked={selectedCategories.includes(category)}
                onCheckedChange={(checked) => toggleCategory(category, checked === true)}
              />
              {category}
            </label>
          ))}
        </div>
        {estimateError && <p className="mt-1 text-xs text-destructive">{estimateError}</p>}
        {estimate && (
          <p className="mt-1 text-xs text-muted-foreground">
            예상 조합수 {estimate.total_combos.toLocaleString()}개, 약 {Math.round(estimate.estimated_seconds / 60)}분 소요 예상
            {estimate.total_combos > 40000 && (
              <span className="ml-1 text-amber-600">— 조합이 많아 오래 걸릴 수 있습니다. 카테고리를 나눠서 실행하는 것을 추천합니다.</span>
            )}
          </p>
        )}
      </div>

      {validationError && <p className="text-sm text-destructive">{validationError}</p>}
      {disabled && (
        <p className="text-sm text-muted-foreground">
          이미 실행 중인 grid search가 있습니다. 완료 후 새 요청을 시작할 수 있습니다.
        </p>
      )}

      <Button onClick={handleSubmit} disabled={disabled || submitting || !market}>
        {submitting ? '시작하는 중...' : '그리드서치 시작'}
      </Button>
      <p className="text-xs text-muted-foreground">
        9-오실레이터 전 교차 20,700개 조합, 워커 4개 병렬 기준 약 20~30분 소요됩니다.
      </p>
    </div>
  );
}
