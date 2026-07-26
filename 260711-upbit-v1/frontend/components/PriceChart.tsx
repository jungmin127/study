'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createSeriesMarkers,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { OhlcvPoint, Trade } from '@/lib/types/eda';

interface PriceChartProps {
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  timeframe: string;
  backtestEnd: string;
}

function isIntraday(timeframe: string): boolean {
  return timeframe.startsWith('minutes');
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

type DayString = `${number}-${number}-${number}`;

export default function PriceChart({ ohlcv, trades, timeframe, backtestEnd }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const intradayMode = isIntraday(timeframe);

  useEffect(() => {
    if (!containerRef.current || ohlcv.length === 0) return;

    // getComputedStyle(...).getPropertyValue('--x') on a *custom* property returns the
    // literal authored token stream (e.g. "oklch(0.577 0.245 27.325)") verbatim — custom
    // properties are untyped, so the browser never resolves their color space. Even
    // resolving through a real color-typed property (e.g. an element's `color`) doesn't
    // help in modern Chromium: per the updated CSS Color 4 serialization rules,
    // getComputedStyle now preserves the oklch() notation instead of converting to rgb().
    // lightweight-charts' internal ColorParser only understands hex/rgb(a)/hsl(a)/named
    // colors, so passing oklch() straight through throws "Failed to parse color" at
    // runtime. Canvas 2D's fillStyle, however, *does* natively parse oklch() (it uses the
    // browser's CSS color parser) — so painting a 1x1 rect and reading the pixel back via
    // getImageData gives a guaranteed rgba() string that lightweight-charts can parse.
    const resolveColor = (varName: string): string => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return raw;
      ctx.fillStyle = raw;
      ctx.fillRect(0, 0, 1, 1);
      const pixel = ctx.getImageData(0, 0, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];
      return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
    };

    const priceUp = resolveColor('--price-up');
    const priceDown = resolveColor('--price-down');
    const markerEntry = resolveColor('--marker-entry');
    const markerExit = resolveColor('--marker-exit');
    const markerBoundary = resolveColor('--marker-boundary');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: priceUp,
      downColor: priceDown,
      borderVisible: false,
      wickUpColor: priceUp,
      wickDownColor: priceDown,
    });

    if (intradayMode) {
      const candleData = ohlcv
        .map((bar) => ({
          time: toUnix(bar.time),
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        }))
        .sort((a, b) => a.time - b.time);
      candleSeries.setData(candleData);

      const boundaryUnix = toUnix(backtestEnd);
      const boundaryBar = candleData.find((bar) => bar.time > boundaryUnix);

      const markers = [
        ...trades.map((t) => ({
          time: toUnix(t.entryTime), position: 'belowBar' as const,
          color: markerEntry, shape: 'arrowUp' as const, text: 'B',
        })),
        ...trades.map((t) => ({
          time: toUnix(t.exitTime), position: 'aboveBar' as const,
          color: markerExit, shape: 'arrowDown' as const, text: 'S',
        })),
        ...(boundaryBar ? [{
          time: boundaryBar.time, position: 'inBar' as const,
          color: markerBoundary, shape: 'circle' as const, text: '종료',
        }] : []),
      ].sort((a, b) => a.time - b.time);
      createSeriesMarkers(candleSeries, markers);
    } else {
      const candleData = ohlcv
        .map((bar) => ({
          time: bar.time.split('T')[0] as DayString,
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        }))
        .sort((a, b) => String(a.time).localeCompare(String(b.time)))
        .filter((bar, i, arr) => i === 0 || bar.time !== arr[i - 1].time);
      candleSeries.setData(candleData);

      const buysByDay = new Map<string, number>();
      const sellsByDay = new Map<string, number>();
      trades.forEach((t) => {
        const day = t.entryTime.split('T')[0];
        buysByDay.set(day, (buysByDay.get(day) ?? 0) + 1);
      });
      trades.forEach((t) => {
        const day = t.exitTime.split('T')[0];
        sellsByDay.set(day, (sellsByDay.get(day) ?? 0) + 1);
      });

      const boundaryDay = backtestEnd.split('T')[0];
      const boundaryBar = candleData.find((bar) => String(bar.time) > boundaryDay);

      const markers = [
        ...Array.from(buysByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'belowBar' as const,
          color: markerEntry, shape: 'arrowUp' as const, text: count > 1 ? `B×${count}` : 'B',
        })),
        ...Array.from(sellsByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'aboveBar' as const,
          color: markerExit, shape: 'arrowDown' as const, text: count > 1 ? `S×${count}` : 'S',
        })),
        ...(boundaryBar ? [{
          time: boundaryBar.time, position: 'inBar' as const,
          color: markerBoundary, shape: 'circle' as const, text: '종료',
        }] : []),
      ].sort((a, b) => String(a.time).localeCompare(String(b.time)));
      createSeriesMarkers(candleSeries, markers);
    }

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [ohlcv, trades, intradayMode, backtestEnd]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-entry)' }} />
          매수 (B)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-exit)' }} />
          매도 (S)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          백테스트 종료
        </span>
      </div>
      <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />
    </div>
  );
}
