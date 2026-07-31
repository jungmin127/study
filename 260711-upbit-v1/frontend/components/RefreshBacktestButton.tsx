'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { refreshBacktestRun } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';

export default function RefreshBacktestButton({ runId }: { runId: string }) {
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      await refreshBacktestRun(runId);
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '갱신에 실패했습니다.');
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
        <RefreshCw className={refreshing ? 'size-3.5 animate-spin' : 'size-3.5'} />
        {refreshing ? '갱신 중...' : '최신 데이터로 갱신'}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
