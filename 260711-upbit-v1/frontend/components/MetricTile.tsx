'use client';

import { useState } from 'react';

function InfoTooltip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);

  return (
    <span className="relative shrink-0">
      <button
        type="button"
        className="flex h-4 w-4 items-center justify-center rounded-full border border-muted-foreground text-[10px] leading-none text-muted-foreground hover:border-foreground hover:text-foreground"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label="지표 설명"
      >
        ?
      </button>
      {open && (
        <div className="absolute left-1/2 top-full z-50 mt-1 w-64 -translate-x-1/2 whitespace-pre-line rounded-md border bg-background p-2 text-left text-xs font-normal text-foreground shadow-lg">
          {text}
        </div>
      )}
    </span>
  );
}

interface MetricTileProps {
  label: string;
  value: string;
  colorClass?: string;
  tooltip?: string;
}

export default function MetricTile({ label, value, colorClass, tooltip }: MetricTileProps) {
  return (
    <div className="rounded-md border p-3">
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </p>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}
