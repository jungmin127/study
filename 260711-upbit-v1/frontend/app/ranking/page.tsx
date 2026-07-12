import { getRanking } from '@/lib/api/eda';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default async function RankingPage() {
  const rows = await getRanking();

  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">혼합전략 코인 랭킹</h1>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">아직 혼합 전략 스윕 데이터가 없습니다.</p>
      ) : (
        <div className="grid gap-3">
          {rows.map((row, i) => (
            <Card key={`${row.signal_set_name}-${row.market}-${row.timeframe}`}>
              <CardHeader>
                <CardTitle className="flex items-center justify-between text-base">
                  <span>
                    #{i + 1} {row.market} · {row.timeframe}
                  </span>
                  <span className={row.return_rate && row.return_rate > 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                    {row.return_rate?.toFixed(2) ?? '-'}%
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-muted-foreground">
                전략: {row.signal_set_name} · Sharpe: {row.sharpe?.toFixed(2) ?? '-'} · MDD: {row.max_drawdown?.toFixed(2) ?? '-'}%
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
