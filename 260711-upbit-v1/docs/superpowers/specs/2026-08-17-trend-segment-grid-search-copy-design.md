# 추세 세그먼트 → Grid Search 복사 설계

날짜: 2026-08-17

## 배경

"세그먼트 - 추세기반" 탭(`/analysis`, `TrendSegmentView.tsx`)에서 코인을 선택하면 상승/하락/횡보 구간별 기간·일수·등락률·패턴이 `TrendSegmentTable.tsx`에 표로 나열된다. 특정 구간(패턴)이 흥미로우면 그 코인/기간으로 바로 Grid Search를 돌려보고 싶은 경우가 많은데, 지금은 코인명과 날짜를 직접 손으로 옮겨 적어야 한다.

`BacktestRunsTable.tsx`/`BacktestRunCard.tsx`에는 이미 동일한 성격의 "복사" 버튼이 있다 — 백테스트 조건을 쿼리스트링으로 인코딩해 `/`(백테스트 설정 폼)로 이동시키고 그 폼이 쿼리파라미터를 읽어 프리필한다. `/grid-search` 페이지(`GridSearchPage.tsx`)도 이미 `market`/`timeframe`/`capital`/`start`/`end`/`topN` 쿼리파라미터를 읽어 `GridSearchForm`을 프리필하도록 구현돼 있다(grid-search 스킬이 안내 링크를 만들 때 쓰는 것과 동일한 파라미터). 이 두 기존 패턴을 그대로 이어붙이면 된다.

## 결정 사항 (Q&A로 확정)

- 복사 버튼은 패턴 셀(`seg.pattern_label`) 옆에 아이콘 전용으로 배치한다(텍스트 라벨 없음). 행마다 반복되므로 아이콘만으로 표를 덜 산만하게 유지한다. 아이콘 전용이므로 `aria-label`로 접근성을 보완한다.
- 쿼리파라미터는 `market`/`start`/`end` 세 개만 채운다. `timeframe`/`capital`/`topN`은 넘기지 않고 `/grid-search` 폼의 기존 기본값(`minutes60`/`1000000`/`20`)을 그대로 따른다 — 세그먼트 데이터에는 timeframe이라는 개념이 없고, 사용자가 요청한 것도 "코인명, 날짜 프리필"뿐이다.
- 이동은 서버 왕복 없는 클라이언트 사이드 `next/link` 내비게이션이며, 기존 "복사" 버튼들과 동일하게 새 탭이 아니라 같은 탭에서 화면 전환된다.

## 변경 사항

### `frontend/components/TrendSegmentTable.tsx`

- `TrendSegmentTableProps`에 `market: string` 추가.
- `buildGridSearchHref(market: string, seg: TrendSegment): string` 헬퍼 추가:
  ```ts
  function buildGridSearchHref(market: string, seg: TrendSegment): string {
    const params = new URLSearchParams({
      market,
      start: seg.start_date,
      end: seg.end_date,
    });
    return `/grid-search?${params.toString()}`;
  }
  ```
- 패턴 `TableCell`을 `seg.pattern_label` 텍스트 + 아이콘 전용 `Button`으로 구성. `BacktestRunsTable.tsx`의 기존 복사 버튼과 동일한 구현 패턴(`variant="link"`, `size="sm"`, `nativeButton={false}`, `role="link"`, `render={<Link href={buildGridSearchHref(market, seg)} />}`)을 따르되, 텍스트 없이 `Copy` 아이콘만 넣고 `aria-label="그리드서치로 복사"`를 추가한다.

### `frontend/components/TrendSegmentView.tsx`

- `<TrendSegmentTable segments={data.segments} market={selectedMarket} />` 로 변경해 현재 선택된 코인 코드를 내려준다.

## 테스트 계획

이 변경은 프론트엔드 전용 UI 배선(순수 링크 생성 + prop 전달)이며 기존 백엔드/타입에 변화가 없다. 별도 자동화 테스트는 추가하지 않고, 브라우저에서 다음을 수동 확인한다:
- 세그먼트 표의 패턴 셀에 복사 아이콘이 보이는지
- 클릭 시 `/grid-search?market=<코인>&start=<시작일>&end=<종료일>` 로 이동하는지
- Grid Search 폼에 코인/기간이 채워져 있고 timeframe/운용자금/상위N개는 기존 기본값(1시간/1,000,000원/20)인지
