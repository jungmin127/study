import { getMarkets, getSegmentSizeAnalysis } from '@/lib/api/eda';
import AnalysisSidebarView from '@/components/AnalysisSidebarView';

export default async function AnalysisPage() {
  const [segmentSizeEntries, markets] = await Promise.all([
    getSegmentSizeAnalysis(),
    getMarkets(),
  ]);

  const marketByCode = new Map(markets.map((m) => [m.market, m]));
  const segmentSizeRows = segmentSizeEntries.map((entry) => {
    const market = marketByCode.get(entry.market);
    return {
      ...entry,
      price: market?.price ?? null,
      change_rate: market?.change_rate ?? null,
      change_price: market?.change_price ?? null,
      trade_price_24h: market?.trade_price_24h ?? null,
    };
  });

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">분석</h1>
      <AnalysisSidebarView segmentSizeRows={segmentSizeRows} />
    </div>
  );
}
