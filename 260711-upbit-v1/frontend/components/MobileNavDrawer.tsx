'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog';
import { Menu, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import ThemeToggle from '@/components/ThemeToggle';
import { isActive } from '@/lib/nav-active';
import { cn } from '@/lib/utils';

export interface MobileNavStep {
  href: string;
  title: string;
  icon: LucideIcon;
}

export default function MobileNavDrawer({ steps }: { steps: MobileNavStep[] }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Trigger
        render={<Button type="button" variant="ghost" size="icon-lg" aria-label="메뉴 열기" />}
      >
        <Menu className="size-5" />
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/30 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <DialogPrimitive.Popup className="fixed inset-y-0 right-0 z-50 flex h-full w-64 max-w-[80vw] flex-col gap-1 border-l bg-background p-3 outline-none data-open:animate-in data-open:slide-in-from-right data-closed:animate-out data-closed:slide-out-to-right">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold">메뉴</span>
            <DialogPrimitive.Close
              render={<Button type="button" variant="ghost" size="icon-lg" aria-label="메뉴 닫기" />}
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <nav className="flex flex-col gap-2">
            {steps.map((step) => {
              const Icon = step.icon;
              const active = isActive(pathname, step.href);
              return (
                <Link
                  key={step.href}
                  href={step.href}
                  onClick={() => setOpen(false)}
                  className={cn(
                    'flex items-center gap-2 rounded-md px-3 py-2.5 text-sm',
                    active
                      ? 'bg-muted font-semibold text-foreground'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )}
                >
                  <Icon className="size-4" />
                  {step.title}
                </Link>
              );
            })}
          </nav>
          <div className="mt-auto flex items-center justify-between border-t pt-2">
            <span className="text-xs text-muted-foreground">테마</span>
            <ThemeToggle />
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
