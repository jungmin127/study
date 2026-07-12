'use client';

import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { getCombos, getHistory } from '@/lib/api/eda';
import type { Combo, SweepResult } from '@/lib/types/eda';

function comboKey(c: Combo): string {
  return `${c.signal_set_name}|${c.market}|${c.timeframe}|${c.is_combined}`;
}

export default function ComboHistoryChart() {
  const [combos, setCombos] = useState<Combo[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>('');
  const [history, setHistory] = useState<SweepResult[]>([]);

  useEffect(() => {
    getCombos().then((cs) => {
      setCombos(cs);
      if (cs.length > 0) setSelectedKey(comboKey(cs[0]));
    });
  }, []);

  useEffect(() => {
    const combo = combos.find((c) => comboKey(c) === selectedKey);
    if (!combo) return;
    let ignore = false;
    getHistory(combo).then((h) => {
      if (!ignore) setHistory(h);
    });
    return () => {
      ignore = true;
    };
  }, [selectedKey, combos]);

  if (combos.length === 0) {
    return <p className="text-muted-foreground">아직 스윕 데이터가 없습니다.</p>;
  }

  return (
    <div>
      <select
        className="mb-4 rounded border px-2 py-1 text-sm"
        value={selectedKey}
        onChange={(e) => setSelectedKey(e.target.value)}
      >
        {combos.map((c) => (
          <option key={comboKey(c)} value={comboKey(c)}>
            {c.signal_set_name}{c.is_combined ? '(혼합)' : ''} / {c.market} / {c.timeframe}
          </option>
        ))}
      </select>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={history}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="swept_at" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Line type="monotone" dataKey="return_rate" stroke="#3b82f6" name="수익률(%)" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
