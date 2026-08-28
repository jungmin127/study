# 장세 판별 ML — 마켓 확장(B) + UI 단순화 설계

날짜: 2026-08-29
백로그: `docs/regime-ml-backlog.md` "B. 마켓(코인) 확장" (우선순위 A2→E→A1→**B**→C 중 4번째)

## 배경

`engine/regime_ml_constants.py:TRAINING_MARKETS`가 KRW-BTC/ETH/XRP 3개로 고정되어
있었다. 이번 세션에서 사용자가 학습 대상을 14개로 확장하기로 했고, 겸사겸사
`/regime` 페이지 UI도 정리하기로 했다(과거 3개 한정일 때는 코인선택/봉데이터
버튼이 "언젠가 전체 마켓/타임프레임을 지원할 수도 있다"는 여지를 열어뒀지만,
`docs/regime-ml-backlog.md`의 "D. 의도적으로 범위 밖에 둔 것"에 멀티 타임프레임과
전체 마켓 학습이 이미 명시적으로 비범위로 확정되어 있어 그 여지가 실제로는 없다).

## 범위

### 1. 학습 마켓 확장 (3개 → 14개)

```
기존: KRW-BTC, KRW-ETH, KRW-XRP
신규: KRW-SOL, KRW-DOGE, KRW-LINK, KRW-ADA, KRW-XLM, KRW-TRX,
      KRW-TRUMP, KRW-BCH, KRW-BSV, KRW-QTUM, KRW-ALGO
```

- `engine/regime_ml_constants.py:TRAINING_MARKETS`를 14개 리스트로 갱신한다. 이
  값이 학습 스크립트(`scripts/train_regime_ml.py`)와 추론
  (`backend/regime_ml_service.py`)의 유일한 소스라서, 다른 백엔드 코드 변경은
  필요 없다(2026-08-27 리팩터로 이미 단일화됨).
- `frontend/components/RegimeMlCurrentPrediction.tsx:TRAINED_MARKETS`도 정확히
  같은 14개로 갱신한다 — `tests/test_regime_ml_constants_frontend_sync.py`가
  두 배열이 정렬 기준 일치하는지 이미 자동으로 감시하고 있으므로, 하나만 빠뜨리면
  이 테스트가 실패해서 바로 잡힌다.

### 2. 재학습 & 검증

- 코드 변경 후 로컬에서 `/regime` 관리자 패널의 "학습 시작"(또는
  `scripts/train_regime_ml.py` 직접 실행)으로 14개 마켓 기준 모델을 1회 재학습한다.
- 실측할 것: 소요 시간(기존 3마켓 기준 2~3분 — 14마켓이면 늘어날 것으로 예상되나
  VPVR 등 순수 파이썬 지표가 마켓 수에 비례해 느려질 수 있어 정확한 배수는
  실측 전엔 모름), 풀링 상관계수가 기존(3마켓, 약 0.07~0.08) 대비 유지/악화되는지.
- [[upbit-v1-dont-push-on-empirical-regression]] 원칙 적용: 실측 결과가 명백히
  악화되면(예: 풀링 상관계수가 크게 떨어지면) 사용자 승인 없이 배포하지 않는다 —
  배포는 어차피 `/regime` 관리자 패널에서 사람이 성능 보고 선택하는 별도 단계라
  자연히 이 절차를 따른다.
- 신규 상장 코인(특히 KRW-TRUMP)은 캔들 히스토리가 짧을 수 있다. 학습 파이프라인은
  이미 fold별 `min_train_samples` 가드로 표본 부족 fold를 건너뛰므로, 별도 코드
  대응은 필요 없다 — 다만 실측 로그에서 해당 마켓이 초반 fold에 거의 기여하지
  못하는지 정도만 확인한다.

### 3. UI 단순화

**봉데이터 선택 제거**: `docs/regime-ml-backlog.md` D 섹션에 멀티 타임프레임 지원이
이미 비범위로 명시되어 있어, 8개 중 7개가 항상 "1시간봉 전용입니다" 에러만 내는
버튼 그룹을 유지할 이유가 없다. `RegimeDashboard.tsx`에서 `timeframe` state와
`TIMEFRAME_OPTIONS` 버튼 렌더링을 제거하고, `'minutes60'`을 상수로 고정해
`RegimeMlCurrentPrediction`에 그대로 넘긴다.

