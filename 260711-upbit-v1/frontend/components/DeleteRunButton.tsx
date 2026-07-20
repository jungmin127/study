'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { deleteBacktestRun } from '@/lib/api/eda';

export default function DeleteRunButton({ runId }: { runId: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function handleDelete() {
    if (!window.confirm('이 백테스트 결과를 삭제하시겠습니까?')) return;

    setPending(true);
    try {
      await deleteBacktestRun(runId);
      router.refresh();
    } catch {
      window.alert('삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
      setPending(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleDelete}
      disabled={pending}
      className="text-red-600 hover:underline disabled:opacity-50 dark:text-red-400"
    >
      삭제
    </button>
  );
}
