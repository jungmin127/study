'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Activity, BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
import ThemeToggle from '@/components/ThemeToggle';
import MobileNavDrawer from '@/components/MobileNavDrawer';
import { isActive } from '@/lib/nav-active';

const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/live-strategies', title: '라이브 전략', icon: Rocket },
  { href: '/journal', title: '매매일지', icon: ClipboardList },
  { href: '/analysis', title: '세그먼트', icon: BarChart3 },
  { href: '/regime', title: '장세 판별', icon: Activity },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];

export default function NavTabs() {
  const pathname = usePathname();
  const activeStep = STEPS.find((step) => isActive(pathname, step.href));

  return (
    <header className="flex items-center justify-between border-b px-3 md:px-6">
      <div className="flex w-full items-center justify-between py-2.5 md:hidden">
        <span className="truncate text-sm font-semibold">{activeStep?.title ?? 'Upbit 전략 EDA'}</span>
        <MobileNavDrawer steps={STEPS} />
      </div>

      <nav className="hidden gap-6 md:flex">
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
      <div className="hidden md:block">
        <ThemeToggle />
      </div>
    </header>
  );
}
