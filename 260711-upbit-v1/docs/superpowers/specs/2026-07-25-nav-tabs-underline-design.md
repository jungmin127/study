# 탭 UI 개선: 언더라인 스타일

## 문제
탭 3개(백테스트 설정/백테스트 결과/분석)가 `grid-cols-3` 블록 스타일이라, 비활성 탭끼리(회색 배경) 시각적으로 구분되지 않는다.

## 변경
`frontend/components/NavTabs.tsx`를 언더라인 탭 스타일로 교체한다.

- 부제(subtitle)는 모든 탭에서 제거. `STEPS`는 `{ href, title }`만 남긴다.
- 컨테이너: `grid grid-cols-3` → `flex gap-6 border-b px-6` (좌측 정렬, 여백으로 탭 사이 구분).
- 각 탭: `py-3 border-b-2` 고정, 활성 탭은 `border-primary font-semibold text-foreground`, 비활성 탭은 `border-transparent text-muted-foreground hover:text-foreground`.
- 배경색(bg-primary/bg-slate-100)은 제거 — 밑줄과 글자색만으로 활성/비활성을 구분.

## 비범위
- 탭 목록/라우팅 구조 변경 없음(경로 그대로).

## 검증
- 브라우저에서 세 페이지를 순회하며 현재 탭에 파란 밑줄 + 굵은 글씨가 표시되고, 나머지는 회색 텍스트로 명확히 구분되는지 확인한다.
