# 분석 탭 — 세그먼트 좌측 사이드바 + 코인 테이블화 설계

- 작성일: 2026-07-26
- 상태: 승인 대기 (사용자 리뷰 전)

## 배경 및 목적

`/analysis` 페이지가 세그먼트(규모) 카드(대형주/중형주/잡주 3그룹, 총 약 270여개 코인을 flex 리스트로 나열)와 세그먼트(섹터) placeholder 카드를 위아래로 쌓아 보여주고 있어, 페이지가 세로로 매우 길어지는 문제가 있다.

## 결정된 사항 (사용자 승인)

- `/analysis` 내부에 좌측 사이드바(세그먼트(규모) / 세그먼트(섹터) 두 항목) + 우측 콘텐츠 영역 레이아웃을 둔다. 클릭한 항목만 오른쪽에 표시(클라이언트 상태 전환, URL 쿼리스트링 없음 — 세그먼트(섹터)가 아직 placeholder라 공유 가능한 URL이 필요한 수준은 아님).
- 세그먼트(규모)의 코인 리스트(대형주/중형주/잡주 3그룹)를 shadcn `Table`로 전환하고, 그룹별로 `max-h-80 overflow-y-auto`로 감싸 개별 스크롤되게 한다(페이지 자체 길이는 늘어나지 않음).
- 각 코인 행에 현재가/전일대비등락률/거래대금을 백테스트 설정 탭의 코인선택(`CoinSelect`)과 **동일한 값·포맷·색상 컨벤션**으로 추가 표시한다. 이 값들은 세그먼트 배치 데이터에는 없으므로 실시간 마켓 데이터(`getMarkets()`)를 추가로 불러와 `market` 코드로 매칭한다.
- 기존 변동성(30일) 컬럼은 유지한다(대형주/중형주/잡주 분류 기준의 일부라 의미가 있음).
- `CoinSelect.tsx`에 비공개로 있던 포맷 함수들(`formatPrice`, `changeColorClass`, `formatChangeRate`, `formatChangePrice`, `formatTradePrice24h`)을 공용 파일로 추출해 중복 없이 재사용한다.

## 1. 포맷 함수 공용화 — `frontend/lib/market-format.ts` (신규)

`frontend/components/CoinSelect.tsx`의 비공개 함수 5개(`changeColorClass`, `formatPrice`, `formatChangeRate`, `formatChangePrice`, `formatTradePrice24h`)를 이 파일로 그대로 옮기고 각각 `export`한다. 로직 변경 없음, 위치만 이동.

`CoinSelect.tsx`는 이 5개 함수의 로컬 정의를 제거하고 `frontend/lib/market-format.ts`에서 import하도록 수정한다(동작 동일, 코드만 정리).

## 2. `frontend/lib/types/eda.ts` — 타입 확장 불필요

`SegmentSizeEntry`(기존)와 `Market`(기존)을 각각 그대로 쓰고, 화면에서 `market` 코드로 조인한다. 새 타입은 만들지 않고, 조인된 뷰모델은 컴포넌트 내부 지역 타입으로 충분히 표현한다:

```ts
interface SegmentRow extends SegmentSizeEntry {
  price: number | null;
  change_rate: number | null;
  change_price: number | null;
  trade_price_24h: number | null; // 실시간 거래대금 (CoinSelect와 동일 출처)
}
```

## 3. `frontend/app/analysis/page.tsx` — 데이터 조회 + 조인

전체를 아래로 교체:

```tsx
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
```

`getMarkets()`가 실패해도(네트워크 오류 등) 세그먼트 분류 자체는 보여줘야 하므로, `Promise.all` 대신 `getMarkets()` 실패를 흡수하는 방식은 범위 밖(현재 `getSegmentSizeAnalysis()`도 실패 시 페이지 전체가 깨지는 기존 동작과 동일하게 둔다 — 이번 작업은 레이아웃/표시 개선이 목적이지 에러 내성 강화가 목적이 아님).

## 4. `frontend/components/AnalysisSidebarView.tsx` — 신규 (좌측 사이드바 + 우측 콘텐츠)

```tsx
'use client';

import { useState } from 'react';
import { BarChart3, PieChart } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import SegmentSizeTable, { type SegmentRow } from '@/components/SegmentSizeTable';

type Section = 'size' | 'sector';

const SECTIONS: { key: Section; label: string; icon: typeof BarChart3 }[] = [
  { key: 'size', label: '세그먼트(규모)', icon: BarChart3 },
  { key: 'sector', label: '세그먼트(섹터)', icon: PieChart },
];

export default function AnalysisSidebarView({ segmentSizeRows }: { segmentSizeRows: SegmentRow[] }) {
  const [section, setSection] = useState<Section>('size');

  return (
    <div className="flex gap-6">
      <nav className="flex w-44 shrink-0 flex-col gap-1">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setSection(key)}
            className={
              section === key
                ? 'flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm font-medium text-foreground'
                : 'flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground'
            }
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1">
        {section === 'size' ? (
          <SegmentSizeTable rows={segmentSizeRows} />
        ) : (
          <Card>
            <CardContent className="pt-4">
              <p className="text-muted-foreground">준비 중입니다.</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
```

