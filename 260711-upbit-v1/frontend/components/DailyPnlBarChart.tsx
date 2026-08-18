'use client';

import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { JournalDailyPnlPoint } from '@/lib/types/journal';

// 이 앱의 손익 색상 관례(frontend/lib/return-rate-color.ts)와 동일: 양수=빨강, 음수=파랑
// (한국 증시 관례, 서구식 초록/빨강 아님).
function barColorClass(pnl: number): string {
  if (pnl > 0) return 'fill-red-600 dark:fill-red-400';
  if (pnl < 0) return 'fill-blue-600 dark:fill-blue-400';
  return 'fill-muted-foreground/20';
}

function fmtTick(date: string): string {
  return date.slice(5).replace('-', '/');
}

function fmtLabel(value: number | string | null | undefined): string {
  if (value === undefined || value === null) return '';
  const num = typeof value === 'string' ? parseFloat(value) : value;
  if (isNaN(num) || num === 0) return '';
  return Math.round(num).toLocaleString();
}

export default function DailyPnlBarChart({
  data, heightPx,
}: {
  data: JournalDailyPnlPoint[];
  heightPx: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={heightPx}>
      <BarChart data={data} margin={{ top: 16 }}>
        <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={fmtTick} interval={4} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => v.toLocaleString()} />
        <Bar dataKey="pnl">
          {data.map((entry) => (
            <Cell key={entry.date} className={barColorClass(entry.pnl)} />
          ))}
          <LabelList
            dataKey="pnl"
            position="top"
            className="fill-foreground"
            fontSize={10}
            formatter={fmtLabel as any}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
