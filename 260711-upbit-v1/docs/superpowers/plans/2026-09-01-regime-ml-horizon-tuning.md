# 장세 판별 ML — 예측 horizon(N_MULTIPLIER) 그리드서치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs/2026-09-01-regime-ml-horizon-tuning-design.md`에서 승인된 설계대로 `run_training()`에 `n_multiplier` 확장점을 추가하고, 라벨 horizon(N_MULTIPLIER) 후보 5개를 실데이터 walk-forward로 비교하는 진단 스크립트를 추가해 현재 값(2.5)이 kappa 기준 최선인지 확인한다.

**Architecture:** `scripts/train_regime_ml.py::run_training()`에 `n_multiplier: float = N_MULTIPLIER` 파라미터를 추가해 라벨 horizon(`n_bars`)만 바꾸고 피처 EWM 윈도우(`half_life_bars`)는 그대로 유지 → `scripts/tune_regime_ml_horizon.py`가 같은 `run_training()`을 5개 `n_multiplier` 후보로 반복 호출해 pooled weighted kappa를 비교.

**Tech Stack:** Python, pandas, LightGBM, pytest. 신규 의존성 없음(c-2에서 이미 확장된 `run_training()`의 `model_factory`/`preprocess_fold`/`save_model` 확장점 패턴을 그대로 재사용).

## Global Constraints

- 평가지표: `weighted_kappa`(pooled) 1순위, `macro_f1` 2순위. 현재 프로덕션 값(N_MULTIPLIER=2.5)의 kappa: **0.105**(c-2 baseline 비교 실측, `docs/regime-ml-backlog.md` 참고).
- `half_life_bars`(피처 EWM 윈도우, 라벨 변동성 EWM 윈도우)는 이번 스코프에서 바꾸지 않는다 — `n_multiplier`는 오직 `n_bars`(라벨 horizon)에만 영향을 줘야 한다.
- `barrier_k`는 5개 후보 전부 `BARRIER_K`(6.25, 현재 프로덕션 값)로 고정한다 — barrier_k와 horizon의 조인트 재탐색은 범위 밖.
- 이번 스코프에서는 **튜닝 결과를 `engine/regime_math.py`의 `N_MULTIPLIER` 프로덕션 상수에 자동으로 반영하지 않는다** — 콘솔 출력+백로그 문서 기록까지만 하고, 실제 반영은 사용자와 별도 논의 후 다음 세션에서.
- 각 태스크 끝에서 `PYTHONPATH=. python -m pytest tests/ -q`가 전부 통과해야 한다(단, `tests/test_import_backtest_results.py::test_script_runs_as_real_subprocess_entry_point`는 이 작업과 무관한 기존 Windows subprocess 인코딩 flake로 알려져 있음 — 실패해도 회귀 아님).
- Task 3(실데이터 실행)는 실제 네트워크로 20개 마켓 데이터를 조회하며 오래 걸린다(5개 후보 × 실측 단일 학습 시간 ~40분 ≈ 3.3시간). **`run_in_background: true`로 직접 실행하고(내부에서 `&`/`disown`으로 다시 감싸지 말 것 — 그러면 도구가 즉시 "완료"로 오인식해 실제 완료 알림을 못 받는다), 결과 숫자를 실제로 손에 넣을 때까지 폴링하며 기다릴 것.** 실행 중 실측 소요시간이 이 추정(3.3시간)을 크게 벗어나면(예: 2배 이상) 진행을 멈추고 사용자에게 확인할 것 — 임의로 그리드를 줄이거나 중단하지 말 것.
- 코스피/코스닥, PBO 프레임워크, 메타 레이블링, CUSUM 이벤트 샘플링, `HALF_LIFE_DAYS` 자체 변경은 이번 스코프 아님.
- 한국어 docstring/주석 관례 유지("왜"를 설명).

---

## Task 1: `run_training()`에 `n_multiplier` 파라미터 추가

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Modify: `tests/test_train_regime_ml.py`

**Interfaces:**
- Produces: `run_training(..., n_multiplier: float = N_MULTIPLIER)` — 기본값을 쓰면 기존 동작과 100% 동일. `n_bars = round(half_life_bars * n_multiplier)`로 계산이 바뀌는 것 외에는 다른 로직 변경 없음. Task 2가 이 파라미터로 horizon 후보를 순회한다.

