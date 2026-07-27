'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BarChart3, BookOpen, FlaskConical, Settings } from 'lucide-react';
import ThemeToggle from '@/components/ThemeToggle';

const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/analysis', title: '분석', icon: BarChart3 },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function NavTabs() {
  const pathname = usePathname();

  return (
    <header className="flex items-center justify-between border-b px-6">
      <nav className="flex gap-6">
        {STEPS.map((step) => {
          const Icon = step.icon;
          const active = isActive(pathname, step.href);
          return (
            <Link
              key={step.href}
              href={step.href}
              className={
                active
                  ? 'flex items-center gap-1.5 border-b-2 border-primary py-3 font-semibold text-foreground'
                  : 'flex items-center gap-1.5 border-b-2 border-transparent py-3 text-muted-foreground hover:text-foreground'
              }
            >
              <Icon className="size-4" />
              {step.title}
            </Link>
          );
        })}
      </nav>
      <ThemeToggle />
    </header>
  );
}
