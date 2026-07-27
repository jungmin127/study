import type { ChartConfig } from '@/lib/indicator-example-builder';

function ZoneGauge({ chart }: { chart: Extract<ChartConfig, { type: 'gauge' }> }) {
  const { min, max, zones, value, valueLabel } = chart;
  const pct = (v: number) => ((v - min) / (max - min)) * 100;
  const clampedPct = Math.min(100, Math.max(0, pct(value)));
  const activeZone = zones.find((z) => value >= z.from && value <= z.to) ?? zones[zones.length - 1];

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-sm font-medium">
          {valueLabel} = <span className="tabular-nums">{value}</span>
        </span>
        <span className="text-xs font-medium" style={{ color: activeZone.color }}>
          {activeZone.label}
        </span>
      </div>
      <div className="relative h-3 w-full overflow-hidden rounded-full border">
        <div className="flex h-full w-full">
          {zones.map((z) => (
            <div
              key={`${z.from}-${z.to}`}
              style={{ width: `${pct(z.to) - pct(z.from)}%`, backgroundColor: z.color, opacity: 0.35 }}
            />
          ))}
        </div>
        <div
          className="absolute top-0 h-full w-0.5 bg-foreground"
          style={{ left: `${clampedPct}%` }}
          aria-label={`현재 값 ${value}`}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}

export default ZoneGauge;
