import type { TrendDirection } from '@/lib/types/eda';

const TREND_LABELS: Record<TrendDirection, string> = {
  up: '상승',
  down: '하락',
  sideways: '횡보',
};

const PATTERN_GRID: Record<TrendDirection, Record<TrendDirection, string>> = {
  up: { up: '지속형 상승', down: '상승 후 반전', sideways: '상승 후 둔화' },
  down: { up: '하락 후 반등', down: '지속형 하락', sideways: '하락 후 멈춤' },
  sideways: { up: '횡보 이탈(상승)', down: '횡보 이탈(하락)', sideways: '지속형 횡보' },
};

const ROWS: TrendDirection[] = ['up', 'down', 'sideways'];

interface Cell {
  key: string;
  content: string;
  className: string;
}

const CELLS: Cell[] = [
  { key: 'corner', content: '', className: '' },
  ...ROWS.map((col) => ({
    key: `head-${col}`,
    content: `후반 ${TREND_LABELS[col]}`,
    className: 'font-medium text-muted-foreground',
  })),
  ...ROWS.flatMap((row) => [
    {
      key: `row-${row}`,
      content: `전반 ${TREND_LABELS[row]}`,
      className: 'flex items-center font-medium text-muted-foreground',
    },
    ...ROWS.map((col) => ({
      key: `${row}-${col}`,
      content: PATTERN_GRID[row][col],
      className: 'rounded bg-background px-2 py-1.5',
    })),
  ]),
];

export default function TrendPatternLegend() {
  return (
    <div className="rounded-md border bg-muted/30 p-3 text-xs">
      <p className="mb-2 text-muted-foreground">
        각 구간을 기간의 중간 지점으로 전반부/후반부로 나눠, 절반 구간의 등락률이 임계값의
        절반(threshold_pct / 2) 이상이면 &apos;상승&apos;, -임계값/2 이하면 &apos;하락&apos;,
        그 사이면 &apos;횡보&apos;로 판정합니다. 전반부·후반부 조합으로 아래 9가지 패턴 중
        하나로 라벨링됩니다.
      </p>
      <div className="grid grid-cols-[auto_repeat(3,1fr)] gap-1 text-center">
        {CELLS.map((cell) => (
          <div key={cell.key} className={cell.className}>
            {cell.content}
          </div>
        ))}
      </div>
    </div>
  );
}