## 5. `frontend/components/SegmentSizeTable.tsx` — 신규 (기존 `SegmentSizeCard.tsx` 대체)

기존 `SegmentSizeCard.tsx`의 `SEGMENT_ORDER`/`SEGMENT_LABELS`/`groupBySegment`/`formatTradeValue`/`formatVolatility`는 그대로 재사용하되, 렌더링을 `Table`로 교체하고 신규 컬럼을 추가:

```tsx
import { AlertTriangle } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { changeColorClass, formatChangeRate, formatPrice, formatTradePrice24h } from '@/lib/market-format';
import type { SegmentSizeEntry } from '@/lib/types/eda';

export interface SegmentRow extends SegmentSizeEntry {
  price: number | null;
  change_rate: number | null;
  change_price: number | null;
  trade_price_24h: number | null;
}

const SEGMENT_ORDER: SegmentRow['segment'][] = ['large', 'mid', 'junk'];
const SEGMENT_LABELS: Record<SegmentRow['segment'], string> = {
  large: '대형주',
  mid: '중형주',
  junk: '잡주',
};

function formatVolatility(value: number | null): string {
  if (value === null) return '-';
  return `${(value * 100).toFixed(2)}%`;
}

export function groupBySegment(rows: SegmentRow[]): { segment: SegmentRow['segment']; rows: SegmentRow[] }[] {
  return SEGMENT_ORDER.map((segment) => ({
    segment,
    rows: rows.filter((r) => r.segment === segment),
  }));
}

export default function SegmentSizeTable({ rows }: { rows: SegmentRow[] }) {
  if (rows.length === 0) {
    return <p className="text-muted-foreground">배치 실행 중입니다. 잠시 후 새로고침해 주세요.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {groupBySegment(rows).map(({ segment, rows: group }) => (
        <div key={segment}>
          <p className="mb-2 text-sm font-semibold">
            {SEGMENT_LABELS[segment]} ({group.length})
          </p>
          <div className="max-h-80 overflow-y-auto rounded-md border">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead>코인</TableHead>
                  <TableHead className="text-right">현재가</TableHead>
                  <TableHead className="text-right">전일대비등락률</TableHead>
                  <TableHead className="text-right">거래대금</TableHead>
                  <TableHead className="text-right">변동성(30일)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.map((r) => (
                  <TableRow key={r.market}>
                    <TableCell>
                      {r.korean_name}
                      {r.is_caution && (
                        <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                          <AlertTriangle className="size-3.5" />
                          유의종목
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{formatPrice(r.price)}</TableCell>
                    <TableCell className={`text-right tabular-nums ${changeColorClass(r.change_rate)}`}>
                      {formatChangeRate(r.change_rate)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatTradePrice24h(r.trade_price_24h)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatVolatility(r.volatility_30d)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      ))}
    </div>
  );
}
```

`frontend/components/SegmentSizeCard.tsx`는 삭제한다(이 파일로 완전히 대체).

## 범위 밖

- 세그먼트(섹터) 실제 콘텐츠 구현 — 여전히 placeholder.
- `getMarkets()`/`getSegmentSizeAnalysis()` 실패 시 부분 렌더링(에러 내성 강화) — 기존 동작(전체 실패) 그대로.
- 사이드바 선택 상태의 URL 반영(쿼리스트링) — 클라이언트 상태로만 처리.
- 다른 페이지(백테스트 설정 등)의 `CoinSelect` 동작 변경 — 포맷 함수 위치만 이동, 로직/출력 동일.

## Self-Review 결과

- **스펙 커버리지**: 사용자가 승인한 4가지(좌측 사이드바+우측 콘텐츠, Table+그룹별 고정 높이 스크롤, 실시간 현재가/등락률/거래대금 추가, 변동성 컬럼 유지)가 각각 4/5번 섹션에 반영됨.
- **내부 정합성**: `SegmentSizeEntry`/`Market` 타입을 그대로 쓰고 조인 뷰모델(`SegmentRow`)만 로컬로 정의한다는 결정이 2번과 5번 섹션에서 일관됨.
- **범위 확인**: 섹터 placeholder 유지, 에러 내성 미강화, URL 미반영을 범위 밖에 명시.
- **대상 파일 목록**: `frontend/lib/market-format.ts`(신규), `frontend/components/CoinSelect.tsx`(포맷 함수 제거+import로 교체), `frontend/app/analysis/page.tsx`(전체 교체), `frontend/components/AnalysisSidebarView.tsx`(신규), `frontend/components/SegmentSizeTable.tsx`(신규), `frontend/components/SegmentSizeCard.tsx`(삭제).
