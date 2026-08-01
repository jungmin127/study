import { SAMPLE_BARS, SAMPLE_BTC, SAMPLE_FEAR_GREED, SAMPLE_FUNDING_RATE, SAMPLE_KOREA_PREMIUM, SAMPLE_VPIN, type SampleBar } from '@/lib/guide-sample-data';
import * as calc from '@/lib/indicator-calc';

export interface TableColumn {
  key: string;
  label: string;
}

export interface TableRow {
  bar: number;
  cells: Record<string, string>;
}

export interface LineSpec {
  key: string;
  name: string;
  color: string;
  dash?: boolean;
}

export type ChartConfig =
  | {
      type: 'line';
      data: Array<Record<string, number | undefined>>;
      lines?: LineSpec[];
      bars?: LineSpec[];
      refLines?: { y: number; label?: string }[];
    }
  | {
      type: 'gauge';
      min: number;
      max: number;
      zones: { from: number; to: number; color: string; label: string }[];
      value: number;
      valueLabel: string;
    }
  | { type: 'none' };

export interface GuideExample {
  columns: TableColumn[];
  rows: TableRow[];
  chart: ChartConfig;
}

const n = (v: number, digits = 2) => (Number.isNaN(v) ? '-' : calc.round(v, digits).toLocaleString('ko-KR'));
const clean = (v: number): number | undefined => (Number.isNaN(v) ? undefined : v);

function firstValidIndex(arr: number[]): number {
  return arr.findIndex((v) => !Number.isNaN(v));
}

function windowFrom(startIdx: number, count = 6): SampleBar[] {
  return SAMPLE_BARS.slice(startIdx, startIdx + count);
}

const closes = SAMPLE_BARS.map((b) => b.close);
const highs = SAMPLE_BARS.map((b) => b.high);
const lows = SAMPLE_BARS.map((b) => b.low);
const volumes = SAMPLE_BARS.map((b) => b.volume);
const tradeValues = SAMPLE_BARS.map((b) => b.tradeValue);
const btcCloses = SAMPLE_BTC.map((b) => b.close);

function overlayLineExample(
  maValues: number[],
  maLabel: string,
  maKey: string,
  color: string,
  period: number
): GuideExample {
  const start = firstValidIndex(maValues);
  const rows = windowFrom(start).map((bar, i) => ({
    bar: bar.bar,
    cells: { close: n(bar.close, 0), [maKey]: n(maValues[start + i]) },
  }));
  return {
    columns: [
      { key: 'close', label: '종가' },
      { key: maKey, label: maLabel },
    ],
    rows,
    chart: {
      type: 'line',
      data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, [maKey]: clean(maValues[i]) })),
      lines: [
        { key: 'close', name: '종가', color: '#94a3b8' },
        { key: maKey, name: `${maLabel} (period=${period})`, color },
      ],
    },
  };
}

function gaugeExample(
  values: number[],
  min: number,
  max: number,
  zones: { from: number; to: number; color: string; label: string }[],
  valueLabel: string
): { chart: ChartConfig } {
  const last = values[values.length - 1];
  return {
    chart: { type: 'gauge', min, max, zones, value: calc.round(last), valueLabel },
  };
}

