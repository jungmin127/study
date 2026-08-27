# 장세 판별 ML 백로그 정리 — 설계 스펙

## 배경

[[2026-08-27-regime-detector-ml-classifier-design]]로 ML 분류기를 도입하고
`/regime` 대시보드에 통합한 뒤(`ff43b4e`까지 push됨), 세션 종료 시점에
사용자가 "당장 처리할 항목"으로 지정한 3개 백로그 항목을 다룬다. 상세 배경은
메모리 `upbit-v1-regime-ml-backlog` 참고.

## 비범위

- ML 정확도 리포트/confusion matrix/과거 백테스트 대시보드 노출 (v1 설계 문서에서
  이미 비범위로 확정, 인샘플 문제 미해결)
- 모델 자동 재학습/스케줄링
- 1시간봉 외 타임프레임 지원
- KRW-BTC/ETH/XRP 외 마켓 확장 (이번 작업은 확장을 "쉽게" 만들 뿐, 실제 확장은
  하지 않는다)
- `engine/regime_ml_splits.py`의 embargo 1봉 부족 이슈, `find_latest_model()`의
  손상된 JSON 사이드카 처리 등 나머지 자잘한 백로그 항목 (별도 세션)

## 1. OBV 스케일 불일치 해결

### 문제

`backend/regime_ml_service.py`는 추론 시 최근 30일(`WARMUP_DAYS`)만 로드하는데,
`OBV`(`trading/live_indicators.py:create_obv`, 모델 gain 기준 2위 중요도 11.4%)는
윈도우 없는 누적합(`cumsum`)이라 학습 시(2024-01-01~현재, 2.5년치)와 추론 시
스케일이 완전히 다르다. 크래시 없이 조용히 예측을 왜곡시킬 수 있다.

### 해결

`engine/regime_ml_features.py:build_feature_matrix()`가 `LIVE_INDICATOR_FACTORY`를
순회할 때 `"OBV"` 키를 제외한다. `"OBV_ROC"`(같은 레지스트리의 rolling window 기반
%지표, `create_obv_roc`)는 이미 별도 피처로 존재하며 스케일 문제가 없으므로 유지한다.

```python
features: dict[str, pd.Series] = {
    name: factory(df) for name, factory in LIVE_INDICATOR_FACTORY.items() if name != "OBV"
}
```

이 함수는 학습(`scripts/train_regime_ml.py`)과 추론(`backend/regime_ml_service.py`)
양쪽에서 호출되는 유일한 피처 생성 지점이므로, 여기 하나만 고치면 두 경로가 자동으로
동기화된다(별도 컬럼 필터링이 두 곳에 중복될 필요 없음).

### 영향받는 코드

- `backend/regime_ml_service.py:24-34`의 "알려진 한계" 코멘트 블록 — 문제가
  해결됐으므로 삭제하고, `WARMUP_DAYS=30`이 이제 OBV 문제와 무관하게 안전하다는
  점만 간단히 남긴다.
- `tests/test_regime_ml_features.py`의
  `test_build_feature_matrix_has_one_column_per_registered_indicator_plus_regime_features` —
  `expected_columns`가 `LIVE_INDICATOR_FACTORY.keys()` 전체와 정확히 일치해야 한다고
  단언하는데, OBV 제외 후에는 `LIVE_INDICATOR_FACTORY.keys() - {"OBV"}`가 되어야
  한다. 테스트명과 docstring도 갱신한다.

### 재학습 및 실측 검증

`scripts/train_regime_ml.py` 재실행 → 콘솔에 출력되는 fold별/전체 상관계수를
기존 기록값(fold r=0.06~0.16, 메모리 `upbit-v1-regime-ml-classifier` 참고)과 비교한다.

[[upbit-v1-dont-push-on-empirical-regression]] 원칙에 따라, 상관계수가 눈에 띄게
나빠지면 push하지 않고 사용자에게 먼저 보고한다. 개선되거나 동등하면 새로 저장된
모델(`data/regime_ml_models/regime_ml_<timestamp>.txt/.json`)을 그대로 둔다
(디렉터리는 gitignore 대상이라 커밋 대상 아님).

## 2. 마켓 목록 4중복 제거

### 문제

