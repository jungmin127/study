'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import type { Market } from '@/lib/types/eda';
import { getMarkets } from '@/lib/api/eda';
import { INPUT_CLASS } from '@/lib/ui-classes';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from '@/components/ui/command';

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
  const [query, setQuery] = useState('');

  useEffect(() => {
    setLiveMarkets(markets);
  }, [markets]);

  const sorted = useMemo(() => sortMarkets(liveMarkets, sortKey, sortDir), [liveMarkets, sortKey, sortDir]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter(
      (m) => m.korean_name.toLowerCase().includes(q) || m.market.replace('KRW-', '').toLowerCase().includes(q)
    );
  }, [sorted, query]);
  const selected = liveMarkets.find((m) => m.market === value) ?? null;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setRefreshing(true);
      getMarkets()
        .then(setLiveMarkets)
        .catch(() => {})
        .finally(() => setRefreshing(false));
    } else {
      setQuery('');
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

  function SortIcon({ sortKeyOf }: { sortKeyOf: MarketSortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      {/* base-ui's Popover.Trigger already renders a native <button> and does not support
          Radix-style `asChild` composition (confirmed via popover.tsx / base-ui docs: Trigger
          only exposes a `render` prop for element replacement). Since we just need a plain
          button with our own className/disabled, we pass those props straight to
          PopoverTrigger instead of wrapping a child button in `asChild`. */}
      <PopoverTrigger
        type="button"
        className={`${INPUT_CLASS} flex w-full items-center justify-between gap-3`}
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
      </PopoverTrigger>
      {/*
        The brief's sample used Radix's `w-[var(--radix-popover-trigger-width)]` to sync the
        popover width to the trigger. This project's popover.tsx is backed by @base-ui/react,
        whose Positioner exposes the anchor's width as `--anchor-width` instead (confirmed in
        node_modules/@base-ui/react docs: "Positioner CSS Variables" table). That variable is
        set on the Positioner element and inherits down to the Popup, so we reference it here
        with Tailwind v4's CSS-variable-shorthand syntax (`w-(--anchor-width)`), the same
        convention popover.tsx itself uses for `origin-(--transform-origin)`. `min-w-80` is kept
        as a fallback for the first paint / narrow triggers.
      */}
      <PopoverContent className="w-(--anchor-width) min-w-80 p-0" align="start">
        <Command shouldFilter={false}>
          <div className="border-b">
            <CommandInput
              placeholder="한글명 또는 티커로 검색 (예: 비트코인, BTC)"
              value={query}
              onValueChange={setQuery}
            />
          </div>
          <div className="grid grid-cols-[2fr_1fr_1fr_1fr] gap-2 border-b bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
            <span>{refreshing ? '새로고침 중...' : '한글명'}</span>
            <span className="text-right">현재가</span>
            <button type="button" className="flex items-center justify-end gap-1 hover:text-foreground" onClick={() => toggleSort('change_rate')}>
              전일대비 <SortIcon sortKeyOf="change_rate" />
            </button>
            <button type="button" className="flex items-center justify-end gap-1 hover:text-foreground" onClick={() => toggleSort('trade_price_24h')}>
              거래대금 <SortIcon sortKeyOf="trade_price_24h" />
            </button>
          </div>
          <CommandList className="max-h-80">
            <CommandEmpty>검색 결과가 없습니다.</CommandEmpty>
            {filtered.map((m) => (
              <CommandItem
                key={m.market}
                value={m.market}
                onSelect={() => {
                  onChange(m.market);
                  setOpen(false);
                }}
                className={m.market === value ? 'bg-muted' : ''}
              >
                {/*
                  CommandItem (components/ui/command.tsx) always appends a trailing CheckIcon
                  after its children, shown via a `data-checked` state we don't set here. That
                  icon still participates in layout even while invisible. CommandItem's own
                  className is a flex row by default, so nesting our 4-column grid in a single
                  `w-full` child (instead of overriding CommandItem's own class to `grid`) keeps
                  the hidden CheckIcon as a sibling flex item on the right rather than an
                  overflow item that wraps onto a new implicit grid row.
                */}
                <div className="grid w-full grid-cols-[2fr_1fr_1fr_1fr] items-center gap-2">
                  <span>
                    <span className="block font-medium">{m.korean_name}</span>
                    <span className="block text-xs text-muted-foreground">{m.market.replace('KRW-', '')}/KRW</span>
                  </span>
                  <span className="text-right font-semibold tabular-nums">{formatPrice(m.price)}</span>
                  <span className={`text-right tabular-nums ${changeColorClass(m.change_rate)}`}>
                    <span className="block font-semibold">{formatChangeRate(m.change_rate)}</span>
                    <span className="block text-xs">{formatChangePrice(m.change_price)}</span>
                  </span>
                  <span className="text-right tabular-nums text-muted-foreground">
                    {formatTradePrice24h(m.trade_price_24h)}
                  </span>
                </div>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
