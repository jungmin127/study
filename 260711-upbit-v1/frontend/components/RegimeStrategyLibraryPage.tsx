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
import {
  MAJOR_MARKETS,
  REGIME_LABEL_BG_CLASS as LABEL_BG_CLASS,
  REGIME_UNCLASSIFIED_BG_CLASS as UNCLASSIFIED_BG_CLASS,
  TIMEFRAME,
} from '@/lib/constants/regime';
import type { BacktestRunSummary, Market, RegimeAdxOverviewItem } from '@/lib/types/eda';
import type { LiveStrategy } from '@/lib/types/liveStrategies';
import type { RegimeLibrarySlot, RegimeStrategyMapping } from '@/lib/types/regimeLibrary';

const SLOTS: RegimeLibrarySlot[] = ['하락', '횡보', '상승', '기본'];

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
  // 백테스트 결과 목록 자체를 못 불러온 경우, 매핑에 남은 run_id를
  // "삭제된 백테스트 결과"로 오인 표시하지 않기 위한 구분용 플래그.
  const [runsLoadFailed, setRunsLoadFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingDeleteKey, setPendingDeleteKey] = useState<string | null>(null);

  // 최초 마운트 시 1회만 호출되는 전체 로드. 5개 소스 중 이 페이지의
  // 존재 이유인 매핑 목록(getRegimeStrategyLibrary)만 실패를 치명적으로
  // 취급하고, 나머지 4개는 개별 실패해도 화면 자체는 뜨도록 degrade한다
  // (RegimeAdxOverview.tsx와 동일한 Promise.allSettled 패턴).
  async function loadAll() {
    setLoading(true);
    setError(null);
    const [mappingsResult, overviewResult, liveResult, runsResult, marketsResult] = await Promise.allSettled([
      getRegimeStrategyLibrary(),
      getRegimeAdxOverview(TIMEFRAME),
      getLiveStrategies(),
      getBacktestRuns(),
      getMarkets(),
    ]);

    if (mappingsResult.status === 'rejected') {
      const err = mappingsResult.reason;
      setError(err instanceof ApiError ? err.message : '전략 라이브러리를 불러오지 못했습니다.');
      setLoading(false);
      return;
    }
    setMappings(mappingsResult.value);
    setOverview(overviewResult.status === 'fulfilled' ? overviewResult.value : []);
    setLiveStrategies(liveResult.status === 'fulfilled' ? liveResult.value : []);
    setBacktestRuns(runsResult.status === 'fulfilled' ? runsResult.value : []);
    setRunsLoadFailed(runsResult.status === 'rejected');
    setMarkets(marketsResult.status === 'fulfilled' ? marketsResult.value : []);
    setLoading(false);
  }

  // 저장/삭제 후에는 바뀐 매핑 목록만 다시 받아온다. loadAll()을 다시
  // 부르면 loading=true가 되어 20행짜리 표 전체가 "불러오는 중..."으로
  // 잠깐 사라지므로, 클릭할 때마다 그러지 않도록 이 부분만 갱신한다.
  async function refreshMappings() {
    try {
      const result = await getRegimeStrategyLibrary();
      setMappings(result);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : '매핑 목록을 다시 불러오지 못했습니다.');
    }
  }

  function handleDelete(market: string, slot: RegimeLibrarySlot) {
    const key = `${market}-${slot}`;
    if (pendingDeleteKey === key) return; // 삭제 진행 중 중복 클릭 무시
    setPendingDeleteKey(key);
    setActionError(null);
    deleteRegimeStrategyMapping(market, slot)
      .then(() => refreshMappings())
      .catch((err) => {
        setActionError(err instanceof ApiError ? err.message : '매핑 제거에 실패했습니다.');
      })
      .finally(() => setPendingDeleteKey(null));
  }

  useEffect(() => {
    loadAll();
  }, []);

  if (loading) return <p className="text-muted-foreground">불러오는 중...</p>;
  if (error) return <p className="text-destructive">{error}</p>;

  return (
    <div className="space-y-3">
      {actionError && <p className="text-sm text-destructive">{actionError}</p>}
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
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${currentLabel ? LABEL_BG_CLASS[currentLabel] : UNCLASSIFIED_BG_CLASS}`}>
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
                    const summary = mapping
                      ? run
                        ? (run.title ?? run.run_id)
                        : runsLoadFailed
                          ? mapping.source_run_id
                          : '삭제된 백테스트 결과'
                      : null;

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
                            onSelect={(runId) => upsertRegimeStrategyMapping(market, slot, runId).then(() => refreshMappings())}
                          />
                          {mapping && (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={pendingDeleteKey === `${market}-${slot}`}
                              onClick={() => handleDelete(market, slot)}
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
    </div>
  );
}
