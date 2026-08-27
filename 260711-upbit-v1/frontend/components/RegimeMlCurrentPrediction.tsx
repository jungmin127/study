'use client';

import { useEffect, useState } from 'react';
import type { MlCurrentPrediction, RegimeCategory } from '@/lib/types/eda';
import { ApiError } from '@/lib/api/client';
import { getRegimeMlCurrentPrediction } from '@/lib/api/eda';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { InfoPopover } from '@/components/ui/info-popover';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];
export const TRAINED_MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'];

function formatPct(value: number | null): string {
  return value === null ? '-' : `${(value * 100).toFixed(1)}%`;
}

function formatCorrelation(value: number | null): string {
  return value === null ? '-' : value.toFixed(3);
}

function categoryVarName(label: RegimeCategory): string {
  switch (label) {
    case '급상승':
      return '--regime-surge-up';
    case '완만상승':
      return '--regime-mild-up';
    case '횡보':
      return '--marker-boundary';
    case '완만하락':
      return '--regime-mild-down';
    case '급하락':
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
        <>
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
          <div className="mt-4 border-t pt-3">
            <h3 className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
              모델 성능
              <InfoPopover>
                피어슨 상관계수(-1 ~ +1). 시점마다 예측 확률벡터를 카테고리별 기준점수(급상승 +0.35
                ~ 급하락 -0.35)로 가중평균한 "기댓값"과, 이후 n봉 동안의 실현수익률을 변동성으로
                정규화한 값 사이의 선형 상관관계입니다. +1에 가까울수록 예측 방향과 실제 방향이
                강하게 같이 움직이고, -1에 가까울수록 반대로 움직이며, 0에 가까우면 확률벡터에
                예측력이 거의 없다는 뜻입니다. 아래 hit-rate(예측 카테고리별 적중률)와 달리, 확률분포
                전체(강도 포함)를 반영하는 지표입니다.
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
                        <TableHead className="text-right">상관계수</TableHead>
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
                          <TableCell className="text-right tabular-nums">{formatCorrelation(fold.correlation)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  풀링 상관계수: {formatCorrelation(modelPerformance.pooled_correlation)}
                </p>
                <h4 className="mt-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
                  카테고리별 hit-rate(전체 fold 합산)
                  <InfoPopover>
                    각 카테고리로 예측했을 때 실제로 그 카테고리가 맞았던 비율(적중건수/예측건수,
                    전체 fold 합산 기준)입니다. 위 확신도·상관계수와 달리 예측이 맞았는지 여부만
                    보는 단순 지표입니다.
                  </InfoPopover>
                </h4>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  {CATEGORY_ORDER.map((label) => (
                    <span key={label}>
                      {label} {formatPct(modelPerformance.pooled_hit_rate[label])}
                    </span>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">성능 지표 없음(재학습 후 모델을 배포하면 표시됩니다)</p>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
