import { Suspense } from 'react';
import NewLiveStrategyPage from '@/components/NewLiveStrategyPage';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">라이브 전략 만들기</h1>
      <Suspense fallback={null}>
        <NewLiveStrategyPage />
      </Suspense>
    </div>
  );
}
