export interface ParsedGridResultTitle {
  buyRest: string;
  sellRest: string;
}

const GRID_PREFIX = '[Grid] ';
const BUY_SELL_PATTERN = /^매수\s+(.+?)\s+\/\s+매도\s+(.+)$/;

export function parseGridResultTitle(title: string): ParsedGridResultTitle | null {
  const withoutPrefix = title.startsWith(GRID_PREFIX) ? title.slice(GRID_PREFIX.length) : title;
  const match = withoutPrefix.match(BUY_SELL_PATTERN);
  if (!match) return null;
  return { buyRest: match[1], sellRest: match[2] };
}
