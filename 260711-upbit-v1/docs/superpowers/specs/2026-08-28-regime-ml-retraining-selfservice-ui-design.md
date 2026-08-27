# 장세 판별 ML 재학습 셀프서비스 UI 설계

`docs/regime-ml-backlog.md`의 최종 우선순위(A2 → E → A1 → B → C) 중 3순위 A1("재학습
자동화")에 대한 설계. 백로그 문서 자체가 미결정 사항으로 남겨둔 세 가지(증분학습 vs
스크래치 재학습, 트리거 방식, 자동배포 vs 사람 승인)를 브레인스토밍을 통해 아래처럼
확정했다.

## 배경 및 결정 경위

- 지금은 `scripts/train_regime_ml.py`를 터미널에서 수동 실행 → 콘솔에 출력되는 fold별
  상관계수를 사람이 눈으로 확인 → 괜찮다 싶으면 `scripts/push_regime_ml_model.sh`를
  수동 실행해 AWS 라이브 서버로 모델을 올리는 구조.
- 처음엔 "재학습 자동화"를 Windows Task Scheduler로 주기적(예: 주 1회) 실행하는 안을
  검토했으나, 사용자가 "학습도 제가 원할 때 누르면 되게, 배포도 제가 원할 때 하게"로
  방향을 바꿨다 — 학습 결과가 마음에 안 들면 새 모델을 배포하지 않고 기존 모델을
  계속 쓰는 판단을 사람이 직접 해야 하기 때문. 이에 따라 **cron형 자동화는 폐기하고,
  웹 UI에서 버튼으로 학습/배포를 온디맨드 실행하는 방식**으로 확정했다(기존 Grid
  Search 웹 탭과 동일한 셀프서비스 패턴).
- 학습 위치(로컬 vs AWS)는 실측으로 결정했다: 로컬에서 `train_regime_ml.py`를 실제
  실행해보니 3마켓 walk-forward 전체가 **약 2.5분, 베이스라인 대비 피크 메모리
  +350~400MB**로 끝났다. grid search를 OOM 낸 멀티프로세스 Cerebro 백테스트와는
  스케일이 다르지만, 그렇다고 AWS(`t4g.small`, 2GB RAM, daemon/backend/frontend가
  이미 상시 실행 중)에서 실행할 실익도 크지 않다고 판단해 **학습은 항상 로컬, 배포만
  AWS로 전송**하는 기존 grid search와 같은 워크플로를 그대로 따르기로 했다.
- 증분학습(LightGBM `init_model`)은 학습 시간 단축이 목적인데, 스크래치 재학습이
  이미 2.5분으로 충분히 가벼워서 증분학습 도입의 이점이 없다고 판단, **스크래치
  재학습을 그대로 유지**한다.

## 범위

**포함**
- `/regime` 탭에 관리자 패널(학습 시작 버튼 + 모델 목록/배포 테이블) 추가
- 로컬에서 버튼으로 `train_regime_ml.py` 실행 → 진행 상태 확인 → 완료 후 사이드카
  성능 지표(A2에서 이미 저장 중인 `pooled_correlation`/`pooled_hit_rate`)를 모델
  목록에서 확인 → 원하는 시점의 모델을 골라 AWS 라이브로 배포
- AWS에 잘못 배포되는 사고(과거 grid search가 실제로 겪은 OOM 사고, `upbit-v1-grid-
  search-local-only-workflow` 메모리 참고)를 막기 위한 환경변수 기반 기능 게이트

**비범위(별도 백로그/향후 논의)**
- Task Scheduler/cron 기반 정기 자동 재학습 — 이번에 온디맨드 버튼 방식으로 대체하기로
  하면서 폐기
- 증분학습(`init_model`) — 스크래치 재학습 유지로 확정, 재검토 불필요
- "장세 예측 → 그 장세에 맞는 백테스트 전략으로 실시간 자동 전환" — 완전히 별개의
  기능(현재 `trading/`은 regime 관련 코드를 전혀 참조하지 않음). 이번 설계와
  무관하며 별도 프로젝트로 브레인스토밍 필요
