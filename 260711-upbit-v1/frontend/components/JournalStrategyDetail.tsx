'use client';

import type { JournalStrategyDetail } from '@/lib/types/journal';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { formatDateTime } from '@/lib/format';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function fmtKrw(value: number): string {
  return `${Math.round(value).toLocaleString()}원`;
}

const CLOSE_REASON_LABELS: Record<string, string> = {
  take_profit: '익절',
  stop_loss: '손절',
  sell_signal: '매도신호',
  manual: '수동청산',
  circuit_breaker: '서킷브레이커',
};

function fmtCloseReason(reason: string): string {
  return CLOSE_REASON_LABELS[reason] ?? reason;
}

export default function JournalStrategyDetailView({
  detail,
}: {
  detail: JournalStrategyDetail;
}) {
  const comparison = detail.backtest_comparison;

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <p className="text-xs text-muted-foreground">누적손익</p>
          <p className="font-semibold">
            {fmtKrw(detail.cumulative_pnl)} ({fmtPct(detail.cumulative_pnl_pct)})
          </p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">MDD</p>
          <p className="font-semibold">{fmtPct(detail.mdd_pct)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">승률</p>
          <p className="font-semibold">{detail.win_rate_pct.toFixed(1)}%</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">평균 · 최대 슬리피지</p>
          <p className="font-semibold">
            {detail.avg_slippage_pct !== null ? fmtPct(detail.avg_slippage_pct) : 'N/A'}
            {' · '}
            {detail.max_slippage_pct !== null ? fmtPct(detail.max_slippage_pct) : 'N/A'}
          </p>
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">백테스트 vs 실매매</h3>
        {comparison === null ? (
          <p className="text-sm text-muted-foreground">
            백테스트 비교 불가(연결된 백테스트 결과가 없습니다).
          </p>
        ) : (
          <>
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead></TableHead>
                    <TableHead>백테스트</TableHead>
                    <TableHead>실매매</TableHead>
                    <TableHead>차이</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell>승률</TableCell>
                    <TableCell>{comparison.backtest.win_rate_pct.toFixed(1)}%</TableCell>
                    <TableCell>{comparison.live.win_rate_pct.toFixed(1)}%</TableCell>
                    <TableCell>
                      {fmtPct(comparison.live.win_rate_pct - comparison.backtest.win_rate_pct)}p
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>평균수익률</TableCell>
                    <TableCell>{fmtPct(comparison.backtest.avg_return_pct)}</TableCell>
                    <TableCell>{fmtPct(comparison.live.avg_return_pct)}</TableCell>
                    <TableCell>
                      {fmtPct(comparison.live.avg_return_pct - comparison.backtest.avg_return_pct)}p
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>MDD</TableCell>
                    <TableCell>{fmtPct(comparison.backtest.mdd_pct)}</TableCell>
                    <TableCell>{fmtPct(comparison.live.mdd_pct)}</TableCell>
                    <TableCell>
                      {fmtPct(comparison.live.mdd_pct - comparison.backtest.mdd_pct)}p
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>거래횟수</TableCell>
                    <TableCell>{comparison.backtest.trade_count}건</TableCell>
                    <TableCell>{comparison.live.trade_count}건</TableCell>
                    <TableCell>-</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:hidden">
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">승률</p>
                <p>
                  백테스트 {comparison.backtest.win_rate_pct.toFixed(1)}% · 실매매{' '}
                  {comparison.live.win_rate_pct.toFixed(1)}%
                </p>
                <p className="text-xs text-muted-foreground">
                  차이 {fmtPct(comparison.live.win_rate_pct - comparison.backtest.win_rate_pct)}p
                </p>
              </div>
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">평균수익률</p>
                <p>
                  백테스트 {fmtPct(comparison.backtest.avg_return_pct)} · 실매매{' '}
                  {fmtPct(comparison.live.avg_return_pct)}
                </p>
                <p className="text-xs text-muted-foreground">
                  차이 {fmtPct(comparison.live.avg_return_pct - comparison.backtest.avg_return_pct)}p
                </p>
              </div>
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">MDD</p>
                <p>
                  백테스트 {fmtPct(comparison.backtest.mdd_pct)} · 실매매 {fmtPct(comparison.live.mdd_pct)}
                </p>
                <p className="text-xs text-muted-foreground">
                  차이 {fmtPct(comparison.live.mdd_pct - comparison.backtest.mdd_pct)}p
                </p>
              </div>
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">거래횟수</p>
                <p>
                  백테스트 {comparison.backtest.trade_count}건 · 실매매 {comparison.live.trade_count}건
                </p>
              </div>
            </div>
            {comparison.sample_size_warning && (
              <p className="mt-2 text-xs text-amber-600">
                실매매 표본이 10건 미만이라 통계적으로 신뢰하기 이릅니다.
              </p>
            )}
          </>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">매매일지</h3>
        {detail.trade_log.length === 0 ? (
          <p className="text-sm text-muted-foreground">청산된 거래가 없습니다.</p>
        ) : (
          <>
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>진입</TableHead>
                    <TableHead>청산</TableHead>
                    <TableHead>손익</TableHead>
                    <TableHead>청산사유</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.trade_log.map((t) => (
                    <TableRow key={t.position_id}>
                      <TableCell>
                        {formatDateTime(t.entry_time)}
                        <br />
                        {Math.round(t.entry_price).toLocaleString()}원 × {t.entry_qty}
                      </TableCell>
                      <TableCell>
                        {formatDateTime(t.exit_time)}
                        <br />
                        {Math.round(t.exit_price).toLocaleString()}원 × {t.exit_qty}
                      </TableCell>
                      <TableCell>
                        {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                      </TableCell>
                      <TableCell>{fmtCloseReason(t.close_reason)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="space-y-2 md:hidden">
              {detail.trade_log.map((t) => (
                <div key={t.position_id} className="rounded-md border p-3 text-sm">
                  <p className="text-xs text-muted-foreground">
                    진입 {formatDateTime(t.entry_time)} · {Math.round(t.entry_price).toLocaleString()}원 ×{' '}
                    {t.entry_qty}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    청산 {formatDateTime(t.exit_time)} · {Math.round(t.exit_price).toLocaleString()}원 ×{' '}
                    {t.exit_qty}
                  </p>
                  <p className="mt-1">
                    {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)}) · {fmtCloseReason(t.close_reason)}
                  </p>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
