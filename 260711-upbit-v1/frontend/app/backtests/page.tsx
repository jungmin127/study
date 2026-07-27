import { getBacktestRuns, getMarkets } from '@/lib/api/eda';
import BacktestRunsTable from '@/components/BacktestRunsTable';

export default async function BacktestResultsPage() {
  const [runs, markets] = await Promise.all([getBacktestRuns(), getMarkets()]);
  const marketNames = Object.fromEntries(markets.map((m) => [m.market, m.korean_name]));

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">백테스트 결과</h1>
      {runs.length === 0 ? (
        <p className="text-muted-foreground">
          아직 실행한 백테스트가 없습니다. &quot;백테스트 설정&quot; 탭에서 먼저 실행해 보세요.
        </p>
      ) : (
        <BacktestRunsTable runs={runs} marketNames={marketNames} />
      )}
    </div>
  );
}
