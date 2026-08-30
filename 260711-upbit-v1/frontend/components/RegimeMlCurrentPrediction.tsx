'use client';

import { useEffect, useState } from 'react';
import type { MlCurrentPrediction, RegimeCategory } from '@/lib/types/eda';
import { ApiError } from '@/lib/api/client';
import { getRegimeMlCurrentPrediction } from '@/lib/api/eda';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InfoPopover } from '@/components/ui/info-popover';

export const TRAINED_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP',
  'KRW-SOL', 'KRW-DOGE', 'KRW-LINK', 'KRW-ADA', 'KRW-XLM', 'KRW-TRX',
  'KRW-BCH', 'KRW-BSV', 'KRW-QTUM', 'KRW-ALGO',
  'KRW-SHIB', 'KRW-SUI', 'KRW-SEI', 'KRW-NEAR', 'KRW-ETC', 'KRW-STX', 'KRW-HBAR',
];

function formatPct(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${(value * 100).toFixed(1)}%`;
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : value.toFixed(3);
}

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '하락':
      return '--regime-surge-down';
    case '하락아님':
      // 상승색(--regime-surge-up)을 쓰지 않는다 — 하락아님은 횡보+상승을 합친
      // 값이라 "상승"으로 오인하기 쉬운데, 상승색을 쓰면 그 오해를 더 부추긴다.
      return '--marker-boundary';
    default:
      return '--marker-boundary';
  }
}

interface RegimeMlCurrentPredictionProps {
  market: string;
  timeframe: string;
  /** fold별 상세 성능 옆(우측 칸)에 끼워 넣을 내용 — 재학습 관리자 패널용. */
  rightPanel?: React.ReactNode;
}

export default function RegimeMlCurrentPrediction({ market, timeframe, rightPanel }: RegimeMlCurrentPredictionProps) {
  const [data, setData] = useState<MlCurrentPrediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (timeframe !== 'minutes60' || !market || !TRAINED_MARKETS.includes(market)) {
      setData(null);
      setError(null);
      return;
    }
    let ignore = false;
    setLoading(true);
    setError(null);
    setData(null);
    getRegimeMlCurrentPrediction({ market, timeframe })
      .then((d) => {
        if (!ignore) setData(d);
      })
      .catch((err) => {
        if (!ignore) setError(err instanceof ApiError ? err.message : 'ML 예측을 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!ignore) setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [market, timeframe]);

  const modelPerformance = data?.model_performance ?? null;
  const pooled = modelPerformance?.pooled ?? null;
  const downProb = data ? (data.probs['하락'] ?? 0) : 0;
  const notDownProb = data ? (data.probs['하락아님'] ?? 0) : 0;

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">ML 현재예측</h2>
        {data && (
          <span className="text-xs text-muted-foreground">
            {market} {formatTimeframe(timeframe)}
          </span>
        )}
      </div>
      {timeframe !== 'minutes60' ? (
        <p className="text-sm text-muted-foreground">ML은 1시간봉 전용입니다.</p>
      ) : !TRAINED_MARKETS.includes(market) ? (
        <p className="text-sm text-muted-foreground">이 모델은 {TRAINED_MARKETS.join('/')}로만 학습되어 있습니다.</p>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div>
          {/* 히어로: 다음 60봉 내 하락 확률 */}
          <div className="mb-1 text-xs text-muted-foreground">다음 60봉 내 하락 확률</div>
          <div className="mb-3 flex items-baseline gap-2">
            <span
              className="text-5xl font-bold tracking-tight tabular-nums"
              style={{ color: `var(${categoryVarName('하락')})` }}
            >
              {(downProb * 100).toFixed(1)}
            </span>
            <span className="text-xl font-semibold" style={{ color: `var(${categoryVarName('하락')})` }}>
              %
            </span>
            <span className="ml-1 text-sm text-muted-foreground">
              {data.predicted_category === '하락' ? '하락 우세' : '하락아님 우세'}
            </span>
          </div>

          <div className="w-1/2">
            <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
              <div style={{ width: `${downProb * 100}%`, backgroundColor: `var(${categoryVarName('하락')})` }} />
              <div style={{ width: `${notDownProb * 100}%`, backgroundColor: `var(${categoryVarName('하락아님')})` }} />
            </div>
            <div className="mt-1 flex justify-between text-[11px]">
              <span className="font-semibold" style={{ color: `var(${categoryVarName('하락')})` }}>
                하락 {(downProb * 100).toFixed(1)}%
              </span>
              <span className="text-muted-foreground">하락아님 {(notDownProb * 100).toFixed(1)}%</span>
            </div>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
            앞으로 60시간 안에 현재 변동성 대비 큰 폭의 하락이, 큰 폭의 상승보다 먼저 나타날지를 예측합니다.
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            ※ 하락아님은 횡보·상승을 모두 포함합니다 — &ldquo;상승&rdquo;을 의미하지 않습니다.
          </p>

          {/* 모델 성능: 한 줄 4칸 */}
          <div className="mt-4 border-t pt-4">
            <div className="mb-2 flex items-center gap-1.5">
              <h3 className="text-xs font-semibold text-muted-foreground">모델 성능</h3>
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground/70">
                참고용 보조 신호
              </span>
            </div>
            {pooled ? (
              <div className="grid grid-cols-4 gap-2">
                <StatCard
                  label="macro F1"
                  value={formatScore(pooled.macro_f1)}
                  caption="두 클래스 예측력의 평균(0~1, 높을수록 좋음)"
                  info="macro F1(0~1)은 하락/하락아님 두 클래스의 F1-score 평균입니다. 1에 가까울수록 좋습니다."
                />
                <StatCard
                  label="weighted kappa"
                  value={formatScore(pooled.weighted_kappa)}
                  caption="우연히 맞을 확률을 뺀 순수 적중력(0이면 무작위와 동일)"
                  info="weighted kappa(-1~+1)는 우연히 맞을 확률을 보정한 일치도입니다. 0 이하면 무작위 추측보다도 못하다는 뜻입니다."
                />
                <StatCard
                  label="하락 precision"
                  value={formatPct(pooled.class_precision_recall['하락']?.precision)}
                  caption='"하락" 경고 중 실제로 맞은 비율'
                  info="precision은 이 카테고리로 예측했을 때 실제로 맞았던 비율입니다."
                />
                <StatCard
                  label="하락 recall"
                  value={formatPct(pooled.class_precision_recall['하락']?.recall)}
                  caption="실제 하락 중 모델이 잡아낸 비율"
                  info="recall은 실제로 이 카테고리였던 것 중 모델이 맞춘 비율입니다."
                />
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">성능 지표 없음(재학습 후 모델을 배포하면 표시됩니다)</p>
            )}
          </div>

          {/* 상세: fold별 성능+모델정보(좌) / ML 재학습 관리자 패널(우) */}
          {modelPerformance && (
            <div className="mt-4 grid grid-cols-2 gap-4 border-t pt-3">
              <div>
                <h4 className="mb-1.5 text-[11px] font-medium text-muted-foreground">fold별 상세 성능</h4>
                <div className="overflow-hidden rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-[11px]">fold</TableHead>
                        <TableHead className="text-right text-[11px]">test</TableHead>
                        <TableHead className="text-right text-[11px]">F1</TableHead>
                        <TableHead className="text-right text-[11px]">kappa</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {modelPerformance.folds.map((fold) => (
                        <TableRow
                          key={fold.fold_index}
                          className={fold.fold_index === data.model_fold_index ? 'font-semibold' : ''}
                        >
                          <TableCell className="text-[11px]">{fold.fold_index}</TableCell>
                          <TableCell className="text-right text-[11px] tabular-nums">
                            {fold.n_test.toLocaleString()}
                          </TableCell>
                          <TableCell className="text-right text-[11px] tabular-nums">
                            {formatScore(fold.macro_f1)}
                          </TableCell>
                          <TableCell className="text-right text-[11px] tabular-nums">
                            {formatScore(fold.weighted_kappa)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
                  fold 5(가장 최근까지 학습)의 모델이 실제 서빙에 쓰입니다 — 표는 다른
                  시기에도 성능이 안정적인지 보여주는 참고용입니다.
                </p>
                <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
                  학습시각 {formatDateTime(data.model_trained_at)} · 사용 fold {data.model_fold_index} ·
                  기준 봉 {formatDateTime(data.bar_time)} · 학습 마켓 {TRAINED_MARKETS.length}개
                </p>
              </div>
              <div>{rightPanel}</div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function StatCard({
  label,
  value,
  caption,
  info,
}: {
  label: string;
  value: string;
  caption: string;
  info: string;
}) {
  return (
    <div className="rounded-lg border px-3 py-2.5">
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
        {label}
        <InfoPopover>{info}</InfoPopover>
      </div>
      <div className="my-0.5 text-xl font-bold tabular-nums">{value}</div>
      <div className="text-[10.5px] leading-tight text-muted-foreground/70">{caption}</div>
    </div>
  );
}
