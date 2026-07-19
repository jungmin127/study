import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

const STEPS = [
  { title: '백테스트 설정', subtitle: '코인/전략/기간을 설정하세요.' },
  { title: '백테스트 결과', subtitle: '실행한 백테스트 결과가 저장됩니다.' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <header className="grid grid-cols-2 border-b">
          {STEPS.map((step, i) => (
            <div
              key={step.title}
              className={
                i === 0
                  ? 'bg-primary px-6 py-4 text-primary-foreground'
                  : 'bg-slate-100 px-6 py-4 text-muted-foreground dark:bg-slate-800'
              }
            >
              <p className="font-semibold">{step.title}</p>
              <p className="text-sm opacity-80">{step.subtitle}</p>
            </div>
          ))}
        </header>
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
