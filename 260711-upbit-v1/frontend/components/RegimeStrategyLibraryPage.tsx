'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import BacktestPickerDialog from '@/components/BacktestPickerDialog';
import { ApiError } from '@/lib/api/client';
import { getBacktestRuns, getMarkets, getRegimeAdxOverview } from '@/lib/api/eda';
import { getLiveStrategies } from '@/lib/api/liveStrategies';
import {
  deleteRegimeStrategyMapping,
  getRegimeStrategyLibrary,
  upsertRegimeStrategyMapping,
} from '@/lib/api/regimeLibrary';
import { MAJOR_MARKETS, TIMEFRAME } from '@/lib/constants/regime';
import type { BacktestRunSummary, Market, RegimeAdxOverviewItem } from '@/lib/types/eda';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import type { RegimeLibrarySlot, RegimeStrategyMapping } from '@/lib/types/regimeLibrary';

const SLOTS: RegimeLibrarySlot[] = ['하락', '횡보', '상승', '기본'];

const LABEL_BG_CLASS: Record<string, string> = {
  상승: 'bg-[color:var(--regime-surge-up)]/15 border-[color:var(--regime-surge-up)]/40',
  하락: 'bg-[color:var(--regime-surge-down)]/15 border-[color:var(--regime-surge-down)]/40',
  횡보: 'bg-muted border-border',
};

function findMapping(
  mappings: RegimeStrategyMapping[], market: string, regime: RegimeLibrarySlot,
): RegimeStrategyMapping | null {
  return mappings.find((m) => m.market === market && m.regime === regime) ?? null;
}

function currentLiveStrategyFor(strategies: LiveStrategy[], market: string): LiveStrategy | null {
  const active = strategies.filter((s) => s.market === market && (s.status === 'running' || s.status === 'paused'));
  return active.find((s) => s.status === 'running') ?? active[0] ?? null;
}

function syncStatusFor(
  market: string,
  currentLabel: string | null,
  mappings: RegimeStrategyMapping[],
  liveStrategies: LiveStrategy[],
): { text: string; tone: 'ok' | 'warn' | 'muted' } {
  const live = currentLiveStrategyFor(liveStrategies, market);
  if (!live) return { text: '라이브 전략 없음', tone: 'muted' };
  const slot: RegimeLibrarySlot = currentLabel === '상승' || currentLabel === '하락' || currentLabel === '횡보' ? currentLabel : '기본';
  const mapping = findMapping(mappings, market, slot);
  if (!mapping) return { text: '매핑 없음', tone: 'muted' };
  return mapping.source_run_id === live.source_run_id
    ? { text: '동기화됨', tone: 'ok' }
    : { text: '전략 교체 필요', tone: 'warn' };
}

export default function RegimeStrategyLibraryPage() {
  const [mappings, setMappings] = useState<RegimeStrategyMapping[]>([]);
  const [overview, setOverview] = useState<RegimeAdxOverviewItem[]>([]);
  const [liveStrategies, setLiveStrategies] = useState<LiveStrategy[]>([]);
  const [backtestRuns, setBacktestRuns] = useState<BacktestRunSummary[]>([]);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [mappingsResult, overviewResult, liveResult, runsResult, marketsResult] = await Promise.all([
        getRegimeStrategyLibrary(),
        getRegimeAdxOverview(TIMEFRAME),
        getLiveStrategies(),
        getBacktestRuns(),
        getMarkets(),
      ]);
      setMappings(mappingsResult);
      setOverview(overviewResult);
      setLiveStrategies(liveResult);
      setBacktestRuns(runsResult);
      setMarkets(marketsResult);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '전략 라이브러리를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  if (loading) return <p className="text-muted-foreground">불러오는 중...</p>;
  if (error) return <p className="text-destructive">{error}</p>;

  return (
    <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>코인</TableHead>
            <TableHead>현재장세</TableHead>
            <TableHead>라이브전략 상태</TableHead>
            {SLOTS.map((slot) => (
              <TableHead key={slot}>{slot}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {MAJOR_MARKETS.map((market) => {
            const koreanName = markets.find((m) => m.market === market)?.korean_name ?? market;
            const overviewItem = overview.find((o) => o.market === market);
            const currentLabel = overviewItem?.label ?? null;
            const sync = syncStatusFor(market, currentLabel, mappings, liveStrategies);

            return (
              <TableRow key={market}>
                <TableCell className="whitespace-nowrap font-medium">{koreanName}</TableCell>
                <TableCell>
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${currentLabel ? LABEL_BG_CLASS[currentLabel] : 'bg-muted border-border'}`}>
                    {currentLabel ?? '미분류'}
                  </span>
                </TableCell>
                <TableCell>
                  <span className={sync.tone === 'ok' ? 'text-[color:var(--regime-surge-up)]' : sync.tone === 'warn' ? 'text-[color:var(--regime-surge-down)]' : 'text-muted-foreground'}>
                    {sync.text}
                  </span>
                </TableCell>
                {SLOTS.map((slot) => {
                  const mapping = findMapping(mappings, market, slot);
                  const run = mapping ? backtestRuns.find((r) => r.run_id === mapping.source_run_id) : null;
                  const summary = mapping ? (run ? (run.title ?? run.run_id) : '삭제된 백테스트 결과') : null;

                  return (
                    <TableCell key={slot} className="min-w-40">
                      {summary && <p className="mb-1 truncate text-xs text-muted-foreground">{summary}</p>}
                      <div className="flex gap-1">
                        <BacktestPickerDialog
                          market={market}
                          title={`${koreanName} — ${slot} 전략 설정`}
                          excludeRunId={mapping?.source_run_id}
                          trigger={
                            <Button variant="outline" size="sm">
                              {mapping ? '변경' : '설정'}
                            </Button>
                          }
                          onSelect={(runId) => upsertRegimeStrategyMapping(market, slot, runId).then(() => loadAll())}
                        />
                        {mapping && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => deleteRegimeStrategyMapping(market, slot).then(() => loadAll())}
                          >
                            제거
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
