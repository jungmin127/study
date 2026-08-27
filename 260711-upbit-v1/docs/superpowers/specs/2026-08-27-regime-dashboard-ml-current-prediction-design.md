# 장세 판별 대시보드 — ML 현재예측 카드 추가

## 배경

[[2026-08-27-regime-detector-ml-classifier-design.md]]에서 만든 LightGBM 파이프라인(`scripts/train_regime_ml.py`)이 규칙기반 대비 일관되게 높은 상관계수(fold별 0.06~0.16 vs 규칙기반 |r|≤0.04)를 실측으로 확인했다. 이번 작업은 이 모델을 `/regime` 대시보드에서 사람이 직접 볼 수 있게 붙인다.

## 목표

`/regime` 대시보드의 "현재 예측" 영역에 규칙기반 카드 옆에 ML 카드를 나란히 추가한다. 둘의 의견이 다를 때 한눈에 비교할 수 있게 하는 것이 핵심.

## 비범위

- 정확도 리포트/confusion matrix/과거 임의 구간 백테스트에 ML 포함하지 않는다. 저장된 모델은 워크포워드 fold 중 마지막 fold 하나뿐이라, 사용자가 그 모델의 훈련구간과 겹치는 과거 날짜를 조회하면 인샘플(모델이 이미 본 데이터) 결과가 나와 낙관적으로 보일 수 있다 — "현재"는 항상 모델 훈련 종료 시점 이후라 이 문제가 구조적으로 없다.
- 모델 자동 재학습/스케줄링 없음. `scripts/train_regime_ml.py`를 수동으로 다시 돌려야 최신 모델로 갱신된다.
- `minutes60`(1시간봉) 외 타임프레임 지원 없음 — 저장된 모델이 1시간봉으로만 학습됨.
- `engine/regime_detector.py`, 규칙기반 대시보드 로직 변경 없음.

## A. 백엔드

### A-1. `engine/regime_ml_data.py` (이동)

`scripts/regime_ml_data.py`(Task 4에서 작성)를 `engine/regime_ml_data.py`로 옮긴다. 최종 브랜치 리뷰가 남긴 Minor — 백엔드 코드가 `scripts.` 네임스페이스를 import하는 게 어색하다는 지적을 지금 해소한다. `load_market_training_data()`의 시그니처/동작은 변경 없음, import 경로만 바뀐다. `scripts/train_regime_ml.py`와 `tests/test_regime_ml_data.py`(파일명은 유지, 위치는 `tests/` 그대로)의 import 문을 새 경로로 갱신한다.

### A-2. `backend/regime_ml_service.py` (신규)

```python
def find_latest_model() -> tuple[Path, dict] | None:
    """data/regime_ml_models/에서 파일명 타임스탬프 기준 가장 최근 .txt+.json 페어를
    찾는다. 없으면 None."""

def predict_current_ml_regime(market: str, timeframe: str) -> dict:
    """market의 가장 최근 봉 하나에 대한 ML 예측을 반환한다.
    timeframe이 "minutes60"이 아니면 ValueError.
    저장된 모델이 없으면 FileNotFoundError.
    반환: {"predicted_category": str, "probs": dict[str, float],
           "model_trained_at": str(UTC ISO), "model_fold_index": int}"""
```

동작:
1. `timeframe != "minutes60"`이면 `ValueError("ML 모델은 1시간봉(minutes60)으로만 학습되어 있습니다")`
2. `find_latest_model()`이 `None`이면 `FileNotFoundError("학습된 ML 모델이 없습니다. scripts/train_regime_ml.py를 먼저 실행하세요")`
3. 최근 30일 캔들을 `engine.regime_ml_data.load_market_training_data(market, timeframe, now-30일, now)`로 가져온다. 30일(1시간봉 720개)은 가장 긴 지표 워밍업(`WARMUP_MULTIPLIER=5 × half_life_bars=24 = 120봉`, 또는 `LIVE_INDICATOR_FACTORY`의 최장 기본 period=50봉)의 약 6배 여유 — 안전마진.
4. `engine.regime_ml_features.build_feature_matrix(df, market, half_life_bars)`로 피처화, 마지막 행만 사용.
5. `lgb.Booster(model_file=model_path)`로 모델 로드, 마지막 행에 대해 `.predict()` → 확률벡터. 사이드카 JSON의 `classes` 리스트 순서와 그대로 매핑(학습 스크립트가 `model.classes_` 순서로 저장해뒀으므로 booster의 원시 출력 컬럼 순서와 일치함이 이미 확인됨).
6. `predicted_category` = 확률 최댓값의 라벨.
7. `model_trained_at`은 파일명의 타임스탬프(`regime_ml_YYYYMMDDTHHMMSSZ`)를 파싱해 ISO로 변환.

