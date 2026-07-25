import { getSegmentSizeAnalysis } from '@/lib/api/eda';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import SegmentSizeCard from '@/components/SegmentSizeCard';

export default async function AnalysisPage() {
  const segmentSizeEntries = await getSegmentSizeAnalysis();

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">분석</h1>
      <div className="flex flex-col gap-4">
        <SegmentSizeCard entries={segmentSizeEntries} />
        <Card>
          <CardHeader>
            <CardTitle>세그먼트(섹터)</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground">준비 중입니다.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
