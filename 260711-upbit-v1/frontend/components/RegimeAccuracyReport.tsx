import type { RegimeBacktestResult, RegimeCategory } from '@/lib/types/eda';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InfoPopover } from '@/components/ui/info-popover';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

function formatCount(n: number): string {
  return n.toLocaleString('ko-KR');
}

interface RegimeAccuracyReportProps {
  report: RegimeBacktestResult;
}

export default function RegimeAccuracyReport({ report }: RegimeAccuracyReportProps) {
  const { confusion, actual_totals, correlation } = report;
  const totalSamples = CATEGORY_ORDER.reduce((sum, label) => sum + actual_totals[label], 0);

  return (
    <div className="space-y-6 rounded-xl border p-6 shadow-sm">
      <div>
        <h2 className="mb-1.5 flex items-center gap-1 text-sm font-semibold">
          확률벡터-실현수익률 상관계수
          <InfoPopover>
            피어슨 상관계수(-1 ~ +1). 시점마다 예측 확률벡터를 카테고리별 기준점수(급상승 +0.35
            ~ 급하락 -0.35)로 가중평균한 “기댓값”과, 이후 n봉 동안의 실현수익률을 변동성으로
            정규화한 값 사이의 선형 상관관계입니다. +1에 가까울수록 예측 방향과 실제 방향이
            강하게 같이 움직이고, -1에 가까울수록 반대로 움직이며, 0에 가까우면 확률벡터에
            예측력이 거의 없다는 뜻입니다. 위의 확률→단일 카테고리 적중률과 달리, 확률분포
            전체(강도 포함)를 반영하는 지표입니다.
          </InfoPopover>
        </h2>
        <p className="text-sm tabular-nums">
          {correlation === null ? '계산 불가(샘플 부족)' : correlation.toFixed(3)}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div>
          <h2 className="mb-1.5 text-sm font-semibold">카테고리별 적중률</h2>
          <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>예측 카테고리</TableHead>
                  <TableHead className="text-right">총건수</TableHead>
                  <TableHead className="text-right">적중건수</TableHead>
                  <TableHead className="text-right">적중률</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {CATEGORY_ORDER.map((label) => {
                  const row = confusion[label];
                  const total = CATEGORY_ORDER.reduce((sum, a) => sum + row[a], 0);
                  const hit = row[label];
                  return (
                    <TableRow key={label}>
                      <TableCell>{label}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatCount(total)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatCount(hit)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {total === 0 ? '샘플 없음' : `${((hit / total) * 100).toFixed(1)}%`}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>

        <div>
          <h2 className="mb-1.5 text-sm font-semibold">Confusion Matrix (행=실제, 열=예측)</h2>
          <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>실제\예측</TableHead>
                  {CATEGORY_ORDER.map((label) => (
                    <TableHead key={label} className="text-right">
                      {label}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {CATEGORY_ORDER.map((actual) => {
                  const rowTotal = actual_totals[actual];
                  return (
                    <TableRow key={actual}>
                      <TableCell className="font-medium">{actual}</TableCell>
                      {CATEGORY_ORDER.map((predicted) => {
                        const value = confusion[predicted][actual];
                        const ratio = rowTotal ? value / rowTotal : 0;
                        return (
                          <TableCell
                            key={predicted}
                            className="text-right tabular-nums"
                            style={{ backgroundColor: `oklch(0.55 0.2 255 / ${(ratio * 0.5).toFixed(2)})` }}
                          >
                            {formatCount(value)}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>

        <div>
          <h2 className="mb-1.5 text-sm font-semibold">실제 카테고리 분포 (전체 {formatCount(totalSamples)}건)</h2>
          <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>카테고리</TableHead>
                  <TableHead className="text-right">건수</TableHead>
                  <TableHead className="text-right">비중</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {CATEGORY_ORDER.map((label) => {
                  const n = actual_totals[label];
                  const pct = totalSamples ? (n / totalSamples) * 100 : 0;
                  return (
                    <TableRow key={label}>
                      <TableCell>{label}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatCount(n)}</TableCell>
                      <TableCell className="text-right tabular-nums">{pct.toFixed(1)}%</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      </div>
    </div>
  );
}
