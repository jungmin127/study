'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode, type UTCTimestamp } from 'lightweight-charts';
import type { RegimeFactBar } from '@/lib/types/eda';
import { categoryVarName } from '@/components/RegimeMlCurrentPrediction';

export default function RegimeFactChart({ bars }: { bars: RegimeFactBar[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;

    // TrendSegmentChart.tsx와 동일한 이유: getComputedStyle의 oklch() 반환값을
    // lightweight-charts가 파싱하지 못해, canvas 2D로 한 번 그려 rgba()로 변환한다.
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

    const downColor = resolveColor(categoryVarName('하락'));
    const notDownColor = resolveColor(categoryVarName('하락아님'));
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: notDownColor, downColor, borderVisible: false,
      wickUpColor: notDownColor, wickDownColor: downColor,
    });

    // PriceChart.tsx:toUnix()와 동일한 변환(초 단위 유닉스 타임으로 내림).
    const toUnix = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

    const candleData = bars.map((bar) => {
      const color = bar.label === '하락' ? downColor : bar.label === '하락아님' ? notDownColor : unclassifiedColor;
      return {
        time: toUnix(bar.time),
        open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        color, borderColor: color, wickColor: color,
      };
    });
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
  }, [bars]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--regime-surge-down)' }} />
          하락
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          하락아님
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
