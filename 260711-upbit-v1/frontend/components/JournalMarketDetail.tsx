'use client';

import type { JournalMarketDetail } from '@/lib/types/journal';
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

// lib/return-rate-color.ts의 returnRateColor와 의도적으로 다른 헬퍼: 여기선 0을
// 수익(빨강)으로 취급한다 — 이 컴포넌트의 승률 정의("손익이 0 이상인 비율")와
// 맞추기 위함. returnRateColor로 통합하지 말 것(0 처리 결과가 달라짐).
function pnlColorClass(value: number): string {
  return value >= 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}

export default function JournalMarketDetailView({
  detail,
}: {
  detail: JournalMarketDetail;
}) {
  const comparison = detail.backtest_comparison;
  const recentFirstTradeLog = [...detail.trade_log].reverse();

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div className="rounded-md border p-3">
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="text-center">
            <p className="text-muted-foreground">누적손익</p>
            <p className="text-sm font-semibold">
              {fmtKrw(detail.cumulative_pnl)} ({fmtPct(detail.cumulative_pnl_pct)})
            </p>
          </div>
          <div className="text-center">
            <p className="text-muted-foreground">MDD</p>
            <p className="text-sm font-semibold">{fmtPct(detail.mdd_pct)}</p>
          </div>
          <div className="text-center">
            <p className="text-muted-foreground">승률</p>
            <p className="text-sm font-semibold">{detail.win_rate_pct.toFixed(1)}%</p>
          </div>
          <div className="text-center">
            <p className="text-muted-foreground">평균 · 최대 슬리피지</p>
            <p className="text-sm font-semibold">
              {detail.avg_slippage_pct !== null ? fmtPct(detail.avg_slippage_pct) : 'N/A'}
              {' · '}
              {detail.max_slippage_pct !== null ? fmtPct(detail.max_slippage_pct) : 'N/A'}
            </p>
          </div>

          {comparison === null ? (
            <p className="col-span-2 border-t border-dashed pt-3 text-center text-muted-foreground">
              백테스트 비교 불가(연결된 백테스트 결과가 없습니다).
            </p>
          ) : (
            <>
              <div className="col-span-2 border-t border-dashed" />
              <div className="text-center">
                <p className="text-muted-foreground">승률</p>
                <p className="text-sm font-semibold">
                  {comparison.backtest.win_rate_pct.toFixed(1)}%
                  <span className="text-muted-foreground"> → </span>
                  {comparison.live.win_rate_pct.toFixed(1)}%
                </p>
              </div>
              <div className="text-center">
                <p className="text-muted-foreground">평균수익률</p>
                <p className="text-sm font-semibold">
                  {fmtPct(comparison.backtest.avg_return_pct)}
                  <span className="text-muted-foreground"> → </span>
                  {fmtPct(comparison.live.avg_return_pct)}
                </p>
              </div>
              <div className="text-center">
                <p className="text-muted-foreground">MDD</p>
                <p className="text-sm font-semibold">
                  {fmtPct(comparison.backtest.mdd_pct)}
                  <span className="text-muted-foreground"> → </span>
                  {fmtPct(comparison.live.mdd_pct)}
                </p>
              </div>
              <div className="text-center">
                <p className="text-muted-foreground">거래횟수</p>
                <p className="text-sm font-semibold">
                  {comparison.backtest.trade_count}
                  <span className="text-muted-foreground"> → </span>
                  {comparison.live.trade_count}건
                </p>
              </div>
            </>
          )}
        </div>
        {comparison?.sample_size_warning && (
          <p className="mt-2 text-center text-xs text-amber-600 dark:text-amber-400">
            실매매 표본이 10건 미만이라 통계적으로 신뢰하기 이릅니다.
          </p>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold">
          매매일지
          {detail.trade_log.length > 0 && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              전체 {detail.trade_log.length}건
            </span>
          )}
        </h3>
        {detail.trade_log.length === 0 ? (
          <p className="text-sm text-muted-foreground">청산된 거래가 없습니다.</p>
        ) : (
          <div className="max-h-[320px] overflow-y-auto rounded-md border p-2">
            <div className="hidden md:block">
              <Table>
                <TableHeader className="sticky top-0 bg-background">
                  <TableRow>
                    <TableHead>진입</TableHead>
                    <TableHead>청산</TableHead>
                    <TableHead>손익</TableHead>
                    <TableHead>청산사유</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recentFirstTradeLog.map((t) => (
                    <TableRow key={t.position_id}>
                      <TableCell>
                        {formatDateTime(t.entry_time)}
                        <br />
                        {Math.round(t.entry_price).toLocaleString()}원
                      </TableCell>
                      <TableCell>
                        {formatDateTime(t.exit_time)}
                        <br />
                        {Math.round(t.exit_price).toLocaleString()}원
                      </TableCell>
                      <TableCell className={pnlColorClass(t.realized_pnl)}>
                        {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                      </TableCell>
                      <TableCell>{fmtCloseReason(t.close_reason)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="space-y-2 md:hidden">
              {recentFirstTradeLog.map((t) => (
                <div key={t.position_id} className="rounded-md border p-3 text-sm">
                  <p className="text-xs text-muted-foreground">
                    진입 {formatDateTime(t.entry_time)} · {Math.round(t.entry_price).toLocaleString()}원
                  </p>
                  <p className="text-xs text-muted-foreground">
                    청산 {formatDateTime(t.exit_time)} · {Math.round(t.exit_price).toLocaleString()}원
                  </p>
                  <p className="mt-1">
                    <span className={pnlColorClass(t.realized_pnl)}>
                      {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                    </span>
                    {' · '}
                    {fmtCloseReason(t.close_reason)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
