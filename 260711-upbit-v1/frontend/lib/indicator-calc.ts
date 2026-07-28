/**
 * 지표 가이드 탭 전용 순수 계산 함수.
 *
 * engine/indicators/*.py가 호출하는 backtrader 내장 지표(SMA/EMA/WMA/RSI/MACD/Stochastic/
 * CCI/WilliamsR/BollingerBands/ATR/ROC100/OBV)와 같은 수식을 그대로 옮겨, 가이드 페이지의
 * 표/차트 숫자가 실제 백테스트 엔진과 같은 계산 원리로 나오게 한다. 값이 아직 정의되지 않는
 * 구간(warm-up)은 NaN으로 채운다.
 */

export function sma(values: number[], period: number): number[] {
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    return sum / period;
  });
}

export function ema(values: number[], period: number): number[] {
  const out = new Array(values.length).fill(NaN);
  if (values.length < period) return out;
  const alpha = 2 / (period + 1);
  let seed = 0;
  for (let j = 0; j < period; j++) seed += values[j];
  seed /= period;
  out[period - 1] = seed;
  for (let i = period; i < values.length; i++) {
    out[i] = values[i] * alpha + out[i - 1] * (1 - alpha);
  }
  return out;
}

export function wma(values: number[], period: number): number[] {
  const denom = (period * (period + 1)) / 2;
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let sum = 0;
    for (let w = 1; w <= period; w++) sum += values[i - period + w] * w;
    return sum / denom;
  });
}

/** Wilder(고전) 방식 RSI — backtrader 기본 RSI와 같은 평균화(SMMA)를 쓴다. */
export function rsi(closes: number[], period: number): number[] {
  const out = new Array(closes.length).fill(NaN);
  if (closes.length < period + 1) return out;
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1];
    if (change > 0) avgGain += change;
    else avgLoss += -change;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

export function macd(
  closes: number[],
  fast: number,
  slow: number,
  signalPeriod: number
): { macdLine: number[]; signalLine: number[] } {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const macdLine = closes.map((_, i) =>
    Number.isNaN(emaFast[i]) || Number.isNaN(emaSlow[i]) ? NaN : emaFast[i] - emaSlow[i]
  );
  const firstValid = macdLine.findIndex((v) => !Number.isNaN(v));
  const signalLine = new Array(closes.length).fill(NaN);
  if (firstValid === -1 || firstValid + signalPeriod > closes.length) {
    return { macdLine, signalLine };
  }
  const alpha = 2 / (signalPeriod + 1);
  let seed = 0;
  for (let j = firstValid; j < firstValid + signalPeriod; j++) seed += macdLine[j];
  seed /= signalPeriod;
  const seedIdx = firstValid + signalPeriod - 1;
  signalLine[seedIdx] = seed;
  for (let i = seedIdx + 1; i < closes.length; i++) {
    signalLine[i] = macdLine[i] * alpha + signalLine[i - 1] * (1 - alpha);
  }
  return { macdLine, signalLine };
}

export function stochastic(
  highs: number[],
  lows: number[],
  closes: number[],
  kPeriod: number,
  dPeriod: number
): { percK: number[]; percD: number[] } {
  const percK = closes.map((_, i) => {
    if (i < kPeriod - 1) return NaN;
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = i - kPeriod + 1; j <= i; j++) {
      hh = Math.max(hh, highs[j]);
      ll = Math.min(ll, lows[j]);
    }
    if (hh === ll) return 50;
    return ((closes[i] - ll) / (hh - ll)) * 100;
  });
  const percD = sma(percK, dPeriod);
  return { percK, percD };
}

export function cci(highs: number[], lows: number[], closes: number[], period: number): number[] {
  const tp = closes.map((c, i) => (highs[i] + lows[i] + c) / 3);
  const tpSma = sma(tp, period);
  return tp.map((_, i) => {
    if (i < period - 1) return NaN;
    let meanDev = 0;
    for (let j = i - period + 1; j <= i; j++) meanDev += Math.abs(tp[j] - tpSma[i]);
    meanDev /= period;
    if (meanDev === 0) return 0;
    return (tp[i] - tpSma[i]) / (0.015 * meanDev);
  });
}

export function williamsR(highs: number[], lows: number[], closes: number[], period: number): number[] {
  return closes.map((_, i) => {
    if (i < period - 1) return NaN;
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = i - period + 1; j <= i; j++) {
      hh = Math.max(hh, highs[j]);
      ll = Math.min(ll, lows[j]);
    }
    if (hh === ll) return -50;
    return ((hh - closes[i]) / (hh - ll)) * -100;
  });
}

export function bollinger(
  closes: number[],
  period: number,
  devfactor: number
): { mid: number[]; upper: number[]; lower: number[] } {
  const mid = sma(closes, period);
  const upper = new Array(closes.length).fill(NaN);
  const lower = new Array(closes.length).fill(NaN);
  for (let i = period - 1; i < closes.length; i++) {
    let variance = 0;
    for (let j = i - period + 1; j <= i; j++) variance += (closes[j] - mid[i]) ** 2;
    const std = Math.sqrt(variance / period);
    upper[i] = mid[i] + devfactor * std;
    lower[i] = mid[i] - devfactor * std;
  }
  return { mid, upper, lower };
}

/** Wilder 방식 ATR — True Range를 첫 period개는 단순평균으로 시드하고 이후는 지수 평활한다. */
export function atr(highs: number[], lows: number[], closes: number[], period: number): number[] {
  const out = new Array(closes.length).fill(NaN);
  const tr = closes.map((_, i) => {
    if (i === 0) return highs[i] - lows[i];
    return Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    );
  });
  if (tr.length < period + 1) return out;
  let seed = 0;
  for (let j = 1; j <= period; j++) seed += tr[j];
  seed /= period;
  out[period] = seed;
  for (let i = period + 1; i < tr.length; i++) {
    out[i] = (out[i - 1] * (period - 1) + tr[i]) / period;
  }
  return out;
}

export function roc100(closes: number[], period: number): number[] {
  return closes.map((_, i) => {
    if (i < period) return NaN;
    const prev = closes[i - period];
    if (prev === 0) return NaN;
    return ((closes[i] - prev) / prev) * 100;
  });
}

export function obv(closes: number[], volumes: number[]): number[] {
  const out = new Array(closes.length).fill(NaN);
  out[0] = 0;
  for (let i = 1; i < closes.length; i++) {
    if (closes[i] > closes[i - 1]) out[i] = out[i - 1] + volumes[i];
    else if (closes[i] < closes[i - 1]) out[i] = out[i - 1] - volumes[i];
    else out[i] = out[i - 1];
  }
  return out;
}

export function marketTrend(btcCloses: number[], period: number): { sma: number[]; trend: number[] } {
  const smaLine = sma(btcCloses, period);
  const trend = btcCloses.map((c, i) => (Number.isNaN(smaLine[i]) ? NaN : c - smaLine[i]));
  return { sma: smaLine, trend };
}

export function highest(values: number[], period: number): number[] {
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let max = -Infinity;
    for (let j = i - period + 1; j <= i; j++) max = Math.max(max, values[j]);
    return max;
  });
}

export function lowest(values: number[], period: number): number[] {
  return values.map((_, i) => {
    if (i < period - 1) return NaN;
    let min = Infinity;
    for (let j = i - period + 1; j <= i; j++) min = Math.min(min, values[j]);
    return min;
  });
}

export function round(value: number, digits = 2): number {
  if (Number.isNaN(value)) return NaN;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
