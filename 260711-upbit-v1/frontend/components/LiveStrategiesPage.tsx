'use client';

import { useCallback, useState } from 'react';
import { Check, CircleHelp, Coins, Pause, Play, Square, Trash2, X } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import {
  approveLiveStrategy,
  deleteLiveStrategy,
  getLiveStrategies,
  pauseLiveStrategy,
  resumeLiveStrategy,
  stopLiveStrategy,
  updateLiveStrategyCapital,
} from '@/lib/api/liveStrategies';
import type { LiveStrategy, LiveStrategyRiskConfig } from '@/lib/types/liveStrategies';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { summarizeGroup } from '@/lib/condition-summary';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { INPUT_CLASS } from '@/lib/ui-classes';
import { returnRateColor } from '@/lib/return-rate-color';
import { useVisiblePolling } from '@/lib/hooks/useVisiblePolling';

const POLL_INTERVAL_MS = 5000;

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

const RISK_CONFIG_LABELS: Record<keyof LiveStrategyRiskConfig, string> = {
  position_sizing_mode: '포지션 사이징 방식',
  position_sizing_value: '포지션 사이징 값',
  max_position_per_market: '코인당 최대 포지션',
  order_execution_mode: '주문 방식',
  order_timeout_sec: '주문 타임아웃(초)',
  manual_intervention_policy: '수동 개입 정책',
  daily_loss_limit_pct: '일일 손실 한도(%)',
  consecutive_loss_limit: '연속 손실 한도',
};

const POSITION_SIZING_MODE_LABELS: Record<string, string> = {
  fixed: '고정 금액',
  percent: '계좌잔고 비율(%)',
};

const ORDER_EXECUTION_MODE_LABELS: Record<string, string> = {
  market: '시장가',
  limit: '지정가',
  limit_timeout: '지정가(타임아웃 시 시장가 전환)',
};

const MANUAL_INTERVENTION_POLICY_LABELS: Record<string, string> = {
  all_stop: '전체 정지',
  acknowledge_and_continue: '확인 후 계속',
};

function formatRiskConfigValue(key: string, value: number | string): string {
  if (key === 'position_sizing_mode') return POSITION_SIZING_MODE_LABELS[value as string] ?? String(value);
  if (key === 'order_execution_mode') return ORDER_EXECUTION_MODE_LABELS[value as string] ?? String(value);
  if (key === 'manual_intervention_policy') return MANUAL_INTERVENTION_POLICY_LABELS[value as string] ?? String(value);
  return String(value);
}

function Stat({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="flex-1 border-l border-border pl-2.5 first:border-l-0 first:pl-0">
      <p className="text-[0.68rem] text-muted-foreground">{label}</p>
      <p className={`text-sm font-semibold tabular-nums ${valueClassName ?? ''}`}>{value}</p>
    </div>
  );
}

