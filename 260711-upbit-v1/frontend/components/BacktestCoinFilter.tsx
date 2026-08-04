'use client';

import { useMemo, useState } from 'react';
import { INPUT_CLASS } from '@/lib/ui-classes';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from '@/components/ui/command';

export interface CoinFilterOption {
  market: string;
  koreanName?: string;
}

interface BacktestCoinFilterProps {
  options: CoinFilterOption[];
  value: string | null;
  onChange: (market: string | null) => void;
}

export default function BacktestCoinFilter({ options, value, onChange }: BacktestCoinFilterProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        (o.koreanName?.toLowerCase().includes(q) ?? false) ||
        o.market.replace('KRW-', '').toLowerCase().includes(q)
    );
  }, [options, query]);

  const selected = options.find((o) => o.market === value) ?? null;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) setQuery('');
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger type="button" className={`${INPUT_CLASS} flex w-56 items-center justify-between gap-2`}>
        <span className="truncate text-sm">
          {selected ? (
            <>
              {selected.market}
              {selected.koreanName && (
                <span className="text-xs text-muted-foreground"> ({selected.koreanName})</span>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">코인별 필터</span>
          )}
        </span>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput placeholder="한글명 또는 티커로 검색" value={query} onValueChange={setQuery} />
          <CommandList className="max-h-72">
            <CommandEmpty>검색 결과가 없습니다.</CommandEmpty>
            <CommandItem
              value="__all__"
              onSelect={() => {
                onChange(null);
                setOpen(false);
              }}
              className={value === null ? 'bg-muted' : ''}
            >
              전체 코인
            </CommandItem>
            {filtered.map((o) => (
              <CommandItem
                key={o.market}
                value={o.market}
                onSelect={() => {
                  onChange(o.market);
                  setOpen(false);
                }}
                className={o.market === value ? 'bg-muted' : ''}
              >
                <span className="font-medium">{o.market}</span>
                {o.koreanName && <span className="ml-2 text-xs text-muted-foreground">{o.koreanName}</span>}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
