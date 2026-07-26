'use client';

import { CircleHelp } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

export default function InfoTooltip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground"
        aria-label="설명 보기"
      >
        <CircleHelp className="size-3.5" />
      </TooltipTrigger>
      <TooltipContent className="max-w-64 whitespace-pre-line text-left">{text}</TooltipContent>
    </Tooltip>
  );
}
