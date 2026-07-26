'use client';

import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { getCombos, getHistory } from '@/lib/api/eda';
import type { Combo, SweepResult } from '@/lib/types/eda';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

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
      <Select value={selectedKey} onValueChange={(value) => value !== null && setSelectedKey(value)}>
        <SelectTrigger className="mb-4 w-auto min-w-64">
          <SelectValue>
            {(value: string | null) => {
              const combo = combos.find((c) => comboKey(c) === value);
              if (!combo) return value ?? '';
              return `${combo.signal_set_name}${combo.is_combined ? '(혼합)' : ''} / ${combo.market} / ${combo.timeframe}`;
            }}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {combos.map((c) => (
            <SelectItem key={comboKey(c)} value={comboKey(c)}>
              {c.signal_set_name}{c.is_combined ? '(혼합)' : ''} / {c.market} / {c.timeframe}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={history}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="swept_at" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Line type="monotone" dataKey="return_rate" stroke="var(--color-primary)" name="수익률(%)" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
