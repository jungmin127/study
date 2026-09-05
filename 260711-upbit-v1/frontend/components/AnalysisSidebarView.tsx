'use client';

import { useState } from 'react';
import { BarChart3, PieChart } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import SegmentSizeTable, { type SegmentRow } from '@/components/SegmentSizeTable';

type Section = 'size' | 'sector';

const SECTIONS: { key: Section; label: string; icon: typeof BarChart3 }[] = [
  { key: 'size', label: '세그먼트(규모)', icon: BarChart3 },
  { key: 'sector', label: '세그먼트(섹터)', icon: PieChart },
];

export default function AnalysisSidebarView({
  segmentSizeRows,
}: {
  segmentSizeRows: SegmentRow[];
}) {
  const [section, setSection] = useState<Section>('size');

  return (
    <div className="flex gap-6">
      <nav className="flex w-44 shrink-0 flex-col gap-1">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setSection(key)}
            className={
              section === key
                ? 'flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm font-medium text-foreground'
                : 'flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground'
            }
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1">
        {section === 'size' ? (
          <SegmentSizeTable rows={segmentSizeRows} />
        ) : (
          <Card>
            <CardContent className="pt-4">
              <p className="text-muted-foreground">준비 중입니다.</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
