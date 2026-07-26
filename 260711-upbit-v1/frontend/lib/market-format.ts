export function changeColorClass(rate: number | null): string {
  if (!rate) return 'text-foreground';
  return rate > 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}

export function formatPrice(price: number | null): string {
  if (price === null) return '-';
  if (price === 0) return '0';
  if (price >= 100) return Math.round(price).toLocaleString('ko-KR');
  const magnitude = Math.floor(Math.log10(Math.abs(price)));
  const decimals = Math.max(0, 2 - magnitude);
  return price.toLocaleString('ko-KR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function formatChangeRate(rate: number | null): string {
  if (rate === null) return '-';
  return `${(Math.abs(rate) * 100).toFixed(2)}%`;
}

export function formatChangePrice(price: number | null): string {
  if (price === null) return '-';
  return formatPrice(Math.abs(price));
}

export function formatTradePrice24h(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value / 1_000_000).toLocaleString('ko-KR')}백만`;
}
