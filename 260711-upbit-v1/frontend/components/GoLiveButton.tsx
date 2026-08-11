import Link from 'next/link';
import { Rocket } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function GoLiveButton({ runId }: { runId: string }) {
  return (
    // nativeButton={false} + role="link": base-ui's Button `render` prop assumes a real
    // <button> by default and otherwise logs a console error since Link renders an <a>;
    // role="link" overrides base-ui's default role="button" on the rendered <a>, which
    // would otherwise be an accessibility defect. Same pattern as BacktestRunsTable.tsx
    // and app/heatmap/page.tsx.
    <Button
      variant="outline"
      size="sm"
      nativeButton={false}
      role="link"
      render={<Link href={`/live-strategies/new?source_run_id=${runId}`} />}
    >
      <Rocket className="size-3.5" />
      이 전략으로 실매매 시작
    </Button>
  );
}
