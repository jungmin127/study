import { Suspense } from 'react';
import GridSearchPage from '@/components/GridSearchPage';

export default function Page() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Grid Search</h1>
      <Suspense fallback={null}>
        <GridSearchPage />
      </Suspense>
    </div>
  );
}