### Step 1: 실패하는 테스트 작성

`tests/test_train_regime_ml.py` 파일 끝(기존 마지막 테스트
`test_run_training_respects_save_model_false` 다음)에 아래 2개 테스트를 추가한다:

```python
def test_run_training_default_n_multiplier_matches_module_constant(tmp_path, monkeypatch):
    """n_multiplier를 생략하면 지금까지와 동일하게 모듈 상수 N_MULTIPLIER로
    n_bars가 계산되는지 확인한다(회귀 안전장치) — compute_triple_barrier_labels
    호출 인자를 가로챈다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    captured_n_bars = []
    original_labels = train_regime_ml.compute_triple_barrier_labels

    def _capturing_labels(df, half_life_bars, n_bars, k):
        captured_n_bars.append(n_bars)
        return original_labels(df, half_life_bars, n_bars, k)

    monkeypatch.setattr(train_regime_ml, "compute_triple_barrier_labels", _capturing_labels)

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
        save_model=False,
    )

    assert len(result.reports) >= 1
    assert len(captured_n_bars) == len(seeds)  # 마켓별로 한 번씩 호출됨
    expected_n_bars = round(
        train_regime_ml.half_life_bars_for_timeframe("minutes60") * train_regime_ml.N_MULTIPLIER
    )
    assert all(n == expected_n_bars for n in captured_n_bars)


def test_run_training_custom_n_multiplier_changes_n_bars(tmp_path, monkeypatch):
    """n_multiplier를 다르게 넘기면 실제로 다른 n_bars가 라벨링에 쓰이는지
    확인한다 — 기본값(N_MULTIPLIER=2.5, minutes60 기준 n_bars=60)과 다른
    n_multiplier=1.0(n_bars=24)을 넘겨 값이 바뀌는지 검증."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    captured_n_bars = []
    original_labels = train_regime_ml.compute_triple_barrier_labels

    def _capturing_labels(df, half_life_bars, n_bars, k):
        captured_n_bars.append(n_bars)
        return original_labels(df, half_life_bars, n_bars, k)

    monkeypatch.setattr(train_regime_ml, "compute_triple_barrier_labels", _capturing_labels)

    result = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        barrier_k=_BARRIER_K,
        model_output_dir=tmp_path,
        n_multiplier=1.0,
        save_model=False,
    )

    assert len(result.reports) >= 1
    assert len(captured_n_bars) == len(seeds)
    expected_n_bars = round(train_regime_ml.half_life_bars_for_timeframe("minutes60") * 1.0)
    default_n_bars = round(
        train_regime_ml.half_life_bars_for_timeframe("minutes60") * train_regime_ml.N_MULTIPLIER
    )
    assert all(n == expected_n_bars for n in captured_n_bars)
    assert expected_n_bars != default_n_bars  # 실제로 다른 값이어야 의미있는 테스트
```

### Step 2: 테스트 실행해서 실패 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -k n_multiplier -v`
Expected: 2개 테스트 전부 FAIL(`TypeError: run_training() got an unexpected keyword argument 'n_multiplier'`).

### Step 3: `scripts/train_regime_ml.py` 구현

`run_training()` 시그니처를 교체한다.

교체 전:
```python
def run_training(
    markets: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    n_folds: int,
    min_train_samples: int,
    barrier_k: float,
    model_output_dir: Path,
    model_factory: Callable[[], Any] = _default_lgbm_factory,
    preprocess_fold: Callable[[pd.DataFrame, pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]] | None = None,
    save_model: bool = True,
) -> TrainingResult:
```

교체 후:
```python
def run_training(
    markets: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    n_folds: int,
    min_train_samples: int,
    barrier_k: float,
    model_output_dir: Path,
    model_factory: Callable[[], Any] = _default_lgbm_factory,
    preprocess_fold: Callable[[pd.DataFrame, pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]] | None = None,
    save_model: bool = True,
    n_multiplier: float = N_MULTIPLIER,
) -> TrainingResult:
```

docstring 마지막 문장 뒤에 이어서 추가:

