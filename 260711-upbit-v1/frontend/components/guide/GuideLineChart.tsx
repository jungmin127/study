'use client';

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { ChartConfig, LineSpec } from '@/lib/indicator-example-builder';

function GuideLineChart({ chart }: { chart: Extract<ChartConfig, { type: 'line' }> }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <ComposedChart data={chart.data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
        <XAxis dataKey="bar" tick={{ fontSize: 11 }} label={{ value: '봉 번호', position: 'insideBottom', offset: -2, fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} width={48} />
        <Tooltip contentStyle={{ fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {chart.refLines?.map((ref) => (
          <ReferenceLine key={ref.y} y={ref.y} stroke="#64748b" strokeDasharray="4 4" label={{ value: ref.label, fontSize: 10, position: 'insideTopRight' }} />
        ))}
        {chart.bars?.map((b: LineSpec) => (
          <Bar key={b.key} dataKey={b.key} name={b.name} fill={b.color} opacity={0.6} />
        ))}
        {chart.lines?.map((l: LineSpec) => (
          <Line
            key={l.key}
            type="monotone"
            dataKey={l.key}
            name={l.name}
            stroke={l.color}
            strokeWidth={2}
            strokeDasharray={l.dash ? '5 3' : undefined}
            dot={false}
            connectNulls={false}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export default GuideLineChart;
