'use client';

import { useEffect, useRef } from 'react';
import {
  createChart, CandlestickSeries, ColorType, CrosshairMode, TickMarkType,
  type Time, type UTCTimestamp,
} from 'lightweight-charts';
import type { RegimeAdxBar } from '@/lib/types/eda';

// lightweight-charts는 UTCTimestamp를 기본적으로 UTC로 표시한다(브라우저 로컬
// 타임존이 아님) — 이 앱의 다른 화면(표/뱃지 등)은 전부 KST로 보여주므로,
// 축/십자선 라벨도 KST로 맞춰 혼동을 없앤다.
const KST_PARTS_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

function toKstParts(unixSeconds: number) {
  const parts = KST_PARTS_FORMATTER.formatToParts(new Date(unixSeconds * 1000));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return { year: get('year'), month: get('month'), day: get('day'), hour: get('hour'), minute: get('minute') };
}

export default function RegimeAdxChart({ bars }: { bars: RegimeAdxBar[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || bars.length === 0) return;

    // getComputedStyle의 oklch() 반환값을 lightweight-charts가 파싱하지 못해,
    // canvas 2D로 한 번 그려 rgba()로 변환한다.
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

    const upColor = resolveColor('--regime-surge-up');
    const downColor = resolveColor('--regime-surge-down');
    const sidewaysColor = resolveColor('--marker-boundary');
    const unclassifiedColor = resolveColor('--trend-unclassified');
    const background = resolveColor('--background');
    const foreground = resolveColor('--foreground');
    const border = resolveColor('--border');

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      localization: {
        timeFormatter: (time: Time) => {
          const { year, month, day, hour, minute } = toKstParts(time as number);
          return `${year}-${month}-${day} ${hour}:${minute} KST`;
        },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: border,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) => {
          const { year, month, day, hour, minute } = toKstParts(time as number);
          switch (tickMarkType) {
            case TickMarkType.Year:
              return year;
            case TickMarkType.Month:
              return `${month}월`;
            case TickMarkType.DayOfMonth:
              return `${day}일`;
            default:
              return `${hour}:${minute}`;
          }
        },
      },
      rightPriceScale: { borderColor: border },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor, downColor, borderVisible: false,
      wickUpColor: upColor, wickDownColor: downColor,
    });

    const toUnix = (iso: string): UTCTimestamp => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

    const colorFor = (label: RegimeAdxBar['label']): string => {
      if (label === '상승') return upColor;
      if (label === '하락') return downColor;
      if (label === '횡보') return sidewaysColor;
      return unclassifiedColor;
    };

    const candleData = bars.map((bar) => {
      const color = colorFor(bar.label);
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
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--regime-surge-up)' }} />
          상승
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--regime-surge-down)' }} />
          하락
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          횡보
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--trend-unclassified)' }} />
          미분류(워밍업)
        </span>
        <span className="ml-auto">시간축 기준: KST</span>
      </div>
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
    </div>
  );
}
