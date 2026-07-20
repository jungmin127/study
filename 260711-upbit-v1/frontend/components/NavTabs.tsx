'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const STEPS = [
  { href: '/', title: '백테스트 설정', subtitle: '코인/전략/기간을 설정하세요.' },
  { href: '/backtests', title: '백테스트 결과', subtitle: '실행한 백테스트 결과가 저장됩니다.' },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function NavTabs() {
  const pathname = usePathname();

  return (
    <header className="grid grid-cols-2 border-b">
      {STEPS.map((step) => (
        <Link
          key={step.href}
          href={step.href}
          className={
            isActive(pathname, step.href)
              ? 'bg-primary px-6 py-4 text-primary-foreground'
              : 'bg-slate-100 px-6 py-4 text-muted-foreground hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700'
          }
        >
          <p className="font-semibold">{step.title}</p>
          <p className="text-sm opacity-80">{step.subtitle}</p>
        </Link>
      ))}
    </header>
  );
}
