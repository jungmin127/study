'use client';

import { useEffect, useRef } from 'react';
import { createChart, LineSeries } from 'lightweight-charts';
import type { EquityPoint } from '@/lib/types/eda';

export default function EquityCurveChart({ equityCurve }: { equityCurve: EquityPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || equityCurve.length === 0) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 260,
      timeScale: { timeVisible: true, borderColor: '#d1d5db' },
      rightPriceScale: { borderColor: '#d1d5db' },
    });

    const series = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 2 });
    const data = equityCurve
      .map((p) => ({ time: p.timestamp.split('T')[0] as `${number}-${number}-${number}`, value: p.value }))
      .sort((a, b) => String(a.time).localeCompare(String(b.time)))
      .filter((p, i, arr) => i === 0 || p.time !== arr[i - 1].time);
    series.setData(data);
    chart.timeScale().fitContent();

    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
    };
  }, [equityCurve]);

  return <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />;
}
