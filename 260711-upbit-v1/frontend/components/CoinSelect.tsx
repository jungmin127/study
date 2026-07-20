'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Market } from '@/lib/types/eda';
import { getMarkets } from '@/lib/api/eda';
import { INPUT_CLASS } from '@/lib/ui-classes';

export type MarketSortKey = 'change_rate' | 'trade_price_24h';
type SortDir = 'asc' | 'desc';

export function sortMarkets(list: Market[], key: MarketSortKey, dir: SortDir): Market[] {
  const factor = dir === 'asc' ? 1 : -1;
  return [...list].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return (av - bv) * factor;
  });
}

function changeColorClass(rate: number | null): string {
  if (!rate) return 'text-foreground';
  return rate > 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}

function formatPrice(price: number | null): string {
  if (price === null) return '-';
  if (price === 0) return '0';
  if (price >= 100) return Math.round(price).toLocaleString('ko-KR');
  const magnitude = Math.floor(Math.log10(Math.abs(price)));
  const decimals = Math.max(0, 2 - magnitude);
  return price.toLocaleString('ko-KR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatChangeRate(rate: number | null): string {
  if (rate === null) return '-';
  return `${(Math.abs(rate) * 100).toFixed(2)}%`;
}

function formatChangePrice(price: number | null): string {
  if (price === null) return '-';
  return formatPrice(Math.abs(price));
}

function formatTradePrice24h(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value / 1_000_000).toLocaleString('ko-KR')}백만`;
}

interface CoinSelectProps {
  markets: Market[];
  value: string;
  onChange: (market: string) => void;
}

export default function CoinSelect({ markets, value, onChange }: CoinSelectProps) {
  const [open, setOpen] = useState(false);
  const [liveMarkets, setLiveMarkets] = useState(markets);
  const [refreshing, setRefreshing] = useState(false);
  const [sortKey, setSortKey] = useState<MarketSortKey>('change_rate');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLiveMarkets(markets);
  }, [markets]);

  const sorted = useMemo(() => sortMarkets(liveMarkets, sortKey, sortDir), [liveMarkets, sortKey, sortDir]);
  const selected = liveMarkets.find((m) => m.market === value) ?? null;

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function handleToggleOpen() {
    const willOpen = !open;
    setOpen(willOpen);
    if (willOpen) {
      setRefreshing(true);
      getMarkets()
        .then(setLiveMarkets)
        .catch(() => {})
        .finally(() => setRefreshing(false));
    }
  }

  function toggleSort(key: MarketSortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function sortIndicator(key: MarketSortKey): string {
    if (sortKey !== key) return '⇅';
    return sortDir === 'desc' ? '▼' : '▲';
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        className={`${INPUT_CLASS} flex w-full items-center justify-between gap-3`}
        onClick={handleToggleOpen}
        disabled={liveMarkets.length === 0}
      >
        {selected ? (
          <>
            <span className="truncate font-medium">
              {selected.korean_name} <span className="text-xs text-muted-foreground">({selected.market})</span>
            </span>
            <span className="flex shrink-0 items-center gap-3 tabular-nums">
              <span className="font-semibold">{formatPrice(selected.price)}</span>
              <span className={`font-semibold ${changeColorClass(selected.change_rate)}`}>
                {formatChangeRate(selected.change_rate)}
              </span>
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">불러오는 중...</span>
        )}
      </button>

      {open && (
        <div className="absolute z-20 mt-1 w-full rounded-md border bg-background shadow-lg">
          <div className="grid grid-cols-[2fr_1fr_1fr_1fr] gap-2 border-b bg-slate-50 px-3 py-2 text-xs font-medium text-muted-foreground dark:bg-slate-800">
            <span>{refreshing ? '새로고침 중...' : '한글명'}</span>
            <span className="text-right">현재가</span>
            <button
              type="button"
              className="flex items-center justify-end gap-1 hover:text-foreground"
              onClick={() => toggleSort('change_rate')}
            >
              전일대비 {sortIndicator('change_rate')}
            </button>
            <button
              type="button"
              className="flex items-center justify-end gap-1 hover:text-foreground"
              onClick={() => toggleSort('trade_price_24h')}
            >
              거래대금 {sortIndicator('trade_price_24h')}
            </button>
          </div>
          <div className="max-h-80 overflow-y-auto">
            {sorted.map((m) => (
              <button
                key={m.market}
                type="button"
                className={`grid w-full grid-cols-[2fr_1fr_1fr_1fr] items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800 ${
                  m.market === value ? 'bg-slate-100 dark:bg-slate-800' : ''
                }`}
                onClick={() => {
                  onChange(m.market);
                  setOpen(false);
                }}
              >
                <span>
                  <span className="block font-medium">{m.korean_name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {m.market.replace('KRW-', '')}/KRW
                  </span>
                </span>
                <span className="text-right font-semibold tabular-nums">{formatPrice(m.price)}</span>
                <span className={`text-right tabular-nums ${changeColorClass(m.change_rate)}`}>
                  <span className="block font-semibold">{formatChangeRate(m.change_rate)}</span>
                  <span className="block text-xs">{formatChangePrice(m.change_price)}</span>
                </span>
                <span className="text-right tabular-nums text-muted-foreground">
                  {formatTradePrice24h(m.trade_price_24h)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
