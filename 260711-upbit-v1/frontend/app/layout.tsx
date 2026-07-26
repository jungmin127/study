import type { Metadata } from 'next';
import NavTabs from '@/components/NavTabs';
import { TooltipProvider } from '@/components/ui/tooltip';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem('theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <TooltipProvider>
          <NavTabs />
          <main className="p-6">{children}</main>
        </TooltipProvider>
      </body>
    </html>
  );
}
