'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode } from 'lightweight-charts';
import type { OhlcvPoint, TrendDirection, TrendSegment } from '@/lib/types/eda';

interface TrendSegmentChartProps {
  ohlcv: OhlcvPoint[];
  segments: TrendSegment[];
}

type DayString = `${number}-${number}-${number}`;

function trendForDay(day: string, segments: TrendSegment[]): TrendDirection | null {
  for (const seg of segments) {
    if (day >= seg.start_date && day <= seg.end_date) return seg.trend;
  }
  return null;
}

export default function TrendSegmentChart({ ohlcv, segments }: TrendSegmentChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || ohlcv.length === 0) return;

    // PriceChart.tsx와 동일한 이유: getComputedStyle의 oklch() 반환값을 lightweight-charts가
    // 파싱하지 못해, canvas 2D로 한 번 그려 rgba()로 변환한다.
    const resolveColor = (varName: string): string => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return raw;
      ctx.fillStyle = raw;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      return `rgba(${pixel[0]}, ${pixel[1]}, ${pixel[2]}, ${(pixel[3] / 255).toFixed(3)})`;
    };

    const upColor = resolveColor('--price-up');
    const downColor = resolveColor('--price-down');
    const sidewaysColor = resolveColor('--marker-boundary');
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const trendColor: Record<TrendDirection, string> = {
      up: upColor,
      down: downColor,
      sideways: sidewaysColor,
    };

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: false, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor, downColor, borderVisible: false, wickUpColor: upColor, wickDownColor: downColor,
    });

    const candleData = ohlcv
      .map((bar) => {
        const day = bar.time.split('T')[0];
        const trend = trendForDay(day, segments);
        const color = trend ? trendColor[trend] : unclassifiedColor;
        return {
          time: day as DayString,
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
          color, borderColor: color, wickColor: color,
        };
      })
      .sort((a, b) => String(a.time).localeCompare(String(b.time)))
      .filter((bar, i, arr) => i === 0 || bar.time !== arr[i - 1].time);
    candleSeries.setData(candleData);

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [ohlcv, segments]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--price-up)' }} />
          상승 구간
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--price-down)' }} />
          하락 구간
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          횡보 구간
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--trend-unclassified)' }} />
          미분류(최신)
        </span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