`backend/regime_ml_service.py`의 `_TRAINING_MARKETS`, `scripts/train_regime_ml.py`의
`MARKETS`, 프론트 `RegimeMlCurrentPrediction.tsx`의 `TRAINED_MARKETS` + 하드코딩된
안내 문구, 총 4곳이 각자 `["KRW-BTC", "KRW-ETH", "KRW-XRP"]`를 따로 유지한다.
마켓 확장 시 4곳을 전부 손으로 고쳐야 하고 동기화 장치가 없다.

### 해결

- 신규 `engine/regime_ml_constants.py`:
  ```python
  TRAINING_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP"]
  ```
- `backend/regime_ml_service.py`의 `_TRAINING_MARKETS` 정의를 제거하고
  `from engine.regime_ml_constants import TRAINING_MARKETS`로 교체(기존 사용처는
  이름만 맞춰 참조 변경).
- `scripts/train_regime_ml.py`의 `MARKETS` 정의를 제거하고 같은 방식으로 교체.
- 프론트 `TRAINED_MARKETS`는 API 왕복 없이 하드코딩 배열로 유지한다(이번 세션
  결정 — 새 엔드포인트/로딩 상태를 도입하지 않는다).
- 신규 가드레일 테스트 `tests/test_regime_ml_constants_frontend_sync.py`:
  `frontend/components/RegimeMlCurrentPrediction.tsx`를 읽어 정규식으로
  `TRAINED_MARKETS` 배열 리터럴을 추출하고, `engine.regime_ml_constants.TRAINING_MARKETS`
  (정렬 후 비교, 순서 무관)와 일치하는지 단언한다. 파일 경로를 프로젝트 루트 기준으로
  고정하고, 정규식이 매치 실패하면(파일 구조가 바뀌면) 명확한 실패 메시지를 낸다.

### 곁들여 정리

- `scripts/train_regime_ml.py`의 `MODEL_OUTPUT_DIR = Path("data/regime_ml_models")`는
  CWD 상대경로라, `backend/regime_ml_service.py`의 `MODEL_DIR`(이미 `Path(__file__)`
  기준으로 고쳐진 상태)과 비대칭이다. 같은 파일을 손대는 김에
  `Path(__file__).parent.parent / "data" / "regime_ml_models"`로 통일한다.

## 3. AWS 서버 배포 검증

`scripts/push_regime_ml_model.sh`는 로컬 최신 모델을 실서버(SSH/SCP)로 올리는 실제
배포 액션이다. 1번 작업으로 재학습된 새 모델이 준비된 뒤, 다음 절차로 진행한다:

1. `.env`에 `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST`가 설정되어 있는지 먼저 확인.
   없으면 그 사실만 보고하고 스크립트를 실행하지 않는다(설정은 사용자 몫).
2. 설정이 있으면, 실행 직전에 다시 한번 사용자 확인을 구한 뒤 실행한다(운영 서버에
   파일을 올리는 되돌리기 까다로운 액션이므로 자동 실행하지 않음).
3. 실행 결과(성공/실패, 전송된 파일명)를 그대로 보고한다.

이 항목은 새 코드를 작성하지 않는다 — 기존 스크립트를 처음으로 실사용해보는 검증
단계다.

## 테스트 계획

- `tests/test_regime_ml_features.py`: OBV 제외 반영(기존 테스트 수정)
- 신규 `tests/test_regime_ml_constants_frontend_sync.py`: 마켓 목록 동기화 가드
- `scripts/train_regime_ml.py` 재실행 결과(콘솔 출력)를 수동으로 확인 — 자동화된
  회귀 테스트 대상이 아님(실데이터 기반 학습이라 결정론적이지 않음)
- 기존 테스트 스위트 전체(`pytest`) 통과 확인

## 결정 요약

| 항목 | 선택 | 비고 |
|---|---|---|
| OBV 해결 방식 | 피처에서 제거(OBV_ROC로 대체) | 옵션 (a) 캐싱, (c) rolling sum 변경 대비 코드 변경 최소, 정보 손실 적음 |
| 마켓 목록 통합 범위 | 백엔드 2곳만 공유 모듈화, 프론트는 하드코딩 유지 | 새 API/네트워크 의존성 도입 안 함 |
| 프론트 동기화 보장 | 정규식 파싱 가드레일 테스트 | 완전한 단일 소스는 아니지만 드리프트 시 테스트 실패로 즉시 감지 |
| AWS 배포 | 스크립트 실행은 사용자 확인 후 진행 | 실서버에 영향을 주는 되돌리기 까다로운 액션 |