```
    n_multiplier로 라벨 horizon(n_bars = half_life_bars * n_multiplier)을 바꿔
    scripts/tune_regime_ml_horizon.py에서 재사용할 수 있다. half_life_bars(피처
    EWM 윈도우)는 영향받지 않는다."
```

(위 텍스트를 기존 docstring의 마지막 줄 `JSON 사이드카를 저장하지 않는다(data/regime_ml_models/ 오염 방지)."""` 앞의 마침표 뒤에 이어붙이되, 문자열을 닫는 `"""`는 새 문장 뒤로 옮긴다 — 즉 아래처럼 교체한다.)

교체 전:
```python
    model_factory/preprocess_fold로 LightGBM 대신 다른 분류기를 끼워 비교/튜닝
    스크립트에서 재사용할 수 있다(scripts/compare_regime_ml_baseline.py,
    scripts/tune_regime_ml_hyperparams.py 참고). save_model=False면 모델 파일/
    JSON 사이드카를 저장하지 않는다(data/regime_ml_models/ 오염 방지)."""
```

교체 후:
```python
    model_factory/preprocess_fold로 LightGBM 대신 다른 분류기를 끼워 비교/튜닝
    스크립트에서 재사용할 수 있다(scripts/compare_regime_ml_baseline.py,
    scripts/tune_regime_ml_hyperparams.py 참고). save_model=False면 모델 파일/
    JSON 사이드카를 저장하지 않는다(data/regime_ml_models/ 오염 방지).

    n_multiplier로 라벨 horizon(n_bars = half_life_bars * n_multiplier)을 바꿔
    scripts/tune_regime_ml_horizon.py에서 재사용할 수 있다. half_life_bars(피처
    EWM 윈도우)는 영향받지 않는다."""
```

함수 본문의 n_bars 계산 줄을 교체한다.

교체 전:
```python
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    embargo = timeframe_duration(timeframe) * n_bars
```

교체 후:
```python
    half_life_bars = half_life_bars_for_timeframe(timeframe)
    n_bars = round(half_life_bars * n_multiplier)
    embargo = timeframe_duration(timeframe) * n_bars
```

### Step 4: 테스트 실행해서 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_train_regime_ml.py -v`
Expected: 14 passed(기존 12개 + 신규 2개).

### Step 5: 전체 테스트 스위트 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 기존 실패 0건 유지(`test_import_backtest_results.py`의 알려진 무관한 flake 1건 제외 — Global Constraints 참고).

### Step 6: 커밋

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "feat: run_training에 n_multiplier(라벨 horizon) 확장점 추가"
```

---

## Task 2: `scripts/tune_regime_ml_horizon.py` — horizon 그리드서치

**Files:**
- Create: `scripts/tune_regime_ml_horizon.py`

**Interfaces:**
- Consumes: Task 1의 `run_training(..., n_multiplier=..., save_model=False) -> TrainingResult`, `scripts.train_regime_ml`의 `BARRIER_K`/`MIN_TRAIN_SAMPLES`/`MODEL_OUTPUT_DIR`/`N_FOLDS`/`TIMEFRAME`/`TRAIN_END`/`TRAIN_START`, `engine.regime_ml_constants.TRAINING_MARKETS`.

`select_barrier_k.py`/c-2 스크립트들과 같은 성격의 1회성 진단 스크립트라 전용
테스트를 작성하지 않는다(기존 프로젝트 관례). Step 2에서 synthetic 데이터로
손으로 완주 확인한다.

### Step 1: 스크립트 작성

```python
"""
scripts/tune_regime_ml_horizon.py

라벨 horizon(N_MULTIPLIER) 그리드서치. 지금 n_bars=60(1시간봉 기준 2.5일)은
HALF_LIFE_DAYS=1.0(2026-08-23 "반응속도 빠름" 사용자 선택) x N_MULTIPLIER=2.5로
계산되는데, 이 중 N_MULTIPLIER는 barrier_k(scripts/select_barrier_k.py)와 달리
kappa 기준으로 한 번도 실측 재탐색된 적이 없다. barrier_k와 같은 방식으로
horizon 후보들을 walk-forward pooled weighted kappa 기준으로 비교한다. 설계:
docs/superpowers/specs/2026-09-01-regime-ml-horizon-tuning-design.md

barrier_k는 5개 후보 전부 현재 프로덕션 값(BARRIER_K)으로 고정한다 — horizon이
길어질수록 만기 전 바리어 터치 확률이 자연히 올라가 라벨 분포가 후보마다
달라지는 걸 알고 있지만, 이번 조사 목적("horizon이 kappa에 영향을 주는가")에는
그 상태 그대로가 유의미한 관측치다. barrier_k와 horizon의 조인트 재탐색은
범위 밖(설계 문서 참고).

select_barrier_k.py와 같은 성격의 1회성 진단 스크립트 — 결과가 채택되면
engine/regime_math.py의 N_MULTIPLIER 상수를 수동으로 갱신한다(이 스크립트가
자동으로 반영하지 않음).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/tune_regime_ml_horizon.py
(5개 후보 재학습 — 단일 학습 실측 소요시간(~40분, c-2 세션 실측) 기준 총
3~4시간 예상)
"""
from __future__ import annotations