- 학습 작업 취소(cancel) 버튼 — 전체 실행이 2.5분 내외로 짧아 우선순위 낮음, 필요해
  지면 후속 추가

## 아키텍처

Grid Search 웹 탭이 이미 쓰고 있는 패턴(subprocess + 스레드 + DB job 테이블 + 프론트
폴링, `backend/grid_search_service.py` + `engine/cache.py`)을 그대로 재사용한다.

### 백엔드

**DB (`engine/cache.py`)**
- `regime_ml_jobs` 테이블 추가: `id`(job_id), `status`(running/completed/failed),
  `started_at`, `finished_at`, `error_message`. `grid_search_jobs`와 같은 컬럼
  스타일을 따르되, 학습 결과(모델 파일 자체)는 DB가 아니라 파일시스템
  (`data/regime_ml_models/`)이 소스오브트루스이므로 job 테이블엔 실행 상태만 남긴다.
- 백엔드 프로세스 시작 시 "running"으로 멈춰있는 job을 "failed"로 정리하는 로직
  추가(`backend/main.py`의 `_fail_orphaned_grid_search_jobs`와 동일한 목적 —
  Windows 개발 서버가 강제 종료되면 job이 고아 상태로 남는 기존에 확인된 문제 패턴).

**학습 오케스트레이션 (새 모듈, 예: `backend/regime_ml_training_service.py`)**
- `start_job()`: 이미 실행 중인 job이 있으면 거부(grid search의
  `JobAlreadyRunningError`와 동일한 단일 슬롯 방식). 없으면
  `python scripts/train_regime_ml.py`를 subprocess로 실행하고 job row를 생성,
  완료/실패 시 갱신하는 백그라운드 스레드를 띄운다.
- `get_job_status(job_id)` / 최신 job 조회: 프론트가 폴링할 상태(running/
  completed/failed, error_message)를 반환.
- fold별 세부 진행률 파싱(grid search의 `_parse_progress_line`류)은 하지 않는다 —
  전체 실행이 짧아 "실행 중" 스피너만으로 충분하다고 판단(YAGNI).

**모델 목록 API**
- `data/regime_ml_models/*.json` 사이드카 전체를 스캔해 학습 시각순으로 정렬,
  각각의 `performance.pooled_correlation`/`performance.pooled_hit_rate`를 반환.
  `find_latest_model()`이 이미 쓰는 타임스탬프 파싱 로직(`backend/
  regime_ml_service.py:_parse_trained_at`)을 재사용한다.
- 마지막으로 배포에 성공한 모델의 타임스탬프를 `data/regime_ml_models/
  .last_deployed.json`(`{"model_timestamp": "...", "deployed_at": "..."}`) 마커
  파일에 남겨 목록에 "현재 배포됨" 뱃지를 표시한다 — AWS에
  실시간으로 물어보지 않고도 로컬에서 바로 "새로 학습한 모델이 지금 배포된 것보다
  나은가"를 비교할 수 있게 한다. 이 마커는 신뢰 소스가 아니라 참고용 표시임을
  명확히 한다(예: 배포 후 AWS에서 수동으로 모델을 되돌리면 마커와 실제 상태가
  어긋날 수 있음 — 그런 경우까지 동기화하는 건 비범위).

**배포 API**
- `scripts/push_regime_ml_model.sh`를 확장해 선택적 인자(모델 베이스네임, 예:
  `regime_ml_20260827T223633Z`)를 받도록 한다. 인자가 없으면 기존과 동일하게
  최신 파일을 찾는다(하위 호환).
- 배포 API는 사용자가 모델 목록에서 고른 특정 타임스탬프를 이 스크립트의 인자로
  넘겨 subprocess로 실행하고, 성공/실패(exit code, stderr)를 그대로 응답으로
  전달한다. 성공 시 "마지막 배포됨" 마커를 갱신한다.

