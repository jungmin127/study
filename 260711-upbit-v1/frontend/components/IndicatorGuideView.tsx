'use client';

import { useState } from 'react';
import type { IndicatorCatalogItem } from '@/lib/types/eda';
import { CATEGORY_DOT_COLOR, CATEGORY_ICON, CATEGORY_ORDER } from '@/lib/indicator-categories';
import { INDICATOR_GUIDE } from '@/lib/indicator-guide';
import { buildGuideExample } from '@/lib/indicator-example-builder';
import { OPERATOR_SYMBOLS } from '@/lib/condition-summary';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import GuideLineChart from '@/components/guide/GuideLineChart';
import ZoneGauge from '@/components/guide/ZoneGauge';

function groupByCategory(catalog: IndicatorCatalogItem[]): { label: string; items: IndicatorCatalogItem[] }[] {
  return CATEGORY_ORDER.map((label) => ({
    label,
    items: catalog.filter((item) => item.category === label),
  })).filter((cat) => cat.items.length > 0);
}

function IndicatorCard({ item }: { item: IndicatorCatalogItem }) {
  const guide = INDICATOR_GUIDE[item.value];
  if (!guide) return null;
  const example = buildGuideExample(item.value);
  const dotColor = CATEGORY_DOT_COLOR[item.category] ?? 'bg-slate-400';

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
          <CardTitle>{item.label}</CardTitle>
          <code className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">{item.value}</code>
          {item.sellOnly && <Badge variant="secondary">매도 조건 전용</Badge>}
          {item.fixedOperator && (
            <Badge variant="outline">연산자 고정: {OPERATOR_SYMBOLS[item.fixedOperator]}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed">{guide.meaning}</p>

        {(item.params.length > 0 || guide.params.length > 0) && (
          <div>
            <p className="mb-1.5 text-xs font-semibold text-muted-foreground">파라미터</p>
            <div className="overflow-hidden rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>키</TableHead>
                    <TableHead>기본값</TableHead>
                    <TableHead>이 값이 뜻하는 것</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {item.params.map((p) => {
                    const note = guide.params.find((g) => g.key === p.key);
                    return (
                      <TableRow key={p.key}>
                        <TableCell className="font-mono text-xs">{p.key} ({p.label})</TableCell>
                        <TableCell className="tabular-nums">{p.default}</TableCell>
                        <TableCell className="whitespace-normal text-muted-foreground">{note?.role ?? '-'}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        <div>
          <p className="mb-1.5 text-xs font-semibold text-muted-foreground">계산 공식</p>
          <pre className="whitespace-pre-wrap rounded-md bg-muted px-3 py-2 font-mono text-xs leading-relaxed">
            {guide.formula}
          </pre>
        </div>

        {example.rows.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-semibold text-muted-foreground">예시로 계산해보기 (합성 데이터)</p>
            <div className="overflow-hidden rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>봉</TableHead>
                    {example.columns.map((c) => (
                      <TableHead key={c.key}>{c.label}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {example.rows.map((row) => (
                    <TableRow key={row.bar}>
                      <TableCell className="tabular-nums">{row.bar}</TableCell>
                      {example.columns.map((c) => (
                        <TableCell key={c.key} className="tabular-nums">
                          {row.cells[c.key]}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}

        {example.chart.type === 'line' && (
          <div>
            <p className="mb-1.5 text-xs font-semibold text-muted-foreground">시각적으로 보기</p>
            <GuideLineChart chart={example.chart} />
          </div>
        )}
        {example.chart.type === 'gauge' && (
          <div>
            <p className="mb-1.5 text-xs font-semibold text-muted-foreground">지금 값이 구간의 어디쯤인지</p>
            <ZoneGauge chart={example.chart} />
          </div>
        )}

        <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2">
          <p className="mb-1 text-xs font-semibold">이 조건이 뜻하는 것</p>
          <p className="text-sm leading-relaxed">{guide.thresholdExample}</p>
        </div>

        <div>
          <p className="mb-1 text-xs font-semibold text-muted-foreground">실전에서 이렇게 씁니다</p>
          <p className="text-sm leading-relaxed text-muted-foreground">{guide.usage}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export default function IndicatorGuideView({ catalog }: { catalog: IndicatorCatalogItem[] }) {
  const categories = groupByCategory(catalog);
  const [selected, setSelected] = useState<string>(categories[0]?.items[0]?.value ?? '');
  const selectedItem = catalog.find((item) => item.value === selected) ?? null;

  return (
    <div className="flex gap-6">
      <nav className="sticky top-20 flex w-56 shrink-0 flex-col gap-4 self-start overflow-y-auto" style={{ maxHeight: 'calc(100vh - 6rem)' }}>
        {categories.map((cat) => {
          const Icon = CATEGORY_ICON[cat.label] ?? CATEGORY_ICON['추세'];
          const dotColor = CATEGORY_DOT_COLOR[cat.label] ?? 'bg-slate-400';
          return (
            <div key={cat.label}>
              <div className="mb-1 flex items-center gap-1.5 px-3 text-xs font-semibold text-muted-foreground">
                <Icon className="size-3.5" />
                {cat.label}
              </div>
              <div className="flex flex-col gap-0.5">
                {cat.items.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setSelected(item.value)}
                    className={
                      selected === item.value
                        ? 'flex items-center gap-2 rounded-md bg-muted px-3 py-1.5 text-left text-sm font-medium text-foreground'
                        : 'flex items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground'
                    }
                  >
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
                    <span className="truncate">{item.label}</span>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </nav>

      <div className="min-w-0 flex-1">{selectedItem && <IndicatorCard item={selectedItem} />}</div>
    </div>
  );
}
