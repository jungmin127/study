'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const STEPS = [
  { href: '/', title: '백테스트 설정' },
  { href: '/backtests', title: '백테스트 결과' },
  { href: '/analysis', title: '분석' },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function NavTabs() {
  const pathname = usePathname();

  return (
    <header className="flex gap-6 border-b px-6">
      {STEPS.map((step) => (
        <Link
          key={step.href}
          href={step.href}
          className={
            isActive(pathname, step.href)
              ? 'border-b-2 border-primary py-3 font-semibold text-foreground'
              : 'border-b-2 border-transparent py-3 text-muted-foreground hover:text-foreground'
          }
        >
          {step.title}
        </Link>
      ))}
    </header>
  );
}