**안전장치**
- `.env`에 `ENABLE_ML_TRAINING_UI` 추가(로컬 `.env`만 `true`, `.env.example`엔
  주석 처리된 채로 둠 — 안내 문구만 추가). AWS `.env`는 `.env.example`을 복사해서
  만들어지므로(`deploy/setup.sh`) 별도 조치 없이 기본 비활성 상태를 유지한다.
  `deploy/README.md`/`deploy/UPDATE.md`에 "이 플래그를 AWS에서 켜지 말 것"을
  명시한다.
- 학습 시작/배포 엔드포인트 둘 다 이 플래그를 검사해 꺼져 있으면 403을 반환한다
  (프론트가 버튼을 숨기는 것과 별개로 API 자체도 막는 이중 안전장치).
- 플래그 조회용 엔드포인트(예: `GET /api/v1/regime/ml-train-enabled`)를 추가해
  프론트가 마운트 시 조회, 버튼 노출 여부를 결정한다.

### 프론트엔드

- `/regime` 탭에 관리자 패널 컴포넌트를 추가한다(플래그가 꺼져 있으면 아예 렌더링하지
  않음).
- "학습 시작" 버튼: 클릭 시 job 시작 API 호출 → 진행 중엔 스피너 표시, 완료/실패
  시까지 폴링(그리드서치 탭의 폴링 패턴 재사용) → 완료되면 모델 목록을 자동
  갱신한다.
- 모델 목록 테이블: 학습시각 / `pooled_correlation` / `pooled_hit_rate` / 현재
  배포됨 뱃지 / 배포 버튼. 배포 버튼 클릭 시 확인 다이얼로그(실거래에 영향을 주는
  변경이므로) 후 배포 API를 호출하고 결과(성공/실패 메시지)를 표시한다.

## 에러 처리

- 이미 학습 job이 진행 중일 때 새 학습 요청 → 409, 기존 job 상태를 그대로 보여줌.
- `train_regime_ml.py` subprocess가 비정상 종료(캔들 부족, 예외 등) → job을
  failed로 남기고 stderr 마지막 줄(또는 요약)을 `error_message`에 저장, 프론트에
  그대로 노출.
- 백엔드 재시작 시 "running"으로 멈춰있던 job은 시작 시점에 failed로 정리(고아
  job이 이후 학습 요청을 영구히 막지 않도록).
- 배포 실패(SSH 키/호스트 미설정, 네트워크 끊김, scp 실패) → 스크립트 exit
  code/stderr를 그대로 API 에러로 전달. `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST`가
  없는 환경(AWS 쪽 `.env`)에서는 스크립트 자체가 이미 이 값들을 검사해 명확한
  에러 메시지와 함께 종료하므로 별도 처리 불필요.
- `ENABLE_ML_TRAINING_UI`가 꺼진 상태에서 엔드포인트를 직접 호출하면(버튼이 없어도)
  403.

## 테스트 계획

- 백엔드: job 서비스 단위 테스트(정상 완료/실패/중복 실행 방지/재시작 시 orphan
  정리), 모델 목록 API(사이드카 파싱, 정렬, 배포 마커 반영), 배포 API(플래그
  검사, 특정 파일명 인자 전달), 플래그 꺼졌을 때 학습/배포 엔드포인트 403 확인.
- `push_regime_ml_model.sh`: 인자로 특정 모델 베이스네임을 지정했을 때 해당
  파일을 찾는지, 인자 없이 호출했을 때 기존 동작(최신 모델)이 그대로 유지되는지
  확인.
- 프론트: 플래그 off일 때 패널이 렌더링되지 않는지, 학습 진행 중 상태 표시,
  모델 목록 렌더링과 배포 버튼 클릭 흐름(확인 다이얼로그 포함) 테스트.
