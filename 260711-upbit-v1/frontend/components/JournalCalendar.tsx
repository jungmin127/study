'use client';

import { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { JournalDailyCell } from '@/lib/types/journal';
import { returnRateColor } from '@/lib/return-rate-color';

const WEEKDAY_LABELS = ['일', '월', '화', '수', '목', '금', '토'];

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function fmtKrwCompact(value: number): string {
  const sign = value > 0 ? '+' : value < 0 ? '-' : '';
  return `${sign}${Math.round(Math.abs(value)).toLocaleString()}원`;
}

export default function JournalCalendar({ daily }: { daily: JournalDailyCell[] }) {
  const byDate = useMemo(() => {
    const map = new Map<string, JournalDailyCell>();
    for (const cell of daily) map.set(cell.trading_date, cell);
    return map;
  }, [daily]);

  const latest = daily.length > 0 ? daily[daily.length - 1].trading_date : null;
  const now = new Date();
  const [year, setYear] = useState(latest ? Number(latest.slice(0, 4)) : now.getFullYear());
  const [month, setMonth] = useState(latest ? Number(latest.slice(5, 7)) - 1 : now.getMonth());

  function goPrevMonth() {
    if (month === 0) {
      setYear((y) => y - 1);
      setMonth(11);
    } else {
      setMonth((m) => m - 1);
    }
  }

  function goNextMonth() {
    if (month === 11) {
      setYear((y) => y + 1);
      setMonth(0);
    } else {
      setMonth((m) => m + 1);
    }
  }

  const firstWeekday = new Date(Date.UTC(year, month, 1)).getUTCDay();
  const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const cells: (number | null)[] = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];

  return (
    <div className="rounded-xl border p-3 md:p-4">
      <div className="mb-3 flex items-center justify-between">
        <Button size="icon-lg" variant="ghost" aria-label="이전 달" onClick={goPrevMonth}>
          <ChevronLeft />
        </Button>
        <span className="text-sm font-semibold tabular-nums">
          {year}년 {month + 1}월
        </span>
        <Button size="icon-lg" variant="ghost" aria-label="다음 달" onClick={goNextMonth}>
          <ChevronRight />
        </Button>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-[0.65rem] text-muted-foreground">
        {WEEKDAY_LABELS.map((w) => (
          <div key={w}>{w}</div>
        ))}
      </div>
      <div className="mt-1 grid grid-cols-7 gap-1">
        {cells.map((day, idx) => {
          if (day === null) return <div key={`empty-${idx}`} />;
          const dateKey = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
          const cell = byDate.get(dateKey);
          return (
            <div
              key={dateKey}
              className={`rounded-md p-1 text-center ${
                cell ? (cell.pnl >= 0 ? 'bg-red-500/10' : 'bg-blue-500/10') : ''
              }`}
            >
              <p className="text-[0.62rem] text-muted-foreground">{day}</p>
              {cell && (
                <>
                  <p className={`text-[0.6rem] font-semibold tabular-nums ${returnRateColor(cell.pnl_pct)}`}>
                    {fmtPct(cell.pnl_pct)}
                  </p>
                  <p className="text-[0.55rem] tabular-nums text-muted-foreground">
                    {fmtKrwCompact(cell.pnl)}
                  </p>
                </>
              )}
            </div>
          );
        })}
      </div>
      {daily.length === 0 && (
        <p className="mt-2 text-center text-xs text-muted-foreground">아직 청산된 거래가 없습니다.</p>
      )}
    </div>
  );
}