**코인 선택을 14개로 한정**: 기존 `CoinSelect`(전체 업비트 마켓 검색 드롭다운)를
그대로 두되 `markets` prop만 필터링하는 방식은 **쓸 수 없다** — 조사 결과
`CoinSelect`는 팝오버가 열릴 때(`handleOpenChange`) 내부적으로 `getMarkets()`를
다시 호출해 `liveMarkets`를 **전체 마켓으로 덮어쓴다.** 즉 부모가 아무리 14개로
필터링한 `markets` prop을 넘겨도, 사용자가 드롭다운을 한 번이라도 열면 필터가
무시되고 전체 목록이 다시 나타난다. 이건 다른 탭(백테스트 설정 등)에서
`CoinSelect`가 항상 전체 마켓을 다뤄야 하기 때문에 의도된 동작이라, 공용 컴포넌트를
고치는 대신 **이 페이지 전용의 작은 버튼 그룹을 새로 만든다**(기존 "봉데이터"
버튼 그룹과 같은 패턴 — `RegimeDashboard.tsx`가 이미 갖고 있는 `markets`
state를 `TRAINED_MARKETS`로 필터링해서 버튼 14개를 렌더링, 한글명 표시). 검색/정렬/
실시간 시세 같은 `CoinSelect`의 부가 기능은 이 용도엔 불필요하다고 판단해
가져오지 않는다(14개면 버튼 그룹만으로 스캔하기 충분).

**레이아웃 — ML 현재예측(좌) / 모델 성능(우) 2단**: `RegimeMlCurrentPrediction.tsx`의
데이터 로드 성공 시 렌더링 블록을 `grid grid-cols-1 md:grid-cols-2 gap-6`로
감싸, 왼쪽엔 예측 카테고리/확신도/카테고리별 확률 막대/봉·모델 시각 안내를,
오른쪽엔 "모델 성능"(fold 표/풀링 상관계수/hit-rate)을 배치한다. 좁은 화면(모바일)
에서는 `md:` 브레이크포인트 아래로 자동 세로 스택. 로딩/에러/미학습 마켓 안내
메시지는 지금처럼 전체 폭 한 줄로 유지(분할할 이유 없음).

**ML 재학습 관리자 패널**: 변경 없음(사용자 확인대로 로컬 전용 유지).

## 비범위

- 전체 업비트 마켓 학습(비용 대비 효과 없음, 기존 결정 유지)
- 1시간봉 외 타임프레임 지원
- `CoinSelect` 공용 컴포넌트 자체의 동작 변경(다른 탭에 영향 주지 않기 위해 건드리지 않음)
- 신규 코인들의 상장 초기 데이터 품질에 대한 별도 검증 로직(기존 표본부족 가드로 충분하다고 판단)

## 테스트 영향

- `tests/test_regime_ml_constants_frontend_sync.py`: 코드 변경 없이 자동으로
  14개 일치 여부를 검증(이미 존재하는 가드).
- `tests/test_regime_ml_service.py::test_predict_current_ml_regime_rejects_untrained_market`가
  "학습 안 된 마켓" 예시로 `KRW-DOGE`를 쓰고 있는데, DOGE가 이번에 학습 대상에
  추가되므로 이 테스트가 깨진다 — 예시 마켓을 14개 목록에 없는 다른 코드(예:
  `KRW-ETC`)로 교체한다.
- `tests/test_backend.py::test_regime_ml_current_prediction_returns_400_for_unsupported_market`는
  `predict_current_ml_regime` 자체를 mock으로 대체해서 실제 `TRAINING_MARKETS`
  값과 무관하게 동작한다 — 변경 불필요(확인 완료).
- `tests/test_train_regime_ml.py`의 `seeds = {"KRW-BTC": ..., "KRW-ETH": ...,
  "KRW-XRP": ...}` 픽스처들은 `train_and_evaluate()`에 직접 넘기는 독립적인
  테스트용 마켓 목록이라 실제 `TRAINING_MARKETS`와 무관 — 변경 불필요(확인 완료).
- 전체 pytest + frontend tsc --noEmit으로 최종 확인.

## 완료 기준

1. `TRAINING_MARKETS`/`TRAINED_MARKETS` 14개로 일치, 동기화 가드 테스트 통과.
2. 로컬 재학습 1회 성공, 소요 시간/풀링 상관계수 실측 기록.
3. `/regime` 페이지: 봉데이터 버튼 없음, 코인 선택이 14개로 한정된 버튼 그룹,
   ML 현재예측/모델 성능이 좌우 2단(모바일에서는 세로 스택).
4. 전체 pytest + tsc --noEmit 클린.
5. 사용자가 실측 결과 보고 배포 여부 결정(회귀 시 미배포).
