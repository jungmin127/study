'use client';

import { useState } from 'react';
import RegimeAdxOverview from '@/components/RegimeAdxOverview';
import RegimeAdxDetailView from '@/components/RegimeAdxDetailView';
import { MAJOR_MARKETS } from '@/lib/constants/regime';

export default function RegimeDashboard() {
  const [market, setMarket] = useState<string>(MAJOR_MARKETS[0]);

  return (
    <div className="space-y-6">
      <RegimeAdxOverview selectedMarket={market} onSelectMarket={setMarket} />
      <RegimeAdxDetailView market={market} onMarketChange={setMarket} />
    </div>
  );
}