from engine.regime_ml_constants import TRAINING_MARKETS
from scripts.train_regime_ml import (
    BARRIER_K,
    MIN_TRAIN_SAMPLES,
    MODEL_OUTPUT_DIR,
    N_FOLDS,
    TIMEFRAME,
    TRAIN_END,
    TRAIN_START,
    run_training,
)

_CANDIDATES = [0.5, 1.0, 1.5, 2.5, 4.0]  # 2.5 = 현재 프로덕션 값(N_MULTIPLIER)
_CURRENT_PRODUCTION_VALUE = 2.5

_COMMON_KWARGS = dict(
    markets=TRAINING_MARKETS,
    timeframe=TIMEFRAME,
    start=TRAIN_START,
    end=TRAIN_END,
    n_folds=N_FOLDS,
    min_train_samples=MIN_TRAIN_SAMPLES,
    barrier_k=BARRIER_K,
    model_output_dir=MODEL_OUTPUT_DIR,
    save_model=False,
)


def main() -> None:
    print(f"=== horizon(N_MULTIPLIER) 그리드서치: 후보 {_CANDIDATES} ===")
    results: list[tuple[float, float | None, float | None]] = []
    for n_multiplier in _CANDIDATES:
        print(f"\n=== N_MULTIPLIER={n_multiplier} 학습 중 ===")
        result = run_training(**_COMMON_KWARGS, n_multiplier=n_multiplier)
        kappa = result.pooled["weighted_kappa"]
        macro_f1 = result.pooled["macro_f1"]
        results.append((n_multiplier, kappa, macro_f1))
        print(f"N_MULTIPLIER={n_multiplier} -> weighted_kappa={kappa}, macro_f1={macro_f1}")

    print("\n=== 최종 비교 ===")
    print(f"{'N_MULTIPLIER':>12}{'weighted kappa':>18}{'macro F1':>12}")
    for n_multiplier, kappa, macro_f1 in results:
        kappa_str = f"{kappa:.3f}" if kappa is not None else "N/A"
        macro_f1_str = f"{macro_f1:.3f}" if macro_f1 is not None else "N/A"
        marker = " <- 현재 프로덕션 값" if n_multiplier == _CURRENT_PRODUCTION_VALUE else ""
        print(f"{n_multiplier:>12}{kappa_str:>18}{macro_f1_str:>12}{marker}")

    valid_results = [(n, k, f) for n, k, f in results if k is not None]
    if not valid_results:
        print("\n모든 후보가 pooled kappa를 계산하지 못했습니다(표본 부족).")
        return

    best_n_multiplier, best_kappa, _ = max(valid_results, key=lambda item: item[1])
    current_kappa = next((k for n, k, _ in results if n == _CURRENT_PRODUCTION_VALUE), None)
    if current_kappa is None:
        print(f"\n최고 kappa: N_MULTIPLIER={best_n_multiplier}(kappa={best_kappa:.3f})")
    else:
        delta = best_kappa - current_kappa
        print(
            f"\n최고 kappa: N_MULTIPLIER={best_n_multiplier}(kappa={best_kappa:.3f}), "
            f"현재값({_CURRENT_PRODUCTION_VALUE}) 대비 델타={delta:+.3f}"
        )


if __name__ == "__main__":
    main()
