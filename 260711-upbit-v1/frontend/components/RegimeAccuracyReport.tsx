import type { RegimeBacktestResult, RegimeCategory } from '@/lib/types/eda';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

interface RegimeAccuracyReportProps {
  report: RegimeBacktestResult;
}

export default function RegimeAccuracyReport({ report }: RegimeAccuracyReportProps) {
  const { confusion, actual_totals, correlation } = report;
  const totalSamples = CATEGORY_ORDER.reduce((sum, label) => sum + actual_totals[label], 0);

  return (
    <div className="space-y-6 rounded-xl border p-6 shadow-sm">
      <div>
        <h2 className="mb-2 text-sm font-semibold">카테고리별 적중률</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="py-1.5">예측 카테고리</th>
              <th className="py-1.5 text-right">총건수</th>
              <th className="py-1.5 text-right">적중건수</th>
              <th className="py-1.5 text-right">적중률</th>
            </tr>
          </thead>
          <tbody>
            {CATEGORY_ORDER.map((label) => {
              const row = confusion[label];
              const total = CATEGORY_ORDER.reduce((sum, a) => sum + row[a], 0);
              const hit = row[label];
              return (
                <tr key={label} className="border-b last:border-0">
                  <td className="py-1.5">{label}</td>
                  <td className="py-1.5 text-right tabular-nums">{total}</td>
                  <td className="py-1.5 text-right tabular-nums">{hit}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    {total === 0 ? '샘플 없음' : `${((hit / total) * 100).toFixed(1)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">확률벡터-실현수익률 상관계수</h2>
        <p className="text-sm tabular-nums">
          {correlation === null ? '계산 불가(샘플 부족)' : correlation.toFixed(3)}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-sm font-semibold">Confusion Matrix (행=실제, 열=예측)</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1.5">실제\예측</th>
                  {CATEGORY_ORDER.map((label) => (
                    <th key={label} className="py-1.5 text-right">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CATEGORY_ORDER.map((actual) => {
                  const rowTotal = actual_totals[actual];
                  return (
                    <tr key={actual} className="border-b last:border-0">
                      <td className="py-1.5 font-medium">{actual}</td>
                      {CATEGORY_ORDER.map((predicted) => {
                        const value = confusion[predicted][actual];
                        const ratio = rowTotal ? value / rowTotal : 0;
                        return (
                          <td
                            key={predicted}
                            className="py-1.5 text-right tabular-nums"
                            style={{ backgroundColor: `oklch(0.55 0.2 255 / ${(ratio * 0.5).toFixed(2)})` }}
                          >
                            {value}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <h2 className="mb-2 text-sm font-semibold">실제 카테고리 분포 (전체 {totalSamples}건)</h2>
          <table className="w-full text-sm">
            <tbody>
              {CATEGORY_ORDER.map((label) => {
                const n = actual_totals[label];
                const pct = totalSamples ? (n / totalSamples) * 100 : 0;
                return (
                  <tr key={label} className="border-b last:border-0">
                    <td className="py-1.5">{label}</td>
                    <td className="py-1.5 text-right tabular-nums">{n}</td>
                    <td className="py-1.5 text-right tabular-nums">{pct.toFixed(1)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
