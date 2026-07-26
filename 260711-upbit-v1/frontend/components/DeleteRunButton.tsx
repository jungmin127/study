'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Trash2 } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { deleteBacktestRun } from '@/lib/api/eda';

export default function DeleteRunButton({ runId }: { runId: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setPending(true);
    setError(null);
    try {
      await deleteBacktestRun(runId);
      router.refresh();
    } catch {
      setError('삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
      setPending(false);
    }
  }

  return (
    <AlertDialog>
      {/* base-ui's AlertDialog.Trigger already renders a native <button> and manages its own
          ref (needed for focus restoration on close), so it doesn't support Radix-style
          `asChild` composition. Wrapping it with `render={<Button .../>}` (mirroring
          AlertDialogCancel's pattern) actually breaks here: our `Button` wrapper isn't
          React.forwardRef-wrapped, so base-ui logs "cannot be given refs" / "expected a
          native <button>" console errors once the ref fails to reach the DOM node. Instead,
          apply Button's own class-variance styles directly to the Trigger (same approach as
          PopoverTrigger in CoinSelect.tsx / TooltipTrigger in InfoTooltip.tsx). */}
      <AlertDialogTrigger
        type="button"
        className={buttonVariants({ variant: 'ghost', size: 'icon-sm' })}
        disabled={pending}
        aria-label="삭제"
      >
        <Trash2 className="size-4 text-destructive" />
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>이 백테스트 결과를 삭제하시겠습니까?</AlertDialogTitle>
          <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
        </AlertDialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel>취소</AlertDialogCancel>
          <AlertDialogAction onClick={handleDelete}>삭제</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