export function buildGuideExample(value: string): GuideExample {
  switch (value) {
    case 'SMA': {
      const period = 14;
      const line = calc.sma(closes, period);
      return overlayLineExample(line, 'SMA', 'sma', '#3b82f6', period);
    }
    case 'EMA': {
      const period = 14;
      const line = calc.ema(closes, period);
      return overlayLineExample(line, 'EMA', 'ema', '#3b82f6', period);
    }
    case 'WMA': {
      const period = 14;
      const line = calc.wma(closes, period);
      return overlayLineExample(line, 'WMA', 'wma', '#3b82f6', period);
    }
    case 'RSI': {
      const period = 14;
      const line = calc.rsi(closes, period);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        const change = SAMPLE_BARS[idx].close - SAMPLE_BARS[idx - 1].close;
        return {
          bar: bar.bar,
          cells: { close: n(bar.close, 0), change: (change >= 0 ? '+' : '') + n(change, 0), rsi: n(line[idx]) },
        };
      });
      const gauge = gaugeExample(
        line.filter((v) => !Number.isNaN(v)),
        0,
        100,
        [
          { from: 0, to: 30, color: '#10b981', label: '과매도(<30)' },
          { from: 30, to: 70, color: '#94a3b8', label: '중립' },
          { from: 70, to: 100, color: '#ef4444', label: '과매수(>70)' },
        ],
        'RSI'
      );
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'change', label: '전봉 대비 변화' },
          { key: 'rsi', label: 'RSI' },
        ],
        rows,
        chart: gauge.chart,
      };
    }
    case 'MACD_line':
    case 'MACD_signal': {
      const fast = 12;
      const slow = 26;
      const signal = 9;
      const { macdLine, signalLine } = calc.macd(closes, fast, slow, signal);
      const start = firstValidIndex(signalLine);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return {
          bar: bar.bar,
          cells: { close: n(bar.close, 0), macd: n(macdLine[idx]), signal: n(signalLine[idx]) },
        };
      });
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'macd', label: 'MACD Line' },
          { key: 'signal', label: 'MACD Signal' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({
            bar: bar.bar,
            macd: clean(macdLine[i]),
            signal: clean(signalLine[i]),
          })),
          lines: [
            { key: 'macd', name: `MACD Line (${fast}/${slow})`, color: '#3b82f6' },
            { key: 'signal', name: `MACD Signal (${signal})`, color: '#f59e0b' },
          ],
          refLines: [{ y: 0, label: '0선' }],
        },
      };
    }
    case 'STOCH_K':
    case 'STOCH_D': {
      const kPeriod = 14;
      const dPeriod = 3;
      const { percK, percD } = calc.stochastic(highs, lows, closes, kPeriod, dPeriod);
      const start = firstValidIndex(percD);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return {
          bar: bar.bar,
          cells: { high: n(bar.high, 0), low: n(bar.low, 0), close: n(bar.close, 0), k: n(percK[idx]), d: n(percD[idx]) },
        };
      });
      const gauge = gaugeExample(
        (value === 'STOCH_K' ? percK : percD).filter((v) => !Number.isNaN(v)),
        0,
        100,
        [
          { from: 0, to: 20, color: '#10b981', label: '과매도(<20)' },
          { from: 20, to: 80, color: '#94a3b8', label: '중립' },
          { from: 80, to: 100, color: '#ef4444', label: '과매수(>80)' },
        ],
        value === 'STOCH_K' ? '%K' : '%D'
      );
      return {
        columns: [
          { key: 'high', label: '고가' },
          { key: 'low', label: '저가' },
          { key: 'close', label: '종가' },
          { key: 'k', label: '%K' },
          { key: 'd', label: '%D' },
        ],
        rows,
        chart: gauge.chart,
      };
    }
    case 'FIB_382':
    case 'FIB_500':
    case 'FIB_618': {
      const period = 20;
      const ratio = value === 'FIB_382' ? 0.382 : value === 'FIB_500' ? 0.5 : 0.618;
      const hh = calc.highest(highs, period);
      const ll = calc.lowest(lows, period);
      const fib = closes.map((_, i) => (Number.isNaN(hh[i]) ? NaN : hh[i] - (hh[i] - ll[i]) * ratio));
      const start = firstValidIndex(fib);
      const rows = windowFrom(start).map((bar, i) => ({
        bar: bar.bar,
        cells: { close: n(bar.close, 0), high: n(hh[start + i], 0), low: n(ll[start + i], 0), fib: n(fib[start + i]) },
      }));
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'high', label: `${period}봉 최고가` },
          { key: 'low', label: `${period}봉 최저가` },
          { key: 'fib', label: '되돌림 가격' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, fib: clean(fib[i]) })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'fib', name: `${value}`, color: '#0891b2', dash: true },
          ],
        },
      };
    }
    case 'PIVOT_P':
    case 'PIVOT_R1':
    case 'PIVOT_S1': {
      const p = SAMPLE_BARS.map((_, i) => (i === 0 ? NaN : (SAMPLE_BARS[i - 1].high + SAMPLE_BARS[i - 1].low + SAMPLE_BARS[i - 1].close) / 3));
      const r1 = SAMPLE_BARS.map((bar, i) => (i === 0 ? NaN : p[i] * 2 - SAMPLE_BARS[i - 1].low));
      const s1 = SAMPLE_BARS.map((bar, i) => (i === 0 ? NaN : p[i] * 2 - SAMPLE_BARS[i - 1].high));
      const line = value === 'PIVOT_P' ? p : value === 'PIVOT_R1' ? r1 : s1;
      const rows = windowFrom(1, 6).map((bar, i) => {
        const idx = i + 1;
        return {
          bar: bar.bar,
          cells: {
            prevHigh: n(SAMPLE_BARS[idx - 1].high, 0),
            prevLow: n(SAMPLE_BARS[idx - 1].low, 0),
            prevClose: n(SAMPLE_BARS[idx - 1].close, 0),
            value: n(line[idx]),
          },
        };
      });
      return {
        columns: [
          { key: 'prevHigh', label: '직전 봉 고가' },
          { key: 'prevLow', label: '직전 봉 저가' },
          { key: 'prevClose', label: '직전 봉 종가' },
          { key: 'value', label: value },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, value: clean(line[i]) })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'value', name: value, color: '#0891b2', dash: true },
          ],
        },
      };
    }
    case 'VPVR_POC':
    case 'VPVR_VAH':
    case 'VPVR_VAL': {
      const period = 50;
      const { poc, vah, val } = calc.volumeProfile(highs, lows, volumes, period);
      const line = value === 'VPVR_POC' ? poc : value === 'VPVR_VAH' ? vah : val;
      const start = firstValidIndex(line);
      const rows = windowFrom(start, 6).map((bar, i) => ({
        bar: bar.bar,
        cells: { close: n(bar.close, 0), value: n(line[start + i]) },
      }));
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'value', label: value },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, close: bar.close, value: clean(line[i]) })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'value', name: `${value}`, color: '#0891b2', dash: true },
          ],
        },
      };
    }
    case 'CCI': {
      const period = 20;
      const line = calc.cci(highs, lows, closes, period);
      const tp = closes.map((c, i) => (highs[i] + lows[i] + c) / 3);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return {
          bar: bar.bar,
          cells: { tp: n(tp[idx]), cci: n(line[idx]) },
        };
      });
      const gauge = gaugeExample(
        line.filter((v) => !Number.isNaN(v)),
        -200,
        200,
        [
          { from: -200, to: -100, color: '#10b981', label: '과매도(<-100)' },
          { from: -100, to: 100, color: '#94a3b8', label: '중립' },
          { from: 100, to: 200, color: '#ef4444', label: '과매수(>100)' },
        ],
        'CCI'
      );
      return {
        columns: [
          { key: 'tp', label: '전형가(고+저+종/3)' },
          { key: 'cci', label: 'CCI' },
        ],
        rows,
        chart: gauge.chart,
      };
    }
    case 'WILLIAMS_R': {
      const period = 14;
      const line = calc.williamsR(highs, lows, closes, period);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return { bar: bar.bar, cells: { high: n(bar.high, 0), low: n(bar.low, 0), close: n(bar.close, 0), r: n(line[idx]) } };
      });
      const gauge = gaugeExample(
        line.filter((v) => !Number.isNaN(v)),
        -100,
        0,
        [
          { from: -100, to: -80, color: '#10b981', label: '과매도(<-80)' },
          { from: -80, to: -20, color: '#94a3b8', label: '중립' },
          { from: -20, to: 0, color: '#ef4444', label: '과매수(>-20)' },
        ],
        '%R'
      );
      return {
        columns: [
          { key: 'high', label: '고가' },
          { key: 'low', label: '저가' },
          { key: 'close', label: '종가' },
          { key: 'r', label: 'Williams %R' },
        ],
        rows,
        chart: gauge.chart,
      };
    }
    case 'BB_upper':
    case 'BB_lower':
    case 'BB_middle': {
      const period = 20;
      const { mid, upper, lower } = calc.bollinger(closes, period, 2.0);
      const start = firstValidIndex(mid);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return {
          bar: bar.bar,
          cells: { close: n(bar.close, 0), upper: n(upper[idx]), mid: n(mid[idx]), lower: n(lower[idx]) },
        };
      });
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'upper', label: '상단' },
          { key: 'mid', label: '중간선' },
          { key: 'lower', label: '하단' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({
            bar: bar.bar,
            close: bar.close,
            upper: clean(upper[i]),
            mid: clean(mid[i]),
            lower: clean(lower[i]),
          })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'upper', name: '상단(+2σ)', color: '#ef4444', dash: true },
            { key: 'mid', name: `중간선 SMA(${period})`, color: '#3b82f6' },
            { key: 'lower', name: '하단(-2σ)', color: '#10b981', dash: true },
          ],
        },
      };
    }
    case 'ATR': {
      const period = 14;
      const line = calc.atr(highs, lows, closes, period);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return { bar: bar.bar, cells: { close: n(bar.close, 0), atr: n(line[idx]), breakout: n(bar.close + 2 * line[idx], 0) } };
      });
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'atr', label: 'ATR' },
          { key: 'breakout', label: '종가+ATR×2 (변동성 돌파 기준가 예시)' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({
            bar: bar.bar,
            close: bar.close,
            breakout: clean(Number.isNaN(line[i]) ? NaN : bar.close + 2 * line[i]),
          })),
          lines: [
            { key: 'close', name: '종가', color: '#94a3b8' },
            { key: 'breakout', name: '종가+ATR×2', color: '#ef4444', dash: true },
          ],
        },
      };
    }
    case 'OBV': {
      const line = calc.obv(closes, volumes);
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: {
          close: n(bar.close, 0),
          change: i === 0 ? '-' : (bar.close - SAMPLE_BARS[i - 1].close >= 0 ? '+' : '') + n(bar.close - SAMPLE_BARS[i - 1].close, 0),
          volume: n(bar.volume, 0),
          obv: n(line[i], 0),
        },
      }));
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'change', label: '전봉 대비' },
          { key: 'volume', label: '거래량' },
          { key: 'obv', label: 'OBV(누적)' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, obv: clean(line[i]) })),
          lines: [{ key: 'obv', name: 'OBV', color: '#0d9488' }],
          refLines: [{ y: 0, label: '0선' }],
        },
      };
    }
    case 'VOLUME_SMA': {
      const period = 20;
      const line = calc.sma(volumes, period);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => ({
        bar: bar.bar,
        cells: { volume: n(bar.volume, 0), volumeSma: n(line[start + i]) },
      }));
      return {
        columns: [
          { key: 'volume', label: '거래량' },
          { key: 'volumeSma', label: `거래량 SMA(${period})` },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, volume: bar.volume, volumeSma: clean(line[i]) })),
          bars: [{ key: 'volume', name: '거래량', color: '#5eead4' }],
          lines: [{ key: 'volumeSma', name: `거래량 SMA(${period})`, color: '#0d9488' }],
        },
      };
    }
    case 'TRADE_VALUE': {
      const rows = windowFrom(0, 7).map((bar) => ({
        bar: bar.bar,
        cells: { tradeValue: `${n(bar.tradeValue, 0)}억원`, volume: n(bar.volume, 0) },
      }));
      return {
        columns: [
          { key: 'tradeValue', label: '거래대금' },
          { key: 'volume', label: '거래량(참고)' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar) => ({ bar: bar.bar, tradeValue: bar.tradeValue })),
          lines: [],
          bars: [{ key: 'tradeValue', name: '거래대금(억원)', color: '#fbbf24' }],
        },
      };
    }
    case 'TRADE_VALUE_SMA': {
      const period = 20;
      const line = calc.sma(tradeValues, period);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => ({
        bar: bar.bar,
        cells: { tradeValue: `${n(bar.tradeValue, 0)}억원`, tradeValueSma: `${n(line[start + i])}억원` },
      }));
      return {
        columns: [
          { key: 'tradeValue', label: '거래대금' },
          { key: 'tradeValueSma', label: `거래대금 SMA(${period})` },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, tradeValue: bar.tradeValue, tradeValueSma: clean(line[i]) })),
          bars: [{ key: 'tradeValue', name: '거래대금(억원)', color: '#fbbf24' }],
          lines: [{ key: 'tradeValueSma', name: `거래대금 SMA(${period})`, color: '#b45309' }],
        },
      };
    }
    case 'MARKET_TREND': {
      const period = 10;
      const { sma: btcSma, trend } = calc.marketTrend(btcCloses, period);
      const start = firstValidIndex(trend);
      const rows = SAMPLE_BTC.slice(start, start + 6).map((bar, i) => {
        const idx = start + i;
        return {
          bar: bar.bar,
          cells: { btcClose: n(bar.close, 0), btcSma: n(btcSma[idx], 0), trend: n(trend[idx], 0) },
        };
      });
      return {
        columns: [
          { key: 'btcClose', label: 'KRW-BTC 종가' },
          { key: 'btcSma', label: `BTC SMA(${period})` },
          { key: 'trend', label: '시장 추세(=종가-SMA)' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BTC.map((bar, i) => ({ bar: bar.bar, trend: clean(trend[i]) })),
          lines: [{ key: 'trend', name: `KRW-BTC 종가 − SMA(${period})`, color: '#e11d48' }],
          refLines: [{ y: 0, label: '0선(하락 추세 경계)' }],
        },
      };
    }
    case 'BTC_CORRELATION':
    case 'USDT_CORRELATION': {
      const period = 10;
      const coinRoc = closes.map((c, i) => (i === 0 ? NaN : ((c - closes[i - 1]) / closes[i - 1]) * 100));
      const auxCloses = SAMPLE_BTC.map((b) => b.close);
      const auxRoc = auxCloses.map((c, i) => (i === 0 ? NaN : ((c - auxCloses[i - 1]) / auxCloses[i - 1]) * 100));
      const corr = closes.map((_, i) => {
        if (i < period) return NaN;
        const xs = coinRoc.slice(i - period + 1, i + 1);
        const ys = auxRoc.slice(i - period + 1, i + 1);
        const meanX = xs.reduce((a, b) => a + b, 0) / period;
        const meanY = ys.reduce((a, b) => a + b, 0) / period;
        const cov = xs.reduce((sum, x, j) => sum + (x - meanX) * (ys[j] - meanY), 0);
        const stdX = Math.sqrt(xs.reduce((sum, x) => sum + (x - meanX) ** 2, 0));
        const stdY = Math.sqrt(ys.reduce((sum, y) => sum + (y - meanY) ** 2, 0));
        return stdX === 0 || stdY === 0 ? 0 : cov / (stdX * stdY);
      });
      const start = firstValidIndex(corr);
      const rows = windowFrom(start).map((bar, i) => ({
        bar: bar.bar,
        cells: { close: n(bar.close, 0), aux: n(auxCloses[start + i], 0), corr: n(corr[start + i]) },
      }));
      const label = value === 'BTC_CORRELATION' ? 'KRW-BTC' : 'KRW-USDT';
      return {
        columns: [
          { key: 'close', label: '종가' },
          { key: 'aux', label: `${label} 종가` },
          { key: 'corr', label: '상관계수' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, corr: clean(corr[i]) })),
          lines: [{ key: 'corr', name: `${label} 상관계수 (period=${period})`, color: '#e11d48' }],
          refLines: [{ y: 0, label: '0선' }],
        },
      };
    }
    case 'MOMENTUM_PCT': {
      const period = 5;
      const line = calc.roc100(closes, period);
      const start = firstValidIndex(line);
      const rows = windowFrom(start).map((bar, i) => {
        const idx = start + i;
        return {
          bar: bar.bar,
          cells: { prevClose: n(SAMPLE_BARS[idx - period].close, 0), close: n(bar.close, 0), momentum: `${n(line[idx])}%` },
        };
      });
      return {
        columns: [
          { key: 'prevClose', label: `${period}봉 전 종가` },
          { key: 'close', label: '현재 종가' },
          { key: 'momentum', label: '모멘텀(%)' },
        ],
        rows,
        chart: {
          type: 'line',
          data: SAMPLE_BARS.map((bar, i) => ({ bar: bar.bar, momentum: clean(line[i]) })),
          lines: [{ key: 'momentum', name: `모멘텀 %(period=${period})`, color: '#8b5cf6' }],
          refLines: [{ y: 0, label: '0선' }],
        },
      };
    }
    case 'FEAR_GREED_CMC': {
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { fearGreed: n(SAMPLE_FEAR_GREED[i], 0) },
      }));
      const gauge = gaugeExample(
        SAMPLE_FEAR_GREED,
        0,
        100,
        [
          { from: 0, to: 20, color: '#10b981', label: '공포(<20)' },
          { from: 20, to: 80, color: '#94a3b8', label: '중립' },
          { from: 80, to: 100, color: '#ef4444', label: '탐욕(>80)' },
        ],
        '공포탐욕지수'
      );
      return {
        columns: [{ key: 'fearGreed', label: '공포탐욕지수' }],
        rows,
        chart: gauge.chart,
      };
    }
    case 'KOREA_PREMIUM': {
      const start = 12;
      const rows = windowFrom(start, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { premium: n(SAMPLE_KOREA_PREMIUM[start + i]) },
      }));
      const gauge = gaugeExample(
        SAMPLE_KOREA_PREMIUM,
        -10,
        15,
        [
          { from: -10, to: 0, color: '#3b82f6', label: '역프리미엄(<0%)' },
          { from: 0, to: 5, color: '#94a3b8', label: '중립' },
          { from: 5, to: 15, color: '#ef4444', label: '과열(>5%)' },
        ],
        '한국프리미엄'
      );
      return {
        columns: [{ key: 'premium', label: '한국프리미엄(%)' }],
        rows,
        chart: gauge.chart,
      };
    }
    case 'VPIN': {
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { vpin: n(SAMPLE_VPIN[i]) },
      }));
      const gauge = gaugeExample(
        SAMPLE_VPIN,
        0,
        1,
        [
          { from: 0, to: 0.35, color: '#94a3b8', label: '평온(<0.35)' },
          { from: 0.35, to: 0.55, color: '#f59e0b', label: '주의' },
          { from: 0.55, to: 1, color: '#ef4444', label: '독성 흐름(>0.55)' },
        ],
        'VPIN'
      );
      return {
        columns: [{ key: 'vpin', label: 'VPIN' }],
        rows,
        chart: gauge.chart,
      };
    }
    case 'FUNDING_RATE': {
      const rows = windowFrom(0, 7).map((bar, i) => ({
        bar: bar.bar,
        cells: { fundingRate: n(SAMPLE_FUNDING_RATE[i]) },
      }));
      const gauge = gaugeExample(
        SAMPLE_FUNDING_RATE,
        -0.05,
        0.08,
        [
          { from: -0.05, to: -0.03, color: '#3b82f6', label: '숏 과열(<-0.03%)' },
          { from: -0.03, to: 0.05, color: '#94a3b8', label: '중립' },
          { from: 0.05, to: 0.08, color: '#ef4444', label: '롱 과열(>0.05%)' },
        ],
        '펀딩비'
      );
      return {
        columns: [{ key: 'fundingRate', label: '펀딩비(%)' }],
        rows,
        chart: gauge.chart,
      };
    }
    case 'STOP_LOSS_PCT':
    case 'TAKE_PROFIT_PCT':
    case 'HOLDING_PERIOD_BARS': {
      const entry = 100000;
      const path = [100000, 101500, 99200, 96800, 94500];
      const rows = path.map((price, i) => ({
        bar: i,
        cells: {
          price: n(price, 0),
          returnPct: `${(((price - entry) / entry) * 100 >= 0 ? '+' : '') + n(((price - entry) / entry) * 100)}%`,
          bars: String(i),
        },
      }));
      return {
        columns: [
          { key: 'bars', label: '진입 후 지난 봉 수' },
          { key: 'price', label: '현재가' },
          { key: 'returnPct', label: '진입가 대비 수익률' },
        ],
        rows,
        chart: { type: 'none' },
      };
    }
    default:
      return { columns: [], rows: [], chart: { type: 'none' } };
  }
}
