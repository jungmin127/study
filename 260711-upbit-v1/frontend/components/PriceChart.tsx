'use client';

import { useEffect, useRef } from 'react';
import {
  createChart,
  CandlestickSeries,
  CrosshairMode,
  createSeriesMarkers,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { OhlcvPoint, Trade } from '@/lib/types/eda';

interface PriceChartProps {
  ohlcv: OhlcvPoint[];
  trades: Trade[];
  timeframe: string;
}

function isIntraday(timeframe: string): boolean {
  return timeframe.startsWith('minutes');
}

function toUnix(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

type DayString = `${number}-${number}-${number}`;

export default function PriceChart({ ohlcv, trades, timeframe }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const intradayMode = isIntraday(timeframe);

  useEffect(() => {
    if (!containerRef.current || ohlcv.length === 0) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#d1d5db' },
      rightPriceScale: { borderColor: '#d1d5db' },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#dc2626',
      downColor: '#2563eb',
      borderVisible: false,
      wickUpColor: '#dc2626',
      wickDownColor: '#2563eb',
    });

    if (intradayMode) {
      const candleData = ohlcv
        .map((bar) => ({
          time: toUnix(bar.time),
          open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        }))
        .sort((a, b) => a.time - b.time);
      candleSeries.setData(candleData);

      const markers = [
        ...trades.map((t) => ({
          time: toUnix(t.entryTime), position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: 'B',
        })),
        ...trades.map((t) => ({
          time: toUnix(t.exitTime), position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: 'S',
        })),
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

      const markers = [
        ...Array.from(buysByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'belowBar' as const,
          color: '#2563eb', shape: 'arrowUp' as const, text: count > 1 ? `B×${count}` : 'B',
        })),
        ...Array.from(sellsByDay.entries()).map(([day, count]) => ({
          time: day as DayString, position: 'aboveBar' as const,
          color: '#d97706', shape: 'arrowDown' as const, text: count > 1 ? `S×${count}` : 'S',
        })),
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
  }, [ohlcv, trades, intradayMode]);

  return (
    <div className="w-full">
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-blue-500" />
          매수 (B)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
          매도 (S)
        </span>
      </div>
      <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />
    </div>
  );
}
