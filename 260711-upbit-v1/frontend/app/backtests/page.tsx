import Link from 'next/link';

export default function BacktestsIndexPage() {
  return (
    <div className="text-muted-foreground">
      <h1 className="text-lg font-semibold text-foreground">백테스트 상세</h1>
      <p className="mt-2">
        <Link href="/" className="text-blue-600 hover:underline dark:text-blue-400">
          전략 × 코인 × 봉타입 수익률 테이블
        </Link>
        에서 행의 &quot;보기&quot; 링크를 클릭하면 해당 백테스트의 자산 곡선과 거래 내역을 볼 수 있습니다.
      </p>
    </div>
  );
}
