'use client';

import { Fragment, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import BacktestCoinFilter, { type CoinFilterOption } from '@/components/BacktestCoinFilter';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import { parseGridResultTitle } from '@/lib/grid-result-title';
import type { GridSearchJob, GridSearchSavedResult } from '@/lib/types/eda';

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
  if (results.length > 1) {
    return { kind: 'results', results: results.slice(1) };
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
}

export default function GridSearchHistory({ jobs }: GridSearchHistoryProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [coinFilter, setCoinFilter] = useState<string | null>(null);
  const [timeframeFilterValue, setTimeframeFilterValue] = useState<string>(ALL_TIMEFRAMES);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

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
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((job) => {
              const results = job.result_json ?? [];
              const top = results[0];
              const expansion = expansionFor(job);
              const expandable = expansion !== null;
              const isExpanded = expanded.has(job.id);

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
                    <TableCell className="max-w-[320px] truncate">
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
                  </TableRow>
                  {isExpanded && expansion?.kind === 'error' && (
                    <TableRow>
                      <TableCell colSpan={8} className="whitespace-normal text-sm text-destructive">
                        {expansion.message}
                      </TableCell>
                    </TableRow>
                  )}
                  {isExpanded && expansion?.kind === 'results' && (
                    <TableRow>
                      <TableCell colSpan={8}>
                        <div className="space-y-1">
                          {expansion.results.map((r) => (
                            <div key={r.run_id} className="flex items-center gap-2 text-sm">
                              <span className="text-muted-foreground">{r.rank}위</span>
                              <span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>
                              <Link href={`/backtests/${r.run_id}`} className="truncate underline">
                                <ResultTitle result={r} />
                              </Link>
                            </div>
                          ))}
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
    </div>
  );
}
