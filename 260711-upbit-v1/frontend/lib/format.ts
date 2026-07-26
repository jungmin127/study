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

// 타임프레임별 1 bar당 분(minute) 수. engine/metrics.py의 _TIMEFRAME_MINUTES와 동일하게 유지한다.
const TIMEFRAME_MINUTES: Record<string, number> = {
  minutes1: 1,
  minutes5: 5,
  minutes15: 15,
  minutes30: 30,
  minutes60: 60,
  minutes240: 240,
  days: 1440,
};

export function formatHoldingPeriod(bars: number, timeframe: string): string {
  if (!timeframe.startsWith('minutes')) {
    return `${bars}봉`;
  }
  const minutesPerBar = TIMEFRAME_MINUTES[timeframe] ?? 1440;
  const days = (bars * minutesPerBar) / 1440;
  return `${bars}봉 (${days.toFixed(1)}일)`;
}
