export function returnRateColor(rate: number | null): string {
  if (rate === null || rate === 0) return '';
  return rate > 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}
