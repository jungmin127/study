import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

const TABS = [
  { href: '/', label: '수익률 히트맵' },
  { href: '/ranking', label: '혼합전략 랭킹' },
  { href: '/history', label: '시간대별 추이' },
  { href: '/backtests', label: '백테스트 상세' },
  { href: '/model-accuracy', label: '모델 정확도' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <header className="border-b px-6 py-3">
          <nav className="flex gap-4 text-sm">
            {TABS.map((tab) => (
              <Link key={tab.href} href={tab.href} className="hover:underline">
                {tab.label}
              </Link>
            ))}
          </nav>
        </header>
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
