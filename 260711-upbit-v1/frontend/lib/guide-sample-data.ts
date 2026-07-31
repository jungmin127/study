/**
 * 지표 가이드 탭 전용 합성 시세 데이터.
 *
 * bar 1~7의 close/volume/tradeValue/BTC close는 가이드 문서의 "손으로 계산해보기" 예시와
 * 정확히 일치하도록 고정된 값이다 — RSI/스토캐스틱/CCI/Williams %R/볼린저밴드/ATR/OBV/
 * 거래대금/시장추세 예시를 여기 숫자로 손 계산해서 검산했다(high=close+1, low=close-1로
 * 단순화). bar 8 이후는 차트가 자연스러운 모양을 보이도록 이어 붙인 값으로, 정확한 숫자
 * 자체보다 "지표가 가격을 따라 어떻게 움직이는지" 시각적 이해가 목적이다.
 */

export interface SampleBar {
  bar: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  tradeValue: number; // 억원 단위
}

const HAND_VERIFIED_CLOSE = [100, 102, 101, 105, 108, 107, 110];
const HAND_VERIFIED_VOLUME = [100, 80, 120, 150, 90, 60, 200];
const HAND_VERIFIED_TRADE_VALUE = [12, 8, 15, 40, 20, 9, 55];
const HAND_VERIFIED_BTC_CLOSE = [5000, 5050, 4980, 4920, 4890, 4850, 4800];

// MACD 기본값(12/26/9)이 실제로 신호선까지 그려지려면 최소 26+9=35봉이 필요하다 —
// 그 뒤로도 선이 움직이는 모습을 보여주기 위해 60봉으로 넉넉히 잡는다.
const TOTAL_BARS = 60;

function buildCloseSeries(): number[] {
  const closes = [...HAND_VERIFIED_CLOSE];
  for (let i = closes.length; i < TOTAL_BARS; i++) {
    const wave = 8 * Math.sin((2 * Math.PI * i) / 11) + 3 * Math.sin((2 * Math.PI * i) / 4);
    const drift = (i - 6) * 0.6;
    closes.push(Math.round(106 + drift + wave));
  }
  return closes;
}

function buildVolumeSeries(): number[] {
  const volumes = [...HAND_VERIFIED_VOLUME];
  for (let i = volumes.length; i < TOTAL_BARS; i++) {
    const spike = i % 7 === 0 ? 90 : 0;
    volumes.push(70 + Math.round(60 * Math.abs(Math.sin(i * 1.3))) + spike);
  }
  return volumes;
}

function buildTradeValueSeries(): number[] {
  const values = [...HAND_VERIFIED_TRADE_VALUE];
  for (let i = values.length; i < TOTAL_BARS; i++) {
    const bigMoneyDay = i % 6 === 0 ? 45 : 0;
    values.push(10 + Math.round(8 * Math.abs(Math.sin(i * 0.9))) + bigMoneyDay);
  }
  return values;
}

function buildBtcCloseSeries(): number[] {
  const values = [...HAND_VERIFIED_BTC_CLOSE];
  for (let i = values.length; i < TOTAL_BARS; i++) {
    values.push(Math.round(4800 - (i - 6) * 15 + 40 * Math.sin(i / 3)));
  }
  return values;
}

function buildFearGreedSeries(): number[] {
  const values: number[] = [];
  for (let i = 0; i < TOTAL_BARS; i++) {
    const wave = 35 * Math.sin((2 * Math.PI * i) / 20) + 15 * Math.sin((2 * Math.PI * i) / 7);
    values.push(Math.max(0, Math.min(100, Math.round(50 + wave))));
  }
  return values;
}

function buildKoreaPremiumSeries(): number[] {
  const values: number[] = [];
  for (let i = 0; i < TOTAL_BARS; i++) {
    const wave = 3 * Math.sin((2 * Math.PI * i) / 18) + 2 * Math.sin((2 * Math.PI * i) / 6);
    values.push(Math.round((2.5 + wave) * 100) / 100);
  }
  return values;
}

function buildVpinSeries(): number[] {
  const values: number[] = [];
  for (let i = 0; i < TOTAL_BARS; i++) {
    const wave = 0.1 * Math.sin((2 * Math.PI * i) / 15) + 0.05 * Math.sin((2 * Math.PI * i) / 5);
    values.push(Math.max(0, Math.min(1, Math.round((0.46 + wave) * 100) / 100)));
  }
  return values;
}

const closeSeries = buildCloseSeries();
const volumeSeries = buildVolumeSeries();
const tradeValueSeries = buildTradeValueSeries();
const btcCloseSeries = buildBtcCloseSeries();
const fearGreedSeries = buildFearGreedSeries();
const koreaPremiumSeries = buildKoreaPremiumSeries();
const vpinSeries = buildVpinSeries();

export const SAMPLE_BARS: SampleBar[] = closeSeries.map((close, i) => ({
  bar: i + 1,
  close,
  high: close + 1,
  low: close - 1,
  volume: volumeSeries[i],
  tradeValue: tradeValueSeries[i],
}));

export const SAMPLE_BTC: { bar: number; close: number }[] = btcCloseSeries.map((close, i) => ({
  bar: i + 1,
  close,
}));

/** 공포탐욕지수는 코인 캔들과 무관한 고정 시계열이라 SAMPLE_BARS의 bar 인덱스에 맞춰 별도 배열로 둔다. */
export const SAMPLE_FEAR_GREED: number[] = fearGreedSeries;

/** 한국프리미엄은 코인 캔들과 무관한 고정 시계열이라 SAMPLE_BARS의 bar 인덱스에 맞춰 별도 배열로 둔다. */
export const SAMPLE_KOREA_PREMIUM: number[] = koreaPremiumSeries;

/** 가이드 문서 본문에서 "손으로 계산" 설명에 쓰는, 정확히 검산된 앞 7개 봉만 뽑은 뷰. */
export const HAND_VERIFIED_BAR_COUNT = HAND_VERIFIED_CLOSE.length;

/** VPIN은 코인 캔들과 무관한 고정 시계열이라 SAMPLE_BARS의 bar 인덱스에 맞춰 별도 배열로 둔다. */
export const SAMPLE_VPIN: number[] = vpinSeries;
