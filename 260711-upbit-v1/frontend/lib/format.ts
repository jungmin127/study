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

// 백엔드(SQLite datetime('now'))가 타임존 마커 없는 UTC 문자열("2026-09-06 05:30:00")을
// 그대로 내려주기 때문에, new Date()에 곧바로 넘기면 브라우저가 로컬 시간으로 오인한다.
// 마커가 없으면 UTC로 명시해서 파싱한다.
function toUtcDate(iso: string): Date {
  const hasTimezone = /[Zz]|[+-]\d{2}:\d{2}$/.test(iso);
  return new Date(hasTimezone ? iso : `${iso.replace(' ', 'T')}Z`);
}

export function formatDateTime(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(toUtcDate(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

export function formatDateTimeShort(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(toUtcDate(iso));
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
  const pctText = pct > 0 && pct < 0.1 ? pct.toPrecision(2) : pct.toFixed(1);
  return `${tradeCount} / ${candleCount} (${pctText}%)`;
}
