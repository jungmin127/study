import type { Metadata } from 'next';
import NavTabs from '@/components/NavTabs';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <NavTabs />
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
