import Link from 'next/link';
import { getBacktestRuns } from '@/lib/api/eda';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import DeleteRunButton from '@/components/DeleteRunButton';

function returnRateColor(rate: number | null): string {
  if (rate === null) return '';
  if (rate > 0) return 'text-green-600 dark:text-green-400';
  if (rate < 0) return 'text-red-600 dark:text-red-400';
  return '';
}

export default async function BacktestResultsPage() {
  const runs = await getBacktestRuns();

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">백테스트 결과</h1>
      {runs.length === 0 ? (
        <p className="text-muted-foreground">
          아직 실행한 백테스트가 없습니다. &quot;백테스트 설정&quot; 탭에서 먼저 실행해 보세요.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>제목</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>기간</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>실행 시각</TableHead>
              <TableHead>상세</TableHead>
              <TableHead>삭제</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.run_id}>
                <TableCell>
                  {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
                  {run.description && (
                    <p className="text-xs text-muted-foreground">{run.description}</p>
                  )}
                </TableCell>
                <TableCell>{run.market}</TableCell>
                <TableCell>{run.timeframe}</TableCell>
                <TableCell>
                  {run.start} ~ {run.end}
                </TableCell>
                <TableCell className={returnRateColor(run.return_rate)}>
                  {run.return_rate?.toFixed(2) ?? '-'}
                </TableCell>
                <TableCell>{run.created_at}</TableCell>
                <TableCell>
                  <Link
                    href={`/backtests/${run.run_id}`}
                    className="text-blue-600 hover:underline dark:text-blue-400"
                  >
                    보기
                  </Link>
                </TableCell>
                <TableCell>
                  <DeleteRunButton runId={run.run_id} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
