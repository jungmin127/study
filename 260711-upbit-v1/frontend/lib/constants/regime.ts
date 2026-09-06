import type { RegimeAdxLabel } from '@/lib/types/eda';

export const TIMEFRAME = 'minutes60';

// /regime 오버뷰(RegimeAdxOverview.tsx)와 /strategy-library
// (RegimeStrategyLibraryPage.tsx)가 장세 라벨 배지 색상을 동일하게
// 쓰도록 공유하는 상수. 한쪽만 고쳐서 두 탭의 색이 어긋나는 일을 막는다.
export const REGIME_LABEL_BG_CLASS: Record<RegimeAdxLabel, string> = {
  상승: 'bg-[color:var(--regime-surge-up)]/15 border-[color:var(--regime-surge-up)]/40',
  하락: 'bg-[color:var(--regime-surge-down)]/15 border-[color:var(--regime-surge-down)]/40',
  횡보: 'bg-[color:var(--marker-boundary)]/15 border-[color:var(--marker-boundary)]/40',
};

export const REGIME_UNCLASSIFIED_BG_CLASS =
  'bg-[color:var(--trend-unclassified)]/15 border-[color:var(--trend-unclassified)]/40';

export const MAJOR_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-ADA', 'KRW-DOGE',
  'KRW-LINK', 'KRW-DOT', 'KRW-AVAX', 'KRW-TRX', 'KRW-POL', 'KRW-BCH',
  'KRW-ETC', 'KRW-XLM', 'KRW-ATOM', 'KRW-UNI', 'KRW-NEAR', 'KRW-ICP',
  'KRW-HBAR', 'KRW-SUI',
] as const;
