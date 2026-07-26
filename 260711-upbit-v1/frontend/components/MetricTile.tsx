import type { LucideIcon } from 'lucide-react';
import InfoTooltip from '@/components/InfoTooltip';

interface MetricTileProps {
  label: string;
  value: string;
  colorClass?: string;
  tooltip?: string;
  icon?: LucideIcon;
}

export default function MetricTile({ label, value, colorClass, tooltip, icon: Icon }: MetricTileProps) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {Icon && <Icon className="size-3.5 shrink-0" />}
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </div>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}
