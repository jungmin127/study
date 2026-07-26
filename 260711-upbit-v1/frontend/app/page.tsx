import { Suspense } from 'react';
import PortSetupForm from '@/components/PortSetupForm';

export default function HomePage() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">포트 설정</h1>
      <Suspense fallback={null}>
        <PortSetupForm />
      </Suspense>
    </div>
  );
}
