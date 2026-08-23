import { Activity, BarChart3, Coins, DollarSign, Ruler, TrendingUp, Users } from 'lucide-react';

export const CATEGORY_ORDER = ['추세', '오실레이터', '거래량', '거래대금', '가격대', '손익', '시장 심리'];

// 그리드서치 지표 풀 선택에 쓰는 카테고리 목록 — "손익"은 토글 대상이 아니라 항상 매도
// 조건에 포함되므로 제외한다(engine/grid_search_pool.py의 INDICATOR_POOL_SPECS와 동일 범위).
export const GRID_SEARCH_POOL_CATEGORIES = CATEGORY_ORDER.filter((c) => c !== '손익');

export const CATEGORY_DOT_COLOR: Record<string, string> = {
  추세: 'bg-blue-500',
  오실레이터: 'bg-violet-500',
  거래량: 'bg-teal-500',
  거래대금: 'bg-amber-500',
  가격대: 'bg-cyan-500',
  손익: 'bg-orange-500',
  '시장 심리': 'bg-rose-500',
};

export const CATEGORY_ICON: Record<string, typeof TrendingUp> = {
  추세: TrendingUp,
  오실레이터: Activity,
  거래량: BarChart3,
  거래대금: Coins,
  가격대: Ruler,
  손익: DollarSign,
  '시장 심리': Users,
};
