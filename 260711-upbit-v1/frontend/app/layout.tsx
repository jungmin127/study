import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

const STEPS = [
  { title: 'Step 1. 기본 설정', subtitle: '기본 조건들을 설정하세요.' },
  { title: 'Step 2. 매매대상 설정', subtitle: '매매할 대상들을 설정하세요.' },
  { title: 'Step 3. 매매조건 설정', subtitle: '매수/매도조건을 설정하세요.' },
  { title: 'Step 4. 포트 실행', subtitle: '백테스트 포트가 완성되었습니다.' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <header className="grid grid-cols-4 border-b">
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
