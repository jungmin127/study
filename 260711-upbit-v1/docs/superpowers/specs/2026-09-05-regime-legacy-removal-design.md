# 장세 판별 레거시(ML + 추세기반 세그먼트) 전면 삭제 설계 스펙

## 배경

[[upbit-v1-regime-ml-hmm-unsupervised-clustering]]까지 이번 세션에서 시도한
5개 방향(피처/모델/horizon/메타레이블링/HMM)이 전부 미래 장세를 예측하려다
실패했다. 사용자가 근본 방향을 재검토한 결과, "미래를 예측하지 말고 현재
장세만 규칙기반(ADX+방향지표)으로 판별해 코인별로 미리 준비한 3개 전략
(하락/횡보/상승용) 중 하나로 자동 전환(수동 개입 가능)"하는 방향으로 전면
피벗하기로 했다.

이 새 방향은 4단계로 나뉜다:
1. **레거시 삭제**(이 스펙의 범위) — ML 장세판별 + 추세기반 세그먼트 전면 삭제
2. ADX 기반 장세 판별 엔진(과거+현재) + "장세 판별" 탭 재구축(그리드서치
   프리필 복사 포함) — 별도 세션
3. 코인별 하락/횡보/상승 전략 3개 매핑 관리 UI — 별도 세션
4. daemon 자동 스왑 루프(끈백질 방지, 오픈포지션 대기, 자동/수동 스위치) —
   별도 세션, 실거래에 직접 영향을 주는 가장 리스크 높은 단계

**이 세션에서는 1단계(레거시 삭제)의 설계까지만 진행**하고, 실제 구현은
다음 세션에서 별도로 진행한다.

**삭제 이유(사용자 결정)**: 이 프로젝트는 AWS 라이브 서버(RAM 2GB급 인스턴스,
과거 OOM 이력 있음)에서 상시 가동되고 로컬에서도 그리드서치 백테스트를
자주 돌리므로, 더 이상 쓰지 않을 무거운 의존성(`lightgbm`, `hmmlearn`,
`scikit-learn`)과 코드를 프론트만 숨기는 대신 완전히 걷어내 가볍게
유지한다. `deploy/update.sh`가 배포마다 `pip install -r requirements.txt`를
재실행하므로 의존성 목록을 줄이면 배포 시간도 줄어든다.

## 목표

ML 장세판별 시스템(`/regime` 탭)과 추세기반 세그먼트(`/analysis` 탭의
"추세 기반" 섹션)를 백엔드/엔진/스크립트/테스트/프론트엔드/의존성/서버
파일까지 전부 삭제한다. 세그먼트 탭의 "규모"/"섹터" 섹션은 완전히 무관한
기능이라 그대로 유지한다.

## 비범위

- 2~4단계(ADX 엔진, 전략 라이브러리 UI, 자동 스왑) — 별도 세션에서 각각
  브레인스토밍부터 다시 시작
