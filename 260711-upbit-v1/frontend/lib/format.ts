const KST_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

export function formatDateTime(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

export function formatDateTimeShort(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}

// 타임프레임별 1 bar당 분(minute) 수. engine/metrics.py의 _TIMEFRAME_MINUTES와 동일하게 유지한다.
const TIMEFRAME_MINUTES: Record<string, number> = {
  minutes1: 1,
  minutes3: 3,
  minutes5: 5,
  minutes15: 15,
  minutes30: 30,
  minutes60: 60,
  minutes240: 240,
  days: 1440,
};

const TIMEFRAME_LABELS: Record<string, string> = {
  minutes1: '1분',
  minutes3: '3분',
  minutes5: '5분',
  minutes15: '15분',
  minutes30: '30분',
  minutes60: '1시간',
  minutes240: '4시간',
  days: '1일',
};

export const TIMEFRAME_CODES: string[] = Object.keys(TIMEFRAME_LABELS);

export function formatTimeframe(timeframe: string): string {
  return TIMEFRAME_LABELS[timeframe] ?? timeframe;
}

export function formatHoldingPeriod(bars: number, timeframe: string): string {
  if (!timeframe.startsWith('minutes')) {
    return `${bars}봉`;
  }
  const minutesPerBar = TIMEFRAME_MINUTES[timeframe] ?? 1440;
  const days = (bars * minutesPerBar) / 1440;
  return `${bars}봉 (${days.toFixed(1)}일)`;
}

export function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export function formatCapital(digits: string): string {
  if (!digits) return '';
  return Number(digits).toLocaleString('ko-KR');
}

export function formatFrequency(tradeCount: number, candleCount: number | null | undefined): string {
  if (candleCount == null) return '-';
  const pct = candleCount > 0 ? (tradeCount / candleCount) * 100 : 0;
  return `${tradeCount} / ${candleCount} (${pct.toFixed(1)}%)`;
}
