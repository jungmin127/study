'use client';

import { useEffect, useState } from 'react';
import type { MlCurrentPrediction, RegimeCategory } from '@/lib/types/eda';
import { ApiError } from '@/lib/api/client';
import { getRegimeMlCurrentPrediction } from '@/lib/api/eda';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InfoPopover } from '@/components/ui/info-popover';

const CATEGORY_ORDER: RegimeCategory[] = ['상승', '횡보', '하락'];
export const TRAINED_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP',
  'KRW-SOL', 'KRW-DOGE', 'KRW-LINK', 'KRW-ADA', 'KRW-XLM', 'KRW-TRX',
  'KRW-TRUMP', 'KRW-BCH', 'KRW-BSV', 'KRW-QTUM', 'KRW-ALGO',
];

function formatPct(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : `${(value * 100).toFixed(1)}%`;
}

function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : value.toFixed(3);
}

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '상승':
      return '--regime-surge-up';
    case '횡보':
      return '--marker-boundary';
    case '하락':
      return '--regime-surge-down';
  }
}

interface RegimeMlCurrentPredictionProps {
  market: string;
  timeframe: string;
}

export default function RegimeMlCurrentPrediction({ market, timeframe }: RegimeMlCurrentPredictionProps) {
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

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">ML 현재예측</h2>
      {timeframe !== 'minutes60' ? (
        <p className="text-sm text-muted-foreground">ML은 1시간봉 전용입니다.</p>
      ) : !TRAINED_MARKETS.includes(market) ? (
        <p className="text-sm text-muted-foreground">이 모델은 {TRAINED_MARKETS.join('/')}로만 학습되어 있습니다.</p>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">불러오는 중...</p>
      ) : error ? (
        <p className="text-sm text-muted-foreground">{error}</p>
      ) : data ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            <div className="mb-3 flex items-baseline gap-2">
              <span className="text-2xl font-bold">{data.predicted_category}</span>
              <span className="text-sm text-muted-foreground">
                확신도 {(data.probs[data.predicted_category] * 100).toFixed(1)}%
              </span>
            </div>
            <div className="mb-3 space-y-1.5">
              {CATEGORY_ORDER.map((label) => (
                <div key={label} className="flex items-center gap-2 text-xs">
                  <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(data.probs[label] * 100).toFixed(1)}%`,
                        backgroundColor: `var(${categoryVarName(label)})`,
                      }}
                    />
                  </div>
                  <span className="w-10 shrink-0 text-right tabular-nums">
                    {(data.probs[label] * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {market} {formatTimeframe(timeframe)} 기준, {formatDateTime(data.bar_time)} 봉 데이터. (모델: {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index})
            </p>
          </div>
          <div className="border-t pt-3 md:border-l md:border-t-0 md:pl-6 md:pt-0">
            <h3 className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
              모델 성능
              <InfoPopover>
                macro F1(0~1)은 3개 클래스(하락/횡보/상승)의 F1-score 평균, weighted
                kappa(-1~+1)는 우연히 맞을 확률을 보정한 일치도(순서형 가중치 적용,
                하락↔상승처럼 먼 오분류에 더 큰 벌점)입니다. 둘 다 1(또는 macro
                F1=1)에 가까울수록 좋고, weighted kappa가 0 이하면 무작위 추측보다도
                못하다는 뜻입니다.
              </InfoPopover>
            </h3>
            {modelPerformance ? (
              <>
                <div className="overflow-hidden rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>fold</TableHead>
                        <TableHead className="text-right">train</TableHead>
                        <TableHead className="text-right">test</TableHead>
                        <TableHead className="text-right">macro F1</TableHead>
                        <TableHead className="text-right">weighted kappa</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {modelPerformance.folds.map((fold) => (
                        <TableRow
                          key={fold.fold_index}
                          className={fold.fold_index === data.model_fold_index ? 'font-semibold' : ''}
                        >
                          <TableCell>{fold.fold_index}</TableCell>
                          <TableCell className="text-right tabular-nums">{fold.n_train.toLocaleString()}</TableCell>
                          <TableCell className="text-right tabular-nums">{fold.n_test.toLocaleString()}</TableCell>
                          <TableCell className="text-right tabular-nums">{formatScore(fold.macro_f1)}</TableCell>
                          <TableCell className="text-right tabular-nums">{formatScore(fold.weighted_kappa)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  풀링 macro F1: {formatScore(pooled?.macro_f1)} / weighted kappa: {formatScore(pooled?.weighted_kappa)}
                </p>
                <h4 className="mt-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
                  클래스별 precision/recall(전체 fold 풀링)
                  <InfoPopover>
                    precision은 이 카테고리로 예측했을 때 실제로 맞았던 비율, recall은
                    실제로 이 카테고리였던 것 중 모델이 맞춘 비율입니다.
                  </InfoPopover>
                </h4>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  {CATEGORY_ORDER.map((label) => {
                    const pr = pooled?.class_precision_recall?.[label];
                    return (
                      <span key={label}>
                        {label} P {formatPct(pr?.precision)} / R {formatPct(pr?.recall)}
                      </span>
                    );
                  })}
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">성능 지표 없음(재학습 후 모델을 배포하면 표시됩니다)</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