- **설계/계획 문서 삭제** — `docs/superpowers/specs/2026-*-regime-ml-*`,
  `docs/superpowers/plans/2026-*-regime-ml-*`, `2026-08-16-trend-segment-*`
  등 과거 설계·계획 문서는 이력으로 그대로 남긴다(2026-08-28 규칙기반
  제거 때와 동일한 관례 — "과거 설계 문서는 그 시스템을 설명하는 이력
  문서로 그대로 남긴다")
- `docs/ML_Regime_Switching_Improvement_Plan.md` 등 세션 시작 시점부터
  이미 커밋 안 된 채 작업 디렉터리에 있던 참고 문서 3개 — 이번 스펙과
  무관하게 존재하던 파일이라 건드리지 않는다
- 라이브 전략(`trading/`), 그리드서치, 백테스트 엔진(`engine/condition_tree.py`
  등 지표 레지스트리), 저널/캘린더 — 전부 이번 삭제 대상과 무관함을 아래
  "확인된 안전성"에서 검증 완료

## 확인된 안전성 (삭제 전 의존성 조사 결과)

`grep -rl "regime_ml"`(42개 파일)과 `grep -rl "trend_segments"`(6개 파일)로
전체 저장소를 조사해 각 파일의 실제 역할과 외부 의존 여부를 확인했다:

- **`engine/regime_features.py`**: docstring은 "백테스트/그리드서치/라이브
  데몬 어디서든 재사용 가능"이라고 주장하지만, 실제 import는
  `engine/regime_ml_data.py`/`engine/regime_ml_features.py`(둘 다 삭제
  대상)와 자체 테스트뿐 — 실제로는 한 번도 재사용된 적 없어 안전하게
  같이 삭제 가능.
- **`scripts/scan_candle_gaps.py`**: `engine.regime_ml_constants.TRAINING_MARKETS`만
  가져다 쓰는데, 이 스크립트 자체가 "Triple Barrier 라벨링 왜곡 확인"이라는
  ML 파이프라인 전용 데이터검증 목적으로 만들어진 것이라 존재 이유가 같이
  사라짐 — 별도 마켓 리스트로 살리지 않고 스크립트째 삭제.
- **`scikit-learn`**(requirements.txt)**: `grep -rl sklearn`에서 regime_ml
  계열 스크립트/테스트 외 사용처 없음 확인 — `lightgbm`/`hmmlearn`과 함께
  안전하게 제거 가능.
- **`engine/regime_ml_cross_sectional.py`/`regime_ml_calibration.py`**:
  docstring이 명시적으로 `scripts/train_regime_ml.py`+
  `backend/regime_ml_service.py`에만 묶여 있음을 확인.
- **`backend/regime_fact_service.py`**: `engine/regime_ml_labels.py`의
  `compute_triple_barrier_labels()`(ML 아님, 순수 라벨링 함수)를 가져다
  `/regime` 탭의 fact 구간 뷰어 전용으로만 쓴다 — 삭제 대상인
  `regime_ml_labels.py`가 없어져도 이 파일 자체가 같이 삭제되므로 문제
  없음.
- **`engine/trend_segments.py`**은 `engine/segment_analysis.py::_compute_volatility`를
  가져다 쓰지만, `segment_analysis.py` 자체는 유지 대상인 "세그먼트(규모)"
  기능이 별도로 쓰고 있어 **파일은 삭제하지 않고 trend_segments.py의
  import만 없어짐**(segment_analysis.py는 손대지 않는다).

## A. 삭제 대상 — 백엔드/엔진 (15개 파일: engine 12개 + backend 3개)

```
engine/regime_ml_features.py
engine/regime_ml_labels.py
engine/regime_ml_splits.py
engine/regime_ml_metrics.py
engine/regime_ml_data.py
engine/regime_ml_constants.py
engine/regime_ml_hmm.py
engine/regime_ml_cross_sectional.py
engine/regime_ml_calibration.py
engine/regime_math.py
engine/regime_features.py
engine/trend_segments.py
backend/regime_ml_service.py
backend/regime_ml_training_service.py
backend/regime_fact_service.py
```

## B. 삭제 대상 — 스크립트 (11개, 존재 이유가 같이 사라지는 `scan_candle_gaps.py` 포함)

```
scripts/train_regime_ml.py
scripts/train_regime_ml_meta_label.py
scripts/tune_regime_ml_horizon.py
scripts/tune_regime_ml_hyperparams.py
scripts/compare_regime_ml_baseline.py
scripts/analyze_regime_fact_performance.py
scripts/analyze_regime_hmm_fact_performance.py
scripts/validate_hmm_feature.py
scripts/select_barrier_k.py
scripts/push_regime_ml_model.sh
scripts/scan_candle_gaps.py
```

## C. `backend/main.py` 정리

**삭제할 import** (7곳): `engine.cache`의 `create_regime_ml_job`/
`finish_regime_ml_job`/`get_regime_ml_job`/`list_regime_ml_jobs`,
`engine.trend_segments`의 `EARLIEST_CANDLE_START`/`get_or_compute_trend_segments`,
`backend.regime_ml_service`, `backend.regime_fact_service`,
`backend.regime_ml_training_service`

**삭제할 헬퍼/엔드포인트**: `_fail_orphaned_regime_ml_jobs`/
`_cleanup_orphaned_regime_ml_jobs`/`_regime_ml_job_response`/
`_trend_segment_ohlcv`, 그리고 아래 12개 라우트:
`GET/POST /api/v1/trend-segments`(및 refresh), `GET /api/v1/regime/fact-segments`,
`GET /api/v1/regime/ml-current-prediction`, `GET /api/v1/regime/ml-train-enabled`,
`POST /api/v1/regime/ml-train`, `GET /api/v1/regime/ml-train/jobs`,
`GET /api/v1/regime/ml-models`, `POST /api/v1/regime/ml-deploy`
(`DeployRegimeMlModelRequest` 모델 포함)

## D. `engine/cache.py` 정리

- `CREATE TABLE trend_segments` + 관련 인덱스, `CREATE TABLE regime_ml_jobs`
  스키마 정의 삭제
- 함수 6개 삭제: `save_trend_segments`/`list_trend_segments`,
  `create_regime_ml_job`/`finish_regime_ml_job`/`_row_to_regime_ml_job_dict`/
  `get_regime_ml_job`/`list_regime_ml_jobs`

## E. 프론트엔드

**컴포넌트 삭제 (9개)**: `RegimeDashboard.tsx`, `RegimeFactSegmentView.tsx`,
`RegimeFactSegmentTable.tsx`, `RegimeFactChart.tsx`,
`RegimeMlCurrentPrediction.tsx`, `RegimeMlAdminPanel.tsx`,
`TrendSegmentView.tsx`, `TrendSegmentTable.tsx`, `TrendSegmentChart.tsx`

**`NavTabs.tsx`**: `{ href: '/regime', title: '장세 판별', icon: Activity }`
항목 삭제. **`frontend/app/regime/` 라우트 디렉터리 전체 삭제**(2단계에서
새 내용으로 새로 만들 예정이라 지금은 빈 페이지로 남기지 않는다).

**`AnalysisSidebarView.tsx`**: `SECTIONS` 배열에서 `{ key: 'trend', label:
'추세 기반', ... }` 항목만 제거(size/sector는 유지), `TrendSegmentView`
import와 `TrendingUp` 아이콘 import 제거, `section === 'trend'` 분기 제거.

**`frontend/lib/api/eda.ts`, `frontend/lib/types/eda.ts`**: regime_ml/
trend_segment/regime_fact 관련 API 함수·타입(`RegimeCategory`,
`MlCurrentPrediction`, `MlFoldPerformance`, `MlModelPerformance`,
`RegimeFactSegment`, `TrendSegment` 등) 삭제.

## F. 테스트

`test_regime_ml_features.py`, `test_regime_ml_labels.py`,
`test_regime_ml_splits.py`, `test_regime_ml_metrics.py`,
`test_regime_ml_data.py`, `test_regime_ml_hmm.py`,
`test_regime_ml_cross_sectional.py`, `test_regime_ml_calibration.py`,
`test_regime_ml_service.py`, `test_regime_ml_training_service.py`,
`test_regime_ml_constants_frontend_sync.py`, `test_train_regime_ml.py`,
`test_regime_math.py`, `test_regime_features.py`, `test_trend_segments.py`,
`test_regime_fact_service.py` 전체 삭제. `test_backend.py`, `test_cache.py`는
해당 테스트만 부분 삭제(파일 자체는 다른 기능 테스트도 담고 있어 유지).

## G. 의존성/데이터 정리

- `requirements.txt`에서 `lightgbm>=4.0,<4.6`, `scikit-learn>=1.3`,
  `hmmlearn>=0.3,<0.4` 3줄 삭제
- 로컬 `data/regime_ml_models/` 디렉터리 전체 삭제
- **AWS 서버**(`.env`의 `DEPLOY_SERVER_HOST`/`DEPLOY_SSH_KEY_PATH` 사용)
  SSH 접속 후 `/opt/study/260711-upbit-v1/data/regime_ml_models/` 삭제.
  서버는 현재 이 코드 이전 커밋(`94eceea`)에 있으므로, 코드 배포
  (`deploy/update.sh`) 시점에 맞춰 같이 정리한다 — 배포 전 반드시
  [[upbit-v1-deploy-check-open-positions-first]] 원칙대로 오픈 포지션
  확인.
- `venv`에 이미 설치된 `lightgbm`/`hmmlearn`/`scikit-learn` 자체를
  `pip uninstall`할지는 다음 세션 배포 시점에 판단(디스크 용량이 급하지
  않으면 다음 `pip install -r requirements.txt`가 어차피 이 패키지들을
  재설치하지 않게 되는 것만으로 충분 — 강제 uninstall은 선택사항).

## H. 문서화

`docs/regime-ml-backlog.md` 맨 위에 새 절 추가 — "이 방향(지도학습 ML
장세판별) 전체 폐기, 코드 삭제 완료, 다음은 규칙기반(ADX) 현재장세 판별로
전면 피벗" 요약과 이 스펙 문서로의 링크. 과거 라운드 기록(하락/실패 5연속
등)은 그대로 보존 — 이 문서 자체를 삭제하지 않는다.

## 검증 계획

- 삭제 후 `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
  전체 통과(알려진 무관 flake 1건 제외) — import 누락/깨진 참조가 없다는
  가장 확실한 증거
- `cd frontend && npm run build` 성공(삭제된 컴포넌트를 참조하는 곳이
  하나도 안 남았는지 확인)
- 로컬에서 `uvicorn backend.main:app`이 에러 없이 뜨는지 수동 확인
  (import 시점에 죽는 실수 방지)
- AWS 배포는 이번 스펙 구현이 끝나고 사용자 승인 하에 별도로 진행(코드
  배포 시 기존 원칙대로 오픈 포지션 확인 선행)