function ChangeCapitalDialog({
  strategy,
  onChanged,
}: {
  strategy: LiveStrategy;
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const newCapital = Number(value);
    if (!Number.isFinite(newCapital) || newCapital <= 0) {
      setError('0보다 큰 숫자를 입력하세요.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await updateLiveStrategyCapital(strategy.id, newCapital);
      await onChanged();
      setOpen(false);
      setValue('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '시드 변경에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  function closeAndReset() {
    setOpen(false);
    setValue('');
    setError(null);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setOpen(true);
        } else {
          closeAndReset();
        }
      }}
    >
      <DialogTrigger
        type="button"
        className={buttonVariants({ variant: 'outline', size: 'icon-lg' })}
        aria-label="시드 변경"
        title="시드 변경"
      >
        <Coins />
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>시드 변경</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p>
            현재 자본:{' '}
            <span className="font-semibold tabular-nums">
              {strategy.current_capital !== null
                ? `${Math.round(strategy.current_capital).toLocaleString()}원`
                : '-'}
            </span>
          </p>
          <input
            type="number"
            inputMode="decimal"
            className={INPUT_CLASS}
            placeholder="새 시드 금액(원)"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {error && <p className="text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={closeAndReset} disabled={submitting}>
            취소
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            확인
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function LiveStrategiesPage() {
  const [strategies, setStrategies] = useState<LiveStrategy[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getLiveStrategies();
      setStrategies(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '전략 목록을 불러오지 못했습니다.');
    }
  }, []);

  useVisiblePolling(refresh, POLL_INTERVAL_MS);

  async function runAction<T>(id: string, action: (id: string) => Promise<T>) {
    setActionError(null);
    setPendingId(id);
    try {
      await action(id);
      await refresh();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '요청 처리 중 오류가 발생했습니다.');
    } finally {
      setPendingId(null);
    }
  }

  if (loadError) return <p className="text-sm text-destructive">{loadError}</p>;
  if (strategies.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        등록된 라이브 전략이 없습니다. 백테스트 상세 페이지에서 시작하세요.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {actionError && <p className="text-sm text-destructive">{actionError}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3">
        {strategies.map((s) => (
          <Card
            key={s.id}
            className={`gap-2 overflow-hidden border-l-[3px] py-3 md:gap-3 md:py-4 ${
              s.open_position ? 'border-l-red-500' : 'border-l-transparent'
            }`}
          >
            <div className="flex items-center justify-between gap-2 px-4">
              <span className="min-w-0 truncate text-sm font-semibold">
                {s.market} · {formatTimeframe(s.timeframe)}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                <Badge variant={s.status === 'running' ? 'default' : 'secondary'}>{s.status}</Badge>
                <Dialog>
                  <DialogTrigger
                    type="button"
                    className={buttonVariants({ variant: 'outline', size: 'icon-lg' })}
                    aria-label="전략 설정 보기"
                    title="전략 설정 보기"
                  >
                    <CircleHelp />
                  </DialogTrigger>
                  <DialogContent className="max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                      <DialogTitle>
                        {s.market} · {formatTimeframe(s.timeframe)} 전략 설정
                      </DialogTitle>
                    </DialogHeader>
                    <div className="space-y-3 text-sm">
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">매수 조건</p>
                        <p className="rounded-md bg-muted/50 p-2 font-mono text-xs">
                          {summarizeGroup(s.buy_conditions)}
                        </p>
                      </div>
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">매도 조건</p>
                        <p className="rounded-md bg-muted/50 p-2 font-mono text-xs">
                          {summarizeGroup(s.sell_conditions)}
                        </p>
                      </div>
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">리스크 관리</p>
                        <div className="space-y-1 rounded-md bg-muted/50 p-2">
                          {(Object.entries(RISK_CONFIG_LABELS) as [keyof LiveStrategyRiskConfig, string][]).map(
                            ([key, label]) => (
                              <div key={key} className="flex justify-between gap-2">
                                <span className="text-muted-foreground">{label}</span>
                                <span className="tabular-nums">{formatRiskConfigValue(key, s.risk_config[key])}</span>
                              </div>
                            ),
                          )}
                        </div>
                      </div>
                      <div>
                        <p className="mb-1 font-medium text-muted-foreground">자본 변경 이력</p>
                        {s.capital_adjustments.length === 0 ? (
                          <p className="rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                            변경 이력 없음
                          </p>
                        ) : (
                          <div className="space-y-1 rounded-md bg-muted/50 p-2">
                            {s.capital_adjustments.map((adj) => (
                              <div
                                key={adj.id}
                                className="flex flex-wrap items-baseline justify-between gap-x-2 text-xs"
                              >
                                <span className="text-muted-foreground">{formatDateTime(adj.adjusted_at)}</span>
                                <span className="tabular-nums">
                                  {Math.round(adj.previous_capital).toLocaleString()}원 →{' '}
                                  {Math.round(adj.new_capital).toLocaleString()}원
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </DialogContent>
                </Dialog>
                {s.open_position === null && (s.status === 'running' || s.status === 'paused') && (
                  <ChangeCapitalDialog strategy={s} onChanged={refresh} />
                )}
                {s.status === 'draft' && (
                  <>
                    <Button
                      size="icon-lg"
                      aria-label="승인"
                      title="승인"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, approveLiveStrategy)}
                    >
                      <Check />
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="outline"
                      aria-label="취소"
                      title="취소"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      <X />
                    </Button>
                  </>
                )}
                {s.status === 'running' && (
                  <>
                    <Button
                      size="icon-lg"
                      variant="outline"
                      aria-label="일시정지"
                      title="일시정지"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, pauseLiveStrategy)}
                    >
                      <Pause />
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="destructive"
                      aria-label="중지"
                      title="중지"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      <Square />
                    </Button>
                  </>
                )}
                {s.status === 'paused' && (
                  <>
                    <Button
                      size="icon-lg"
                      aria-label="재개"
                      title="재개"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, resumeLiveStrategy)}
                    >
                      <Play />
                    </Button>
                    <Button
                      size="icon-lg"
                      variant="destructive"
                      aria-label="중지"
                      title="중지"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, stopLiveStrategy)}
                    >
                      <Square />
                    </Button>
                  </>
                )}
                {s.status === 'stopped' && (
                  <AlertDialog>
                    {/* AlertDialogTrigger has no asChild in this project's base-ui-backed
                        shadcn style; apply Button's own class-variance styles directly
                        (same pattern as BacktestRunsTable.tsx). */}
                    <AlertDialogTrigger
                      type="button"
                      className={buttonVariants({ variant: 'destructive', size: 'icon-lg' })}
                      aria-label="삭제"
                      title="삭제"
                      disabled={pendingId === s.id}
                    >
                      <Trash2 />
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>이 전략을 삭제하시겠습니까?</AlertDialogTitle>
                        <AlertDialogDescription>
                          이 전략의 거래·주문 내역과 매매일지 기록도 함께 삭제되며, 되돌릴 수 없습니다.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>취소</AlertDialogCancel>
                        <AlertDialogAction onClick={() => runAction(s.id, deleteLiveStrategy)}>
                          삭제
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
              </div>
            </div>

            <div className="flex px-4">
              {s.current_capital !== null && (
                <Stat label="현재 자금" value={`${Math.round(s.current_capital).toLocaleString()}원`} />
              )}
              {s.open_position ? (
                <>
                  <Stat label="진입가" value={Math.round(s.open_position.entry_price).toLocaleString()} />
                  <Stat
                    label="손익"
                    value={
                      s.open_position.unrealized_pnl_pct !== null
                        ? fmtPct(s.open_position.unrealized_pnl_pct)
                        : '-'
                    }
                    valueClassName={returnRateColor(s.open_position.unrealized_pnl_pct)}
                  />
                </>
              ) : (
                s.status !== 'draft' && <Stat label="포지션" value={s.status === 'stopped' ? '중지됨' : '없음'} />
              )}
            </div>

            {s.status === 'stopped' && s.stopped_at && (
              <p className="px-4 text-xs text-muted-foreground">중지 시각: {s.stopped_at}</p>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
