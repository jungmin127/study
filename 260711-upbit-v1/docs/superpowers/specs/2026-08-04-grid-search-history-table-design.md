# Grid Search 요청 이력 테이블 재설계 — Design Spec

## 배경 및 목표

`frontend/components/GridSearchHistory.tsx`(`/grid-search` 탭 하단 "요청 이력")가 현재
job 하나당 3줄짜리 카드로 렌더링된다(상태 배지 줄, 실패 시 에러 메시지 줄, 결과 줄 + "나머지
N개 보기"). job이 쌓일수록 스크롤이 길어지고, 코인/봉타입별로 비교해보기 어렵다.

사용자 요청 사항:
1. 완료/취소/실패 이력을 코인별로 볼 수 있어야 한다.
2. 코인 선택 박스(시세/등락률 불필요, 코인 선택만).
3. 선택한 코인에 대해 봉타입별로도 구분해서 볼 수 있어야 한다.
4. 각 이력을 3줄이 아닌 한 줄짜리 표 형태로. `[Grid]` 프리픽스 제거, "매수"/"매도" 볼드체.
5. 실행 시각 / 기간 시작일 / 1위 수익률에 대한 오름차순·내림차순 정렬.

## 범위

- **백엔드 변경 없음.** `GET /api/v1/grid-search/jobs`가 이미 `market`/`timeframe`/`start`/
  `end`/`started_at`/`result_json`(rank/run_id/return_pct/title) 등 필요한 필드를 전부
  내려준다. 이 스펙은 순수 프론트엔드 재설계다.
- `frontend/components/GridSearchHistory.tsx`를 새 필터+정렬 가능한 표로 교체.
- `frontend/components/BacktestCoinFilter.tsx`를 코인 필터 재사용을 위해 일반화(아래 참고).
- 새 헬퍼 `frontend/lib/grid-result-title.ts` 추가(타이틀 문자열 파싱).
- `GridSearchPage.tsx`/백엔드 API 계약은 변경 없음 — `jobs` prop을 그대로 `GridSearchHistory`에
  전달하는 기존 흐름 유지.

## 필터

**코인 필터**
- 기존 `BacktestRunsTable.tsx`가 이미 쓰고 있는 `BacktestCoinFilter` 컴포넌트를 재사용한다.
- 현재 이 컴포넌트는 `options: { market: string; koreanName: string }[]`를 요구하는데,
  grid search job에는 한글명이 없다(시세 조회를 안 하기로 결정했으므로 `getMarkets()` 호출
  없음). `koreanName`을 optional로 바꾸고, 없으면 괄호 부분을 렌더링하지 않도록 일반화한다.
  이 변경은 기존 호출부(`BacktestRunsTable`)에 영향 없음(항상 `koreanName`을 넘기던 그대로
  동작).
- `GridSearchHistory`는 `jobs` 배열에서 등장하는 고유 `market` 값들로 옵션을 만든다
  (`koreanName` 없이 `market`만).
- "전체 코인" 옵션 포함(기존 `BacktestCoinFilter`가 이미 `value=null`일 때 전체를 의미하는
  패턴을 갖고 있음, 그대로 재사용).

**봉타입 필터**
- 새로 만드는 작은 인라인 드롭다운(기존 `@/components/ui/select` — `PortSetupForm.tsx`의
  `CANDLE_UNITS` 셀렉트와 동일한 컴포넌트/패턴). 검색이 필요 없는 소수 옵션(8개 이하)이라
  `BacktestCoinFilter`류의 Command+Popover 조합 대신 단순 `Select`를 쓴다.
- 옵션은 "전체" + `jobs` 배열에서 등장하는 고유 `timeframe` 값들, 라벨은 기존
  `formatTimeframe()` 재사용.
- 코인 필터와 독립적으로 조합(AND 조건).

**진행중(running) job**
- 이 표에서는 완전히 제외한다(화면 상단 진행률 카드에서만 표시, 기존 `GridSearchPage`의
  `runningJob` 처리는 변경 없음).

## 표

컬럼(왼쪽부터):

| 컬럼 | 내용 | 정렬 |
|---|---|---|
| 상태 | 완료/취소/실패 배지(기존 `STATUS_LABEL`/`STATUS_VARIANT` 그대로) | - |
| 코인 | `market`에서 `KRW-` 접두어 제거(예: `DOGE`) | - |
| 봉타입 | `formatTimeframe(timeframe)` | - |
| 기간 | `${start} ~ ${end}` (정렬은 `start` 기준) | ✅ |
| 실행시각 | `formatDateTime(started_at)` | ✅ |
| 1위 조건 | 완료 job의 `result_json[0].title`을 파싱해 "매수 ..." / "매도 ..." 형태로 렌더링(아래
  타이틀 파싱 참고). 완료가 아니거나 결과가 없으면 `-`. | - |
| 1위 수익률 | `result_json[0].return_pct`, `returnRateColor()`로 색상. 없으면 `-`,
  정렬 시 항상 맨 뒤로 밀림(기존 `BacktestRunsTable`의 null 처리 관례와 동일) | ✅ |

- 정렬은 컬럼 헤더 클릭으로 토글(오름/내림/기본), 아이콘은 기존 `BacktestRunsTable`/
  `CoinSelect`의 `ArrowUp`/`ArrowDown`/`ArrowUpDown` 패턴 그대로 재사용.
- 기본 정렬은 없음(`sortKey: null`) — 백엔드가 이미 `started_at DESC`로 내려주므로 클라이언트
  재정렬 없이 그 순서를 그대로 보여준다(기존 `BacktestRunsTable`과 동일한 관례).
- 상위N개/경과시간(elapsed_sec) 컬럼은 이번 재설계에서 제외한다(사용자 승인됨). 필요해지면
  별도 요청으로 다시 추가.

## 행 확장(아코디언)

- 펼칠 내용이 있는 행(완료 + 결과 2개 이상, 또는 실패 + `error_message` 존재)만 클릭 가능
  표시(마우스 오버 시 배경색 변화 + 우측에 chevron 아이콘). 그 외 행은 클릭해도 반응 없음.
- 완료 job: 클릭 시 바로 아래에 2위~N위 결과가 인라인으로 펼쳐짐(현재 "나머지 N개 보기"와
  동일한 데이터: 순위/수익률/타이틀 링크). 각 결과는 표 폭 전체를 쓰는 별도 sub-row로 렌더링.
- 실패 job: 클릭 시 `error_message` 전체 텍스트가 펼쳐짐.
- 취소(canceled) job이나 결과가 정확히 1개뿐인 완료 job은 펼칠 내용이 없으므로 확장 불가.
- 확장 상태는 기존 컴포넌트의 `expanded: Set<string>` 패턴을 그대로 재사용(job id 기준
  토글).

## 타이틀 파싱 (`[Grid]` 제거 + 매수/매도 볼드)

`scripts/grid_search.py`(수정 대상 아님)가 만드는 title 형식은 항상:

```
[Grid] 매수 {지표}{파라미터}{연산자}{임계값} / 매도 {지표}{파라미터}{연산자}{임계값}
```

새 헬퍼 `frontend/lib/grid-result-title.ts`:

```typescript
export interface ParsedGridResultTitle {
  buyRest: string;
  sellRest: string;
}

export function parseGridResultTitle(title: string): ParsedGridResultTitle | null {
  const withoutPrefix = title.startsWith('[Grid] ') ? title.slice('[Grid] '.length) : title;
  const match = withoutPrefix.match(/^매수\s+(.+?)\s+\/\s+매도\s+(.+)$/);
  if (!match) return null;
  return { buyRest: match[1], sellRest: match[2] };
}
```

- 매칭 실패 시(형식이 다른 경우, 예: 그리드서치가 아닌 다른 경로로 만들어진 title) `null`을
  반환하고, 렌더링 쪽에서는 원본 `title`을 그대로(볼드 처리 없이) 보여주는 폴백을 둔다 —
  데이터가 예상과 다르다고 화면이 깨지면 안 됨.
- 렌더링: `parseGridResultTitle`이 값을 반환하면 `<strong>매수</strong> {buyRest} / <strong>매도</strong>
  {sellRest}`로 조립. `null`이면 원본 `title` 그대로 표시.
- 이 파싱은 순수 문자열 처리이며 `scripts/grid_search.py`나 DB에 저장된 데이터를 바꾸지
  않는다 — 렌더링 시점에만 적용.

## 컴포넌트 구조 변경

- `frontend/components/BacktestCoinFilter.tsx`: `CoinFilterOption.koreanName`을
  `string | undefined`로 변경, 렌더링부에서 `koreanName`이 있을 때만 괄호 부분 표시.
- `frontend/lib/grid-result-title.ts`: 신규 파일, `parseGridResultTitle` 하나만 export.
- `frontend/components/GridSearchHistory.tsx`: 전면 재작성.
  - Props(`jobs: GridSearchJob[]`)는 변경 없음 — `GridSearchPage.tsx`는 손대지 않는다.
  - 내부적으로 `status !== 'running'`인 job만 필터링한 뒤, 코인/봉타입 필터와 정렬을 적용.
  - 코인/봉타입 옵션은 (running 제외한) 전체 job 목록에서 파생 — 필터가 서로의 옵션 목록을
    좁히지 않음(코인을 고른다고 봉타입 옵션이 줄어들지 않음, 반대도 마찬가지) — 두 필터는
    독립적으로 동작.

## 자기 검토(스펙 완성도)

- 플레이스홀더/TBD 없음.
- 코인 필터가 optional `koreanName`으로 일반화돼도 기존 `BacktestRunsTable` 호출부는 항상
  값을 넘기므로 회귀 없음.
- "1위 수익률 없음"(취소/실패) 케이스의 정렬 동작을 기존 코드베이스 관례(널은 항상 끝)와
  명시적으로 일치시켜 모호함 제거.
- 확장 가능/불가능 행의 구분 기준을 명시해 "언제 클릭이 되는지" 모호함 제거.
- 스코프가 프론트엔드 한 화면(그리고 그 화면이 재사용하는 컴포넌트 1개 일반화 + 헬퍼 1개
  신규)으로 좁아, 단일 구현 플랜으로 처리 가능.
