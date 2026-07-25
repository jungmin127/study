# 분석 탭 세부 섹션 구조

## 목적
`/analysis` 페이지 안에 분석주제별 섹션을 두어, 이후 서버 기동 시 배치로 계산된 최신 데이터를 섹션별로 채울 수 있는 자리를 마련한다. 이번 작업은 화면 구조만 만들고 값은 비워둔다.

## 범위
- `frontend/app/analysis/page.tsx`를 섹션 목록 렌더링으로 교체.
- 섹션 정의를 배열(`ANALYSIS_SECTIONS`)로 관리해 향후 새 분석주제(중분류) 추가가 쉽도록 함.
  ```ts
  const ANALYSIS_SECTIONS = [
    { key: 'segment-size', title: '세그먼트(규모)' },
    { key: 'segment-sector', title: '세그먼트(섹터)' },
  ];
  ```
- 각 섹션은 `components/ui/card.tsx`의 `Card`/`CardHeader`/`CardTitle`/`CardContent`를 사용해 카드로 렌더링. 본문은 "준비 중입니다." placeholder.
- 섹션들은 페이지 안에서 세로로 나열(`flex flex-col gap-4` 등).

## 비범위
- 배치 실행(서버 기동 훅), 세그먼트 계산 로직, 분석 API 엔드포인트는 포함하지 않는다(후속 작업).
- 하위 탭/라우팅 분리는 하지 않는다.

## 검증
- 브라우저에서 `/analysis` 접속 시 "분석" 제목 아래 "세그먼트(규모)", "세그먼트(섹터)" 카드 2개가 세로로 나열되고 각각 "준비 중입니다."가 표시되는지 확인한다.
