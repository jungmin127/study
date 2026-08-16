import { Clock, Gauge, Percent, Repeat, Scale } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { getBacktestDetail } from '@/lib/api/eda';
import PriceChart from '@/components/PriceChart';
import MetricTile from '@/components/MetricTile';
import RefreshBacktestButton from '@/components/RefreshBacktestButton';
import GoLiveButton from '@/components/GoLiveButton';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime, formatHoldingPeriod, formatTimeframe } from '@/lib/format';
import type { BacktestMetrics } from '@/lib/types/eda';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  const tiles: { label: string; value: string; colorClass?: string; tooltip: string; icon: LucideIcon }[] = [
    {
      label: '총 수익률', value: fmtPct(metrics.total_return), colorClass: returnRateColor(metrics.total_return),
      tooltip: '초기 자본 대비 최종 자산의 증감률입니다.', icon: Percent,
    },
    {
      label: 'CAGR', value: fmtPct(metrics.cagr), colorClass: returnRateColor(metrics.cagr),
      tooltip: '연평균 복리 성장률입니다. 백테스트 기간과 무관하게 "연 단위로 환산하면 몇 %인가"를 보여줍니다.', icon: Percent,
    },
    {
      label: 'Buy&Hold', value: fmtPct(metrics.buy_and_hold_return), colorClass: returnRateColor(metrics.buy_and_hold_return),
      tooltip: '같은 기간 동안 그냥 사서 들고만 있었을 때의 수익률입니다. 전략이 단순 보유보다 나은지 비교하는 기준입니다.', icon: Percent,
    },
    {
      label: 'MDD', value: fmtPct(metrics.mdd), colorClass: returnRateColor(metrics.mdd),
      tooltip: '최대 낙폭(Max Drawdown). 자산이 고점 대비 가장 많이 떨어졌던 비율입니다. 작을수록(0에 가까울수록) 좋습니다.', icon: Percent,
    },
    {
      label: '샤프 비율', value: metrics.sharpe_ratio.toFixed(2),
      tooltip: '위험(변동성) 대비 수익률입니다. 무위험수익률 0%를 가정하며, 높을수록 안정적으로 수익을 냈다는 뜻입니다.', icon: Gauge,
    },
    {
      label: '소르티노', value: metrics.sortino_ratio.toFixed(2),
      tooltip: '샤프 비율과 비슷하지만 하락 변동성만 위험으로 봅니다. 상승 변동은 페널티로 치지 않아 샤프보다 후하게 나올 수 있습니다.', icon: Gauge,
    },
    {
      label: '칼마 비율', value: metrics.calmar_ratio.toFixed(2),
      tooltip: 'CAGR을 MDD(절대값)로 나눈 값입니다. 수익뿐 아니라 "그 수익을 위해 감수한 최대 손실"까지 함께 고려합니다.', icon: Gauge,
    },
    {
      label: '총 거래', value: `${metrics.total_trades}건`,
      tooltip: '백테스트 기간 동안 체결된 매수→매도 거래 쌍의 개수입니다.', icon: Repeat,
    },
    {
      label: '승률', value: `${metrics.win_rate.toFixed(1)}%`,
      tooltip: '전체 거래 중 수익이 난(pnl > 0) 거래의 비율입니다.', icon: Percent,
    },
    {
      label: '손익비', value: metrics.profit_factor.toFixed(2),
      tooltip: '총 이익 금액을 총 손실 금액으로 나눈 값입니다(Profit Factor). 1보다 크면 이익이 손실보다 큽니다.', icon: Scale,
    },
    {
      label: '평균 보유', value: `${metrics.avg_holding_period.toFixed(1)}일`,
      tooltip: '한 번 진입해서 청산까지 평균적으로 보유한 기간(일)입니다.', icon: Clock,
    },
    {
      label: '최대연속손실', value: `${metrics.max_consecutive_loss}건`,
      tooltip: '연속으로 손실이 난 거래의 최대 횟수입니다. 클수록 연속 손실 구간에서 심리적/자금 압박이 컸다는 뜻입니다.', icon: Repeat,
    },
  ];

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">성과 지표</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
        {tiles.map((tile) => (
          <MetricTile
            key={tile.label} label={tile.label} value={tile.value}
            colorClass={tile.colorClass} tooltip={tile.tooltip} icon={tile.icon}
          />
        ))}
      </div>
    </div>
  );
}

export default async function BacktestDetailPage({ params }: { params: { runId: string } }) {
  const detail = await getBacktestDetail(params.runId);

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold">백테스트 상세</h1>
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <p className="text-sm text-muted-foreground">
          {detail.market} · {formatTimeframe(detail.timeframe)} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
        </p>
        <RefreshBacktestButton runId={params.runId} />
        <GoLiveButton runId={params.runId} />
      </div>
      {detail.live_price_as_of && (
        <p className="mb-4 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <Clock className="size-3.5" />
          미청산 포지션이 있어 현재가 기준으로 재평가됨 ({formatDateTime(detail.live_price_as_of)} 기준)
        </p>
      )}

      <div className="mb-6 rounded-md border p-4">
        <div className="mb-3">
          <MetricTile
            label="총 수익률" value={fmtPct(detail.metrics.total_return)}
            colorClass={returnRateColor(detail.metrics.total_return)} icon={Percent}
          />
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <MetricTile label="MDD" value={fmtPct(detail.metrics.mdd)} colorClass={returnRateColor(detail.metrics.mdd)} icon={Percent} />
          <MetricTile label="총 거래" value={`${detail.metrics.total_trades}건`} icon={Repeat} />
          <MetricTile label="최초 투입금" value={`${Math.round(detail.initial_capital).toLocaleString()}원`} />
          <MetricTile label="최종 금액" value={`${Math.round(detail.final_value).toLocaleString()}원`} />
        </div>
      </div>

      <div className="mb-6">
        <MetricsGrid metrics={detail.metrics} />
      </div>

      <h2 className="mb-2 font-medium">가격 차트</h2>
      <PriceChart ohlcv={detail.ohlcv} trades={detail.trades} timeframe={detail.timeframe} backtestEnd={detail.end} />

      <h2 className="mt-6 mb-2 font-medium">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>진입</TableHead>
              <TableHead>청산</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>매수가</TableHead>
              <TableHead>매도가</TableHead>
              <TableHead>수익금</TableHead>
              <TableHead>보유기간</TableHead>
              <TableHead>상태</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell>{formatDateTime(t.entryTime)}</TableCell>
                <TableCell>{formatDateTime(t.exitTime)}</TableCell>
                <TableCell className={returnRateColor(t.returnRate)}>{t.returnRate.toFixed(2)}</TableCell>
                <TableCell>{t.entryPrice.toLocaleString()}</TableCell>
                <TableCell>{t.exitPrice.toLocaleString()}</TableCell>
                <TableCell className={returnRateColor(t.pnl)}>{Math.round(t.pnl).toLocaleString()}</TableCell>
                <TableCell>{formatHoldingPeriod(t.holdingPeriod, detail.timeframe)}</TableCell>
                <TableCell>
                  {t.forceClosed ? (
                    <Badge
                      variant="secondary"
                      title={
                        detail.live_price_as_of
                          ? '매도 조건을 만족하지 못한 채 아직 보유 중입니다. 현재가로 재평가된 수익률입니다.'
                          : '매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가된 상태입니다.'
                      }
                    >
                      보유중(기간종료)
                    </Badge>
                  ) : (
                    <Badge variant="outline">청산됨</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
