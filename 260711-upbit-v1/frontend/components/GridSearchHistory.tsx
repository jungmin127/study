'use client';

import { Fragment, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import BacktestCoinFilter, { type CoinFilterOption } from '@/components/BacktestCoinFilter';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime, formatFrequency, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import { parseGridResultTitle } from '@/lib/grid-result-title';
import { deleteGridSearchJob, deleteGridSearchResult } from '@/lib/api/eda';
import type { GridSearchJob, GridSearchJobRequest, GridSearchSavedResult } from '@/lib/types/eda';

const STATUS_LABEL: Record<GridSearchJob['status'], string> = {
  running: '진행중',
  completed: '완료',
  failed: '실패',
  canceled: '취소',
};

const STATUS_VARIANT: Record<GridSearchJob['status'], 'secondary' | 'default' | 'destructive' | 'outline'> = {
  running: 'secondary',
  completed: 'default',
  failed: 'destructive',
  canceled: 'outline',
};

const ALL_TIMEFRAMES = '__all__';

type SortKey = 'start' | 'started_at' | 'return_pct';
type SortDir = 'asc' | 'desc';

function sortValue(job: GridSearchJob, key: SortKey): string | number | null {
  if (key === 'start') return job.start;
  if (key === 'started_at') return job.started_at;
  return job.result_json?.[0]?.return_pct ?? null;
}

function sortJobs(list: GridSearchJob[], key: SortKey | null, dir: SortDir): GridSearchJob[] {
  if (!key) return list;
  const factor = dir === 'asc' ? 1 : -1;
  return [...list].sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

type Expansion =
  | { kind: 'error'; message: string }
  | { kind: 'results'; results: GridSearchSavedResult[] }
  | null;

function expansionFor(job: GridSearchJob): Expansion {
  if (job.status === 'failed' && job.error_message) {
    return { kind: 'error', message: job.error_message };
  }
  const results = job.result_json ?? [];
  if (results.length > 0) {
    return { kind: 'results', results };
  }
  return null;
}

function ResultTitle({ result }: { result: GridSearchSavedResult }) {
  const parsed = parseGridResultTitle(result.title);
  if (!parsed) return <>{result.title}</>;
  return (
    <>
      <strong>매수</strong> {parsed.buyRest} / <strong>매도</strong> {parsed.sellRest}
    </>
  );
}

interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
  onRefresh: () => void | Promise<void>;
  onSubmit: (request: GridSearchJobRequest) => Promise<void>;
}

export default function GridSearchHistory({ jobs, onRefresh, onSubmit }: GridSearchHistoryProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [coinFilter, setCoinFilter] = useState<string | null>(null);
  const [timeframeFilterValue, setTimeframeFilterValue] = useState<string>(ALL_TIMEFRAMES);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [selected, setSelected] = useState<Record<string, Set<string>>>({});
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [jobDeleteTarget, setJobDeleteTarget] = useState<string | null>(null);
  const [jobDeleteBusy, setJobDeleteBusy] = useState(false);
  const [jobDeleteError, setJobDeleteError] = useState<string | null>(null);
  const [chainingTarget, setChainingTarget] = useState<{ jobId: string; result: GridSearchSavedResult } | null>(null);
  const [chainCombinator, setChainCombinator] = useState<'AND' | 'OR'>('AND');
  const [chainCategories, setChainCategories] = useState<string[]>([]);
  const [chainSubmitting, setChainSubmitting] = useState(false);
  const [chainError, setChainError] = useState<string | null>(null);

  const timeframeFilter = timeframeFilterValue === ALL_TIMEFRAMES ? null : timeframeFilterValue;

  const historyJobs = useMemo(() => jobs.filter((j) => j.status !== 'running'), [jobs]);

  const coinOptions = useMemo<CoinFilterOption[]>(() => {
    const seen = new Set<string>();
    const options: CoinFilterOption[] = [];
    for (const j of historyJobs) {
      if (!seen.has(j.market)) {
        seen.add(j.market);
        options.push({ market: j.market });
      }
    }
    return options.sort((a, b) => a.market.localeCompare(b.market));
  }, [historyJobs]);

  const timeframeOptions = useMemo(() => {
    const present = new Set(historyJobs.map((j) => j.timeframe));
    return TIMEFRAME_CODES.filter((tf) => present.has(tf));
  }, [historyJobs]);

  const filtered = useMemo(() => {
    return historyJobs.filter((j) => {
      if (coinFilter && j.market !== coinFilter) return false;
      if (timeframeFilter && j.timeframe !== timeframeFilter) return false;
      return true;
    });
  }, [historyJobs, coinFilter, timeframeFilter]);

  const sorted = useMemo(() => sortJobs(filtered, sortKey, sortDir), [filtered, sortKey, sortDir]);

  const jobDeleteJob = useMemo(() => jobs.find((j) => j.id === jobDeleteTarget) ?? null, [jobs, jobDeleteTarget]);
  const jobDeleteResultCount = jobDeleteJob?.result_json?.length ?? 0;

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ sortKeyOf }: { sortKeyOf: SortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  function toggleResultSelection(jobId: string, runId: string, checked: boolean) {
    setSelected((prev) => {
      const current = new Set(prev[jobId] ?? []);
      if (checked) current.add(runId);
      else current.delete(runId);
      return { ...prev, [jobId]: current };
    });
  }

  function toggleAllForJob(jobId: string, runIds: string[], checked: boolean) {
    setSelected((prev) => ({ ...prev, [jobId]: checked ? new Set(runIds) : new Set() }));
  }

  async function handleConfirmDelete() {
    if (!deleteTarget) return;
    const jobId = deleteTarget;
    const ids = Array.from(selected[jobId] ?? []);
    setBulkDeleting(true);
    setBulkError(null);
    const results = await Promise.allSettled(ids.map((runId) => deleteGridSearchResult(jobId, runId)));
    const failedIds = ids.filter((_, i) => results[i].status === 'rejected');
    setBulkDeleting(false);
    await onRefresh();
    if (failedIds.length > 0) {
      setSelected((prev) => ({ ...prev, [jobId]: new Set(failedIds) }));
      setBulkError(`${failedIds.length}건 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.`);
      return;
    }
    setSelected((prev) => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    setDeleteTarget(null);
  }

  async function handleConfirmJobDelete() {
    if (!jobDeleteTarget) return;
    const jobId = jobDeleteTarget;
    setJobDeleteBusy(true);
    setJobDeleteError(null);
    try {
      await deleteGridSearchJob(jobId);
      setJobDeleteBusy(false);
      setJobDeleteTarget(null);
      await onRefresh();
    } catch {
      setJobDeleteBusy(false);
      setJobDeleteError('삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
    }
  }

  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 실행한 grid search가 없습니다.</p>;
  }

  if (historyJobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 완료된 이력이 없습니다.</p>;
  }

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">요청 이력</h2>

      <div className="mb-3 flex flex-wrap items-center gap-3 rounded-md border bg-muted/30 px-3 py-2">
        <BacktestCoinFilter options={coinOptions} value={coinFilter} onChange={setCoinFilter} />
        <Select value={timeframeFilterValue} onValueChange={(value) => value !== null && setTimeframeFilterValue(value)}>
          <SelectTrigger className="w-40">
            <SelectValue>
              {(value: string | null) => (value && value !== ALL_TIMEFRAMES ? formatTimeframe(value) : '전체 봉타입')}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_TIMEFRAMES}>전체 봉타입</SelectItem>
            {timeframeOptions.map((tf) => (
              <SelectItem key={tf} value={tf}>
                {formatTimeframe(tf)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">조건에 맞는 이력이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>상태</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('start')}>
                  기간 <SortIcon sortKeyOf="start" />
                </button>
              </TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('started_at')}>
                  실행시각 <SortIcon sortKeyOf="started_at" />
                </button>
              </TableHead>
              <TableHead>1위 조건</TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('return_pct')}>
                  1위 수익률 <SortIcon sortKeyOf="return_pct" />
                </button>
              </TableHead>
              <TableHead>삭제</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((job) => {
              const results = job.result_json ?? [];
              const top = results[0];
              const expansion = expansionFor(job);
              const expandable = expansion !== null;
              const isExpanded = expanded.has(job.id);
              const jobSelected = selected[job.id] ?? new Set<string>();

              return (
                <Fragment key={job.id}>
                  <TableRow
                    className={expandable ? 'cursor-pointer' : ''}
                    role={expandable ? 'button' : undefined}
                    tabIndex={expandable ? 0 : undefined}
                    aria-expanded={expandable ? isExpanded : undefined}
                    onClick={() => expandable && toggle(job.id)}
                    onKeyDown={(e) => {
                      if (!expandable) return;
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        toggle(job.id);
                      }
                    }}
                  >
                    <TableCell>
                      {expandable &&
                        (isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABEL[job.status]}</Badge>
                    </TableCell>
                    <TableCell>{job.market.replace('KRW-', '')}</TableCell>
                    <TableCell>{formatTimeframe(job.timeframe)}</TableCell>
                    <TableCell>
                      {job.start} ~ {job.end}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.started_at)}</TableCell>
                    <TableCell className="max-w-[320px] whitespace-normal">
                      {top ? (
                        <Link href={`/backtests/${top.run_id}`} className="underline" onClick={(e) => e.stopPropagation()}>
                          <ResultTitle result={top} />
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {top ? (
                        <span className={returnRateColor(top.return_pct)}>{top.return_pct.toFixed(2)}%</span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                        onClick={(e) => {
                          e.stopPropagation();
                          setJobDeleteTarget(job.id);
                        }}
                        aria-label="이 grid search 이력 삭제"
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                  {isExpanded && expansion?.kind === 'error' && (
                    <TableRow>
                      <TableCell colSpan={9} className="whitespace-normal text-sm text-destructive">
                        {expansion.message}
                      </TableCell>
                    </TableRow>
                  )}
                  {isExpanded && expansion?.kind === 'results' && (
                    <TableRow>
                      <TableCell colSpan={9}>
                        <div className="space-y-2">
                          <div className="flex items-center gap-3">
                            <div className="flex items-center gap-1.5">
                              <Checkbox
                                checked={jobSelected.size > 0 && jobSelected.size === expansion.results.length}
                                onCheckedChange={(checked) =>
                                  toggleAllForJob(
                                    job.id,
                                    expansion.results.map((r) => r.run_id),
                                    checked === true
                                  )
                                }
                                aria-label="이 job의 결과 전체 선택"
                              />
                              <span className="text-xs text-muted-foreground">전체 선택</span>
                            </div>
                            <Button
                              variant="destructive"
                              size="sm"
                              disabled={jobSelected.size === 0}
                              onClick={() => setDeleteTarget(job.id)}
                            >
                              <Trash2 className="size-3.5" />
                              선택 삭제{jobSelected.size > 0 ? ` (${jobSelected.size})` : ''}
                            </Button>
                          </div>
                          <div className="grid grid-cols-[auto_auto_auto_auto_auto_auto_auto_auto] items-center gap-x-3 gap-y-1 text-sm">
                            {expansion.results.map((r) => {
                              const parsed = parseGridResultTitle(r.title);
                              return (
                                <Fragment key={r.run_id}>
                                  <Checkbox
                                    checked={jobSelected.has(r.run_id)}
                                    onCheckedChange={(checked) => toggleResultSelection(job.id, r.run_id, checked === true)}
                                    aria-label={`${r.rank}위 결과 선택`}
                                  />
                                  <span className="text-muted-foreground">{r.rank}위</span>
                                  <span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>
                                  {parsed ? (
                                    <>
                                      <span>
                                        <strong>매수</strong> {parsed.buyRest}
                                      </span>
                                      <span>
                                        <strong>매도</strong> {parsed.sellRest}
                                      </span>
                                    </>
                                  ) : (
                                    <span className="col-span-2">{r.title}</span>
                                  )}
                                  <span className="text-xs text-muted-foreground">
                                    {formatFrequency(r.trade_count ?? 0, r.candle_count)}
                                  </span>
                                  <Link href={`/backtests/${r.run_id}`} className="underline">
                                    보기
                                  </Link>
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => {
                                      setChainingTarget({ jobId: job.id, result: r });
                                      setChainCombinator('AND');
                                      setChainCategories([]);
                                      setChainError(null);
                                    }}
                                  >
                                    이 결과 기반으로 추가 탐색
                                  </Button>
                                </Fragment>
                              );
                            })}
                          </div>
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
            setBulkError(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              선택한 {deleteTarget ? (selected[deleteTarget]?.size ?? 0) : 0}개의 그리드서치 결과를 삭제하시겠습니까?
            </AlertDialogTitle>
            <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
          </AlertDialogHeader>
          {bulkError && <p className="text-sm text-destructive">{bulkError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmDelete} disabled={bulkDeleting}>
              {bulkDeleting ? '삭제 중...' : '삭제'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={jobDeleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setJobDeleteTarget(null);
            setJobDeleteError(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {jobDeleteResultCount > 0
                ? `이 grid search 이력과 저장된 결과 ${jobDeleteResultCount}개를 모두 삭제하시겠습니까?`
                : '이 grid search 이력을 삭제하시겠습니까?'}
            </AlertDialogTitle>
            <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
          </AlertDialogHeader>
          {jobDeleteError && <p className="text-sm text-destructive">{jobDeleteError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmJobDelete} disabled={jobDeleteBusy}>
              {jobDeleteBusy ? '삭제 중...' : '삭제'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={chainingTarget !== null} onOpenChange={(open) => !open && setChainingTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>추가 탐색 (2차 grid search)</AlertDialogTitle>
            <AlertDialogDescription>
              선택한 결과의 매수/매도 조건을 베이스로 고정하고, 아래에서 고른 지표 풀에서 새 후보 1개씩을 결합 방식으로 이어붙여 탐색합니다.
              AND는 베이스 조건을 좁히기만 해 항상 안전합니다(최악의 경우 거래 0건). OR은 베이스와 새 조건 중 하나만 맞아도 매매하므로,
              새 조건의 질이 낮으면 베이스의 성과가 오히려 나빠질 수 있습니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-3">
            <div className="flex gap-4">
              {(['AND', 'OR'] as const).map((c) => (
                <label key={c} className="flex items-center gap-1.5 text-sm">
                  <input type="radio" checked={chainCombinator === c} onChange={() => setChainCombinator(c)} />
                  {c}
                </label>
              ))}
            </div>
            <div className="flex flex-wrap gap-3">
              {['추세', '가격대', '거래량', '거래대금', '시장 심리', '오실레이터'].map((category) => (
                <label key={category} className="flex items-center gap-1.5 text-sm">
                  <Checkbox
                    checked={chainCategories.includes(category)}
                    onCheckedChange={(checked) =>
                      setChainCategories((prev) =>
                        checked === true ? [...prev, category] : prev.filter((c) => c !== category)
                      )
                    }
                  />
                  {category}
                </label>
              ))}
            </div>
            {chainError && <p className="text-sm text-destructive">{chainError}</p>}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              disabled={chainSubmitting}
              onClick={async (e) => {
                e.preventDefault();
                if (!chainingTarget) return;
                if (chainCategories.length === 0) {
                  setChainError('지표 카테고리를 최소 1개 이상 선택하세요.');
                  return;
                }
                const parentJob = jobs.find((j) => j.id === chainingTarget.jobId);
                if (!parentJob) return;
                setChainSubmitting(true);
                setChainError(null);
                try {
                  await onSubmit({
                    market: parentJob.market,
                    timeframe: parentJob.timeframe,
                    capital: parentJob.capital,
                    start: parentJob.start,
                    end: parentJob.end,
                    top_n: parentJob.top_n,
                    indicator_pool: { categories: chainCategories, excluded_indicators: [] },
                    base_run_id: chainingTarget.result.run_id,
                    combinator: chainCombinator,
                  });
                  setChainingTarget(null);
                  await onRefresh();
                } catch {
                  setChainError('체이닝 job 시작에 실패했습니다.');
                } finally {
                  setChainSubmitting(false);
                }
              }}
            >
              {chainSubmitting ? '시작하는 중...' : '탐색 시작'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
