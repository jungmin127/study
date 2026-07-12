import ComboHistoryChart from '@/components/ComboHistoryChart';

export default function HistoryPage() {
  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">조합별 시간대 수익률 추이</h1>
      <ComboHistoryChart />
    </div>
  );
}
