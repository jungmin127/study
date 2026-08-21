'use client';

import { CircleHelp } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

export function InfoPopover({ children }: { children: React.ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger
        aria-label="설명 보기"
        className="text-muted-foreground/70 hover:text-muted-foreground"
      >
        <CircleHelp className="size-3.5" />
      </PopoverTrigger>
      <PopoverContent className="w-64 text-xs leading-relaxed text-muted-foreground">
        {children}
      </PopoverContent>
    </Popover>
  );
}