```

### Step 2: synthetic 데이터로 손으로 동작 확인

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
from datetime import datetime, timezone
import pandas as pd
from tests.test_train_regime_ml import _make_synthetic_market_df
import scripts.tune_regime_ml_horizon as horizon_mod
import scripts.train_regime_ml as train_mod

seeds = {'KRW-BTC': 1, 'KRW-ETH': 2, 'KRW-XRP': 3}
train_mod.load_market_training_data = lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market])
horizon_mod._COMMON_KWARGS['markets'] = list(seeds.keys())
horizon_mod._COMMON_KWARGS['n_folds'] = 2
horizon_mod._COMMON_KWARGS['min_train_samples'] = 50
horizon_mod._COMMON_KWARGS['start'] = datetime(2024, 1, 1, tzinfo=timezone.utc)
horizon_mod._COMMON_KWARGS['end'] = horizon_mod._COMMON_KWARGS['start'] + pd.Timedelta(hours=24 * 40)
horizon_mod._CANDIDATES = [1.0, 2.5]
horizon_mod.main()
"
```

Expected: 에러 없이 "=== 최종 비교 ===" 줄과 "최고 kappa: ..." 줄까지 출력됨
(synthetic 데이터라 숫자 자체는 의미 없음 — 완주 여부만 확인).

### Step 3: 커밋

```bash
git add scripts/tune_regime_ml_horizon.py
git commit -m "feat: 라벨 horizon(N_MULTIPLIER) 그리드서치 스크립트 추가"
```

---

## Task 3: 실데이터 실행 + 백로그 문서 반영

**Files:**
- Modify: `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: Task 2 스크립트의 실제 콘솔 출력(20마켓, 실네트워크 데이터).

### Step 1: `tune_regime_ml_horizon.py` 실데이터 실행

Run(`run_in_background: true`로 직접 실행 — Global Constraints 참고, `&`/`disown`으로
감싸지 말 것):
`PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/tune_regime_ml_horizon.py`

완료까지 폴링하며 기다린다(예상 3~4시간). 콘솔에 출력된 5개 후보의 pooled
weighted kappa/macro F1과 "최고 kappa: ..." 결론 줄을 그대로 기록해둔다.

### Step 2: `docs/regime-ml-backlog.md`에 결과 반영

c-2 절(`## c-2 로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝 — 완료`)
바로 아래에 새 절을 추가한다(정확한 숫자는 Step 1 실행 결과로 채운다 — 아래는
형식 예시이며 실제 숫자로 교체할 것):

```markdown
## horizon(N_MULTIPLIER) 그리드서치 — 완료(2026-09-XX)

설계: `docs/superpowers/specs/2026-09-01-regime-ml-horizon-tuning-design.md`.
계획: `docs/superpowers/plans/2026-09-01-regime-ml-horizon-tuning.md`. 구현:
`scripts/train_regime_ml.py`(n_multiplier 확장), `scripts/tune_regime_ml_horizon.py`.

**실측 결과(20마켓 실데이터, barrier_k=6.25 고정)**:

| N_MULTIPLIER | n_bars(1시간봉 기준) | weighted kappa | macro F1 |
|---|---|---|---|
| 0.5 | 12시간 | <실측값> | <실측값> |
| 1.0 | 24시간 | <실측값> | <실측값> |
| 1.5 | 36시간 | <실측값> | <실측값> |
| 2.5(현재) | 60시간 | 0.105 | 0.547 |
| 4.0 | 96시간 | <실측값> | <실측값> |

**결정**: <최고 kappa 후보와 현재값(2.5) 대비 델타 서술 — 델타가 자연변동폭
(±0.005) 이내면 "미채택(중립)", 그 이상이면 재현성 확인 후 다음 세션에서
engine/regime_math.py의 N_MULTIPLIER 반영 검토>.
```

### Step 3: 커밋

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: horizon(N_MULTIPLIER) 그리드서치 실측 결과 반영"
```

---

## 범위 밖

- barrier_k와 horizon의 조인트 그리드서치.
- `HALF_LIFE_DAYS`(피처 반응속도) 자체를 바꾸는 실험.
- 튜닝 결과를 `engine/regime_math.py`의 `N_MULTIPLIER` 프로덕션 상수에 자동
  반영하는 것(사용자와 별도 논의 후 다음 세션).
- AWS 배포(별개 백로그 후보).
