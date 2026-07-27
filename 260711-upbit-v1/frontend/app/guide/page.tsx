import { getIndicatorCatalog } from '@/lib/api/eda';
import IndicatorGuideView from '@/components/IndicatorGuideView';

export default async function GuidePage() {
  const catalog = await getIndicatorCatalog();

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold">지표 가이드</h1>
      <p className="mb-4 text-sm text-muted-foreground">
        조건 빌더에서 고를 수 있는 모든 지표가 무엇을 계산하는지, 파라미터와 threshold가 각각 무슨 뜻인지 합성
        데이터로 직접 계산해가며 정리했습니다.
      </p>
      <IndicatorGuideView catalog={catalog} />
    </div>
  );
}
