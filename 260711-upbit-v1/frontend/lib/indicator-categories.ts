import { Activity, BarChart3, Coins, DollarSign, TrendingUp, Users } from 'lucide-react';

export const CATEGORY_ORDER = ['추세', '오실레이터', '거래량', '거래대금', '손익', '시장 심리'];

export const CATEGORY_DOT_COLOR: Record<string, string> = {
  추세: 'bg-blue-500',
  오실레이터: 'bg-violet-500',
  거래량: 'bg-teal-500',
  거래대금: 'bg-amber-500',
  손익: 'bg-orange-500',
  '시장 심리': 'bg-rose-500',
};

export const CATEGORY_ICON: Record<string, typeof TrendingUp> = {
  추세: TrendingUp,
  오실레이터: Activity,
  거래량: BarChart3,
  거래대금: Coins,
  손익: DollarSign,
  '시장 심리': Users,
};
