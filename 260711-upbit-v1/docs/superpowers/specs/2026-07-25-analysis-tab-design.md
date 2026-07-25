# 분석 탭 추가

## 목적
상단 탭 내비게이션(`백테스트 설정`, `백테스트 결과`)에 세 번째 탭 `분석`을 추가한다. 내용은 아직 없으며, 이후 분석 기능이 추가될 자리만 마련한다.

## 범위
- `frontend/components/NavTabs.tsx`: `STEPS` 배열에 `{ href: '/analysis', title: '분석' }` 추가(부제 없음). `grid-cols-2` → `grid-cols-3`.
- `frontend/app/analysis/page.tsx`: 신규 페이지. `백테스트 결과` 페이지와 동일한 최소 패턴으로 제목("분석")과 "준비 중입니다." placeholder 문구만 표시.

## 비범위
- 분석 탭 내부 콘텐츠(차트, 통계 등)는 이번 작업에 포함하지 않는다.

## 검증
- `npm run dev` 상태에서 `/analysis` 접속 시 탭 3개가 모두 보이고, 새 탭 클릭 시 placeholder 페이지가 렌더링되는지 브라우저로 확인한다.