### A-3. API 엔드포인트

`backend/main.py`에 추가:

```python
@app.get("/api/v1/regime/ml-current-prediction")
def get_regime_ml_current_prediction_endpoint(
    market: str = Query(...),
    timeframe: str = Query(...),
) -> dict:
    try:
        return predict_current_ml_regime(market, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

기존 `/api/v1/regime/backtest`(무거운 백테스트 루프, 캐시 없음)와 완전히 분리된 가벼운 엔드포인트 — 최근 30일 데이터 하나만 다루므로 응답이 빠르다. 캐시 없음(규칙기반 현재예측과 동일 정책).

## B. 프론트엔드

### B-1. 타입 (`frontend/lib/types/eda.ts`)

```typescript
export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  model_trained_at: string;
  model_fold_index: number;
}
```

### B-2. API 클라이언트 (`frontend/lib/api/eda.ts`)

```typescript
export function getRegimeMlCurrentPrediction(params: {
  market: string;
  timeframe: string;
}): Promise<MlCurrentPrediction> {
  const query = new URLSearchParams(params);
  return apiFetch<MlCurrentPrediction>(`/api/v1/regime/ml-current-prediction?${query.toString()}`);
}
```

### B-3. `RegimeMlCurrentPrediction.tsx` (신규 컴포넌트)

`RegimeCurrentPrediction.tsx`와 같은 카드 레이아웃(`CATEGORY_ORDER`/`categoryVarName` 재사용 — 3개 차트 컴포넌트가 이미 이 상수들을 의도적으로 중복 정의하고 있는 기존 관례를 따름)을 쓰되:
- `market`/`timeframe`을 props로 받아 컴포넌트 자체가 `useEffect`로 `getRegimeMlCurrentPrediction`을 호출(부모가 데이터를 내려주는 `RegimeCurrentPrediction`과 달리, 독립적으로 로드 — 규칙기반 백테스트 결과와 생명주기가 다르므로)
- `timeframe !== 'minutes60'`이면 API 호출 없이 "ML은 1시간봉 전용입니다" 안내만 표시
- 404(모델 없음) 시 "학습된 ML 모델이 없습니다" 안내
- 로딩 중 스켈레톤 또는 "불러오는 중..." 텍스트
- 카드 하단에 `model_trained_at`/`model_fold_index`를 작은 텍스트로 표시(예: "2026-08-27 학습, fold 5 모델 기준")

### B-4. `RegimeDashboard.tsx` 수정

```tsx
<div className="grid gap-4 md:grid-cols-2">
  <RegimeCurrentPrediction result={result} market={market} timeframe={timeframe} />
  <RegimeMlCurrentPrediction market={market} timeframe={timeframe} />
</div>
```

기존 `<RegimeCurrentPrediction .../>` 단독 줄을 위 grid로 교체. `RegimeChart`/`RegimeAccuracyReport`는 변경 없음(비범위).

## 에러 처리 요약

| 상황 | 백엔드 | 프론트 |
|---|---|---|
| timeframe ≠ minutes60 | (호출 자체를 안 함) | "ML은 1시간봉 전용" 안내 |
| 모델 파일 없음 | 404 | "학습된 ML 모델이 없습니다" 안내 |
| 캔들 데이터 부족(워밍업 미달) | 정상 응답이되 predicted_category가 다수결로 나온 값 그대로(규칙기반과 달리 ML은 결측 허용이 설계 원칙이므로 별도 "판단불가" 상태 없음) | 그대로 표시 |
| 외부데이터(공포탐욕지수 등) 일부 결측 | 정상 응답(LightGBM 네이티브 NaN 처리) | 그대로 표시 |

## 테스트

- `tests/test_regime_ml_service.py`(신규): `find_latest_model`(빈 디렉터리/단일 파일/여러 파일 중 최신 선택), `predict_current_ml_regime`(timeframe 검증, 모델 없음 에러, monkeypatch로 가짜 데이터 로더+피처+더미 LightGBM 모델을 써서 확률벡터가 사이드카 `classes` 순서와 올바르게 매핑되는지)
- 프론트: 이 프로젝트 관례상 프론트 변경은 dev 서버 실행 후 브라우저로 실제 확인(Playwright) — 자동화 테스트 신규 작성은 기존 프론트 컴포넌트들의 관례를 따름(전용 단위테스트 없음, 수동/브라우저 검증)
- 회귀 확인: `engine/regime_ml_data.py` 이동 후 `tests/` 전체 스위트가 여전히 통과하는지(import 경로 변경 누락 없는지)
