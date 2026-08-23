import type { RegimeBacktestResult, RegimeCategory } from '@/lib/types/eda';
import { formatTimeframe } from '@/lib/format';

const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];

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

interface RegimeCurrentPredictionProps {
  result: RegimeBacktestResult;
  market: string;
  timeframe: string;
}

export default function RegimeCurrentPrediction({ result, market, timeframe }: RegimeCurrentPredictionProps) {
  const { current_prediction, n_bars, half_life_bars } = result;

  if (!current_prediction) {
    return null;
  }

  const { time, predicted_category, probs } = current_prediction;
  const daysAhead = (n_bars / half_life_bars).toFixed(1);
  const formattedTime = new Date(time).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });

  return (
    <div className="rounded-xl border p-6 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold">현재 예측</h2>
      {predicted_category === null || probs === null ? (
        <p className="text-sm text-muted-foreground">판단 불가(데이터 부족 — 워밍업 기간 이내)</p>
      ) : (
        <>
          <div className="mb-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold">{predicted_category}</span>
            <span className="text-sm text-muted-foreground">
              확신도 {(probs[predicted_category] * 100).toFixed(1)}%
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
                      width: `${(probs[label] * 100).toFixed(1)}%`,
                      backgroundColor: `var(${categoryVarName(label)})`,
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right tabular-nums">{(probs[label] * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        {market} {formatTimeframe(timeframe)} 기준, {formattedTime} 봉 데이터. 약 {n_bars}봉({daysAhead}일)
        뒤까지의 추세를 예측합니다.
      </p>
    </div>
  );
}
