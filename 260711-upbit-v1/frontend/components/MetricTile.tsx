import InfoTooltip from '@/components/InfoTooltip';

interface MetricTileProps {
  label: string;
  value: string;
  colorClass?: string;
  tooltip?: string;
}

export default function MetricTile({ label, value, colorClass, tooltip }: MetricTileProps) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </div>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}
