'use client';

import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode, type UTCTimestamp } from 'lightweight-charts';
import type { RegimeCandle, RegimeCategory } from '@/lib/types/eda';

interface RegimeChartProps {
  candles: RegimeCandle[];
  timeframe: string;
}

type DayString = `${number}-${number}-${number}`;

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '급상승':
      return '--regime-surge-up';
    case '완만상승':
      return '--regime-mild-up';
    case '횡보':
      return '--marker-boundary';
    case '완만하락':
      return '--regime-mild-down';
    case '급하락':
      return '--regime-surge-down';
  }
}

function isIntraday(timeframe: string): boolean {
  return timeframe.startsWith('minutes');
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

export default function RegimeChart({ candles, timeframe }: RegimeChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const intradayMode = isIntraday(timeframe);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return;

    // PriceChart.tsx/TrendSegmentChart.tsx와 동일한 이유: getComputedStyle의 oklch()
    // 반환값을 lightweight-charts가 파싱하지 못해, canvas 2D로 한 번 그려 rgba()로 변환한다.
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

    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const categoryColor: Record<RegimeCategory, string> = {
      급상승: resolveColor('--regime-surge-up'),
      완만상승: resolveColor('--regime-mild-up'),
      횡보: resolveColor('--marker-boundary'),
      완만하락: resolveColor('--regime-mild-down'),
      급하락: resolveColor('--regime-surge-down'),
    };

    function colorFor(candle: RegimeCandle): string {
      return candle.predicted_category ? categoryColor[candle.predicted_category] : unclassifiedColor;
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: intradayMode, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: unclassifiedColor, downColor: unclassifiedColor, borderVisible: false,
      wickUpColor: unclassifiedColor, wickDownColor: unclassifiedColor,
    });

    if (intradayMode) {
      const candleData = candles
        .map((c) => {
          const color = colorFor(c);
          return {
            time: toUnix(c.time), open: c.open, high: c.high, low: c.low, close: c.close,
            color, borderColor: color, wickColor: color,
          };
        })
        .sort((a, b) => a.time - b.time);
      candleSeries.setData(candleData);
    } else {
      const candleData = candles
        .map((c) => {
          const color = colorFor(c);
          return {
            time: c.time.split('T')[0] as DayString,
            open: c.open, high: c.high, low: c.low, close: c.close,
            color, borderColor: color, wickColor: color,
          };
        })
        .sort((a, b) => String(a.time).localeCompare(String(b.time)))
        .filter((bar, i, arr) => i === 0 || bar.time !== arr[i - 1].time);
      candleSeries.setData(candleData);
    }

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
  }, [candles, timeframe, intradayMode]);

  return (
    <div className="w-full">
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        {CATEGORY_ORDER.map((label) => (
          <span key={label} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: `var(${categoryVarName(label)})` }}
            />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--trend-unclassified)' }} />
          미분류(워밍업)
        </span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
