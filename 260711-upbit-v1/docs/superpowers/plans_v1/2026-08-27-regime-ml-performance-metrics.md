# 장세 판별 ML 모델 성능 지표 표기(A2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 학습 스크립트가 계산하는 fold별/풀링 성능 지표(상관계수·hit-rate)를 모델 사이드카 JSON에 저장하고, 기존 ML 현재예측 API·프론트 카드에 노출한다.

**Architecture:** `scripts/train_regime_ml.py`의 상관계수/hit-rate 계산 로직을 순수 함수(`_correlation_from_pairs`/`_compute_hit_rate`)로 뽑아 콘솔 출력과 사이드카 저장이 공유하게 한다. `backend/regime_ml_service.py`는 이미 로드하는 사이드카에서 `performance` 필드 하나만 응답에 추가한다(새 엔드포인트 없음). 프론트 `RegimeMlCurrentPrediction.tsx`는 기존 카드 하단에 fold 표 + 풀링 지표 섹션을 추가한다.

**Tech Stack:** Python(LightGBM, pandas, numpy, pytest), FastAPI, Next.js/React/TypeScript.

## Global Constraints

- 사이드카 JSON 스키마: `performance.folds`(리스트, 각 원소 `fold_index`/`n_train`/`n_test`/`correlation`), `performance.pooled_correlation`(number|null), `performance.pooled_hit_rate`(카테고리별 number|null). fold별 hit-rate/confusion matrix는 저장하지 않는다(비범위).
- API 응답 필드명: `model_performance`(camelCase 아님, 기존 응답 필드와 동일하게 snake_case).
- 새 API 엔드포인트를 만들지 않는다 — 기존 `GET /api/v1/regime/ml-current-prediction` 재사용.
- `backend/regime_ml_service.py`는 `sidecar.get("performance")`로 방어(구형 사이드카에 키가 없어도 500 대신 `null`).
- 프론트 타입/필드명은 스펙과 정확히 일치: `MlFoldPerformance`, `MlModelPerformance`, `model_performance`.
- 전체 설계 근거는 `docs/superpowers/specs_v1/2026-08-27-regime-ml-performance-metrics-design.md` 참고.

---

## Task 1: 학습 스크립트 — 계산 헬퍼 추출 + 사이드카에 performance 저장

**Files:**
- Modify: `scripts/train_regime_ml.py`
- Test: `tests/test_train_regime_ml.py`

**Interfaces:**
- Produces: `_correlation_from_pairs(expected_scores: list[float], actual_values: list[float]) -> float | None`, `_compute_hit_rate(confusion: dict[str, dict[str, int]]) -> dict[str, float | None]` — 둘 다 모듈 레벨 함수로, Task 2에서 백엔드가 소비하는 사이드카 JSON의 `performance` 구조를 만드는 데 쓰인다.
- Produces: 사이드카 JSON에 `performance` 키(스키마는 Global Constraints 참고) — Task 2가 `backend/regime_ml_service.py`에서 이 키를 읽는다.

- [ ] **Step 1: `_correlation_from_pairs` 실패하는 테스트 작성**

`tests/test_train_regime_ml.py` 끝에 추가:

```python
def test_correlation_from_pairs_returns_none_when_insufficient_samples():
    assert train_regime_ml._correlation_from_pairs([], []) is None
    assert train_regime_ml._correlation_from_pairs([0.1], [0.2]) is None


def test_correlation_from_pairs_computes_pearson_correlation():
    expected_scores = [1.0, 2.0, 3.0, 4.0]
    actual_values = [1.1, 1.9, 3.2, 3.8]
    result = train_regime_ml._correlation_from_pairs(expected_scores, actual_values)
    assert result == pytest.approx(
        float(np.corrcoef(expected_scores, actual_values)[0, 1]), abs=1e-9
    )


def test_correlation_from_pairs_returns_none_for_zero_variance_series():
    result = train_regime_ml._correlation_from_pairs([1.0, 1.0, 1.0], [0.1, 0.2, 0.3])
    assert result is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k correlation_from_pairs -v`
Expected: FAIL — `AttributeError: module 'scripts.train_regime_ml' has no attribute '_correlation_from_pairs'`

- [ ] **Step 3: `_correlation_from_pairs` 구현**

`scripts/train_regime_ml.py`에서 `_print_correlation_block` 함수 바로 위에 추가:

```python
def _correlation_from_pairs(expected_scores: list[float], actual_values: list[float]) -> float | None:
    """(expected_score, actual_value) 쌍에서 피어슨 상관계수를 계산한다. fold 하나의
    쌍이든, 여러 fold를 풀링한 쌍이든 계산 방식은 동일하므로 양쪽에서 공유한다."""
    if len(expected_scores) < 2:
        return None
    computed = float(np.corrcoef(expected_scores, actual_values)[0, 1])
    return None if np.isnan(computed) else computed
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k correlation_from_pairs -v`
Expected: PASS (3 tests)

- [ ] **Step 5: `_compute_hit_rate` 실패하는 테스트 작성**

`tests/test_train_regime_ml.py`에 추가:

```python
def test_compute_hit_rate_divides_correct_by_predicted_total():
    confusion = {
        "급하락": {"급하락": 3, "완만하락": 1, "횡보": 0, "완만상승": 0, "급상승": 0},
        "완만하락": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 0, "급상승": 0},
        "횡보": {"급하락": 0, "완만하락": 0, "횡보": 5, "완만상승": 0, "급상승": 0},
        "완만상승": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 2, "급상승": 2},
        "급상승": {"급하락": 0, "완만하락": 0, "횡보": 0, "완만상승": 0, "급상승": 0},
    }
    hit_rate = train_regime_ml._compute_hit_rate(confusion)
    assert hit_rate["급하락"] == pytest.approx(3 / 4)
    assert hit_rate["완만하락"] is None
    assert hit_rate["횡보"] == pytest.approx(1.0)
    assert hit_rate["완만상승"] == pytest.approx(2 / 4)
    assert hit_rate["급상승"] is None
```

- [ ] **Step 6: 테스트 실패 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k compute_hit_rate -v`
Expected: FAIL — `AttributeError: module 'scripts.train_regime_ml' has no attribute '_compute_hit_rate'`

- [ ] **Step 7: `_compute_hit_rate` 구현**

`scripts/train_regime_ml.py`에서 `_correlation_from_pairs` 바로 아래에 추가:

```python
def _compute_hit_rate(confusion: dict[str, dict[str, int]]) -> dict[str, float | None]:
    hit_rate: dict[str, float | None] = {}
    for label in CATEGORY_LABELS:
        row = confusion[label]
        total = sum(row.values())
        hit_rate[label] = (row[label] / total) if total else None
    return hit_rate
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k compute_hit_rate -v`
Expected: PASS

- [ ] **Step 9: 기존 호출부를 새 헬퍼로 교체**

`scripts/train_regime_ml.py`에서 fold 루프 내부의 인라인 상관계수 계산을 교체.

Old (`run_training` 내부):
```python
        correlation: float | None = None
        if len(expected_scores) >= 2:
            computed = float(np.corrcoef(expected_scores, actual_values)[0, 1])
            if not np.isnan(computed):
                correlation = computed
```

New:
```python
        correlation = _correlation_from_pairs(expected_scores, actual_values)
```

`_print_hit_rate_block`을 `_compute_hit_rate` 호출로 리팩터.

Old:
```python
def _print_hit_rate_block(confusion: dict[str, dict[str, int]]) -> None:
    print("  [예측 카테고리별 hit-rate]")
    for label in CATEGORY_LABELS:
        row = confusion[label]
        total = sum(row.values())
        if total == 0:
            print(f"    {label}: 샘플 없음")
            continue
        hit_rate = row[label] / total * 100
        print(f"    {label}: {row[label]}/{total} 적중 ({hit_rate:.1f}%)")
```

New:
```python
def _print_hit_rate_block(confusion: dict[str, dict[str, int]]) -> None:
    print("  [예측 카테고리별 hit-rate]")
    hit_rate = _compute_hit_rate(confusion)
    for label in CATEGORY_LABELS:
        row = confusion[label]
        total = sum(row.values())
        rate = hit_rate[label]
        if rate is None:
            print(f"    {label}: 샘플 없음")
            continue
        print(f"    {label}: {row[label]}/{total} 적중 ({rate * 100:.1f}%)")
```

`_print_aggregate_summary`의 인라인 상관계수 계산을 교체.

Old:
```python
    correlation: float | None = None
    if len(all_expected_scores) >= 2:
        computed = float(np.corrcoef(all_expected_scores, all_actual_values)[0, 1])
        if not np.isnan(computed):
            correlation = computed
```

New:
```python
    correlation = _correlation_from_pairs(all_expected_scores, all_actual_values)
```

- [ ] **Step 10: 리팩터 후 기존 테스트 통과 확인(회귀 없음)**

Run: `python -m pytest tests/test_train_regime_ml.py -v`
Expected: PASS (전체 — 리팩터는 순수 동작 동일성 보존이라 이 시점엔 아직 사이드카 스키마를 안 건드렸으므로 전부 그대로 통과해야 함)

- [ ] **Step 11: 사이드카 스키마 검증 실패하는 테스트로 갱신**

`tests/test_train_regime_ml.py`의 `test_run_training_saves_json_sidecar_alongside_model`을 교체:

Old:
```python
def test_run_training_saves_json_sidecar_alongside_model(tmp_path, monkeypatch):
    """Finding 4: 저장된 booster(.txt) 옆에 같은 base filename의 .json sidecar가
    boundaries/ref_scores/classes/fold_index를 담아 저장돼야 한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1

    txt_files = list(tmp_path.glob("*.txt"))
    json_files = list(tmp_path.glob("*.json"))
    assert len(txt_files) == 1
    assert len(json_files) == 1
    assert txt_files[0].stem == json_files[0].stem

    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert set(sidecar.keys()) == {"boundaries", "ref_scores", "classes", "fold_index"}
    assert isinstance(sidecar["boundaries"], list) and len(sidecar["boundaries"]) == 4
    assert isinstance(sidecar["ref_scores"], dict)
    assert set(sidecar["ref_scores"].keys()) == set(train_regime_ml.CATEGORY_LABELS)
    assert isinstance(sidecar["classes"], list) and len(sidecar["classes"]) >= 1
    assert isinstance(sidecar["fold_index"], int)
    assert sidecar["fold_index"] == reports[-1]["fold_index"]
```

New:
```python
def test_run_training_saves_json_sidecar_alongside_model(tmp_path, monkeypatch):
    """Finding 4: 저장된 booster(.txt) 옆에 같은 base filename의 .json sidecar가
    boundaries/ref_scores/classes/fold_index/performance를 담아 저장돼야 한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=2,
        min_train_samples=50,
        model_output_dir=tmp_path,
    )
    assert len(reports) >= 1

    txt_files = list(tmp_path.glob("*.txt"))
    json_files = list(tmp_path.glob("*.json"))
    assert len(txt_files) == 1
    assert len(json_files) == 1
    assert txt_files[0].stem == json_files[0].stem

    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert set(sidecar.keys()) == {"boundaries", "ref_scores", "classes", "fold_index", "performance"}
    assert isinstance(sidecar["boundaries"], list) and len(sidecar["boundaries"]) == 4
    assert isinstance(sidecar["ref_scores"], dict)
    assert set(sidecar["ref_scores"].keys()) == set(train_regime_ml.CATEGORY_LABELS)
    assert isinstance(sidecar["classes"], list) and len(sidecar["classes"]) >= 1
    assert isinstance(sidecar["fold_index"], int)
    assert sidecar["fold_index"] == reports[-1]["fold_index"]

    performance = sidecar["performance"]
    assert len(performance["folds"]) == len(reports)
    for fold_perf, report in zip(performance["folds"], reports):
        assert fold_perf["fold_index"] == report["fold_index"]
        assert fold_perf["n_train"] == report["n_train"]
        assert fold_perf["n_test"] == report["n_test"]
        assert fold_perf["correlation"] == report["correlation"]

    pooled_confusion = train_regime_ml._sum_confusion_matrices(reports)
    expected_pooled_hit_rate = train_regime_ml._compute_hit_rate(pooled_confusion)
    assert performance["pooled_hit_rate"] == expected_pooled_hit_rate
    assert set(performance["pooled_hit_rate"].keys()) == set(train_regime_ml.CATEGORY_LABELS)

    # pooled_correlation은 run_training() 내부에서만 접근 가능한
    # all_expected_scores/all_actual_values로 계산되고 reports는 그 원본 페어를
    # 반환하지 않으므로, reports만으로 값을 독립 재계산해 정확히 비교할 수는 없다.
    # 대신 타입/범위로 타당성만 확인한다 — 정확한 계산 자체는
    # test_correlation_from_pairs_computes_pearson_correlation()이 이미 단위
    # 검증했고, 여기서는 그 함수가 실제로 호출·저장됐는지만 보면 된다.
    pooled_correlation = performance["pooled_correlation"]
    assert pooled_correlation is None or -1.0 <= pooled_correlation <= 1.0
```

- [ ] **Step 12: 테스트 실패 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k saves_json_sidecar -v`
Expected: FAIL — `KeyError: 'performance'` (사이드카에 아직 이 키가 없음)

- [ ] **Step 13: 사이드카 저장 블록에 performance 추가**

`scripts/train_regime_ml.py`의 `run_training()` 끝부분(모델 저장 블록)을 교체.

Old:
```python
    if last_model is not None:
        model_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_name = f"regime_ml_{timestamp}"
        last_model.booster_.save_model(str(model_output_dir / f"{base_name}.txt"))

        sidecar = {
            "boundaries": last_boundaries,
            "ref_scores": last_ref_scores,
            "classes": last_class_order,
            "fold_index": last_fold_index,
        }
        with open(model_output_dir / f"{base_name}.json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=2)

    return reports
```

New:
```python
    if last_model is not None:
        model_output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_name = f"regime_ml_{timestamp}"
        last_model.booster_.save_model(str(model_output_dir / f"{base_name}.txt"))

        pooled_confusion = _sum_confusion_matrices(reports)
        pooled_correlation = _correlation_from_pairs(all_expected_scores, all_actual_values)
        pooled_hit_rate = _compute_hit_rate(pooled_confusion)

        sidecar = {
            "boundaries": last_boundaries,
            "ref_scores": last_ref_scores,
            "classes": last_class_order,
            "fold_index": last_fold_index,
            "performance": {
                "folds": [
                    {
                        "fold_index": r["fold_index"],
                        "n_train": r["n_train"],
                        "n_test": r["n_test"],
                        "correlation": r["correlation"],
                    }
                    for r in reports
                ],
                "pooled_correlation": pooled_correlation,
                "pooled_hit_rate": pooled_hit_rate,
            },
        }
        with open(model_output_dir / f"{base_name}.json", "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=2)

    return reports
```

- [ ] **Step 14: 테스트 통과 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k saves_json_sidecar -v`
Expected: PASS

- [ ] **Step 15: 부분 스킵 시나리오 테스트 추가**

`tests/test_train_regime_ml.py`에 추가(같은 합성 데이터에서 fold별 `n_train`이
543 / 1263 / 1983으로 늘어나는 것을 사전 실측 확인함 — `min_train_samples=600`이면
fold 1만 스킵되고 fold 2·3은 평가됨):

```python
def test_run_training_performance_folds_excludes_skipped_folds(tmp_path, monkeypatch):
    """fold 하나가 표본 부족으로 스킵됐을 때, 사이드카 performance.folds가 실제로
    평가된 fold만 담고(reports와 정확히 같은 fold_index 집합) 스킵된 fold는 포함하지
    않는지 확인한다."""
    seeds = {"KRW-BTC": 1, "KRW-ETH": 2, "KRW-XRP": 3}
    monkeypatch.setattr(
        train_regime_ml, "load_market_training_data",
        lambda market, timeframe, start, end: _make_synthetic_market_df(market, seeds[market]),
    )

    reports = run_training(
        markets=list(seeds.keys()),
        timeframe="minutes60",
        start=START,
        end=START + pd.Timedelta(hours=_N),
        n_folds=3,
        min_train_samples=600,
        model_output_dir=tmp_path,
    )

    assert [r["fold_index"] for r in reports] == [2, 3]

    json_files = list(tmp_path.glob("*.json"))
    with open(json_files[0], encoding="utf-8") as f:
        sidecar = json.load(f)

    assert [f["fold_index"] for f in sidecar["performance"]["folds"]] == [2, 3]
```

- [ ] **Step 16: 테스트 통과 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -k excludes_skipped_folds -v`
Expected: PASS. 만약 `[r["fold_index"] for r in reports]`가 `[2, 3]`이 아니면(합성
데이터 생성 로직이 바뀌어 실측 n_train이 달라진 경우), 콘솔에 찍히는 실제
`n_train` 값을 보고 `min_train_samples`를 fold 하나만 걸리도록 다시 조정한다.

- [ ] **Step 17: 전체 스위트 통과 확인**

Run: `python -m pytest tests/test_train_regime_ml.py -v`
Expected: PASS (전체)

- [ ] **Step 18: Commit**

```bash
git add scripts/train_regime_ml.py tests/test_train_regime_ml.py
git commit -m "$(cat <<'EOF'
feat: 장세 판별 ML 학습 스크립트가 fold별/풀링 성능 지표를 사이드카에 저장

상관계수/hit-rate 계산을 _correlation_from_pairs/_compute_hit_rate로 뽑아
콘솔 출력과 사이드카 저장이 같은 로직을 공유하게 함(A2 1/3).
EOF
)"
```

---

## Task 2: 백엔드 — ML 현재예측 응답에 model_performance 추가

**Files:**
- Modify: `backend/regime_ml_service.py`
- Test: `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: 사이드카 JSON의 `performance` 키(Task 1이 생성) — 구조는 `{"folds": [{"fold_index": int, "n_train": int, "n_test": int, "correlation": float|None}], "pooled_correlation": float|None, "pooled_hit_rate": dict[str, float|None]}`. 이 키가 없는(구형) 사이드카도 처리해야 함.
- Produces: `predict_current_ml_regime()` 반환 dict에 `model_performance` 키(사이드카의 `performance` 값 그대로, 없으면 `None`) — Task 3에서 프론트가 이 API 응답을 소비한다.

- [ ] **Step 1: `_train_and_save_tiny_model` 헬퍼에 performance 파라미터 추가**

`tests/test_regime_ml_service.py`의 `_train_and_save_tiny_model`을 교체.

Old:
```python
def _train_and_save_tiny_model(model_dir, timestamp: str, fold_index: int = 3):
    """scripts/train_regime_ml.py의 실제 흐름(3마켓 풀링 -> market astype category
    -> LightGBM 학습 -> booster_.save_model)을 축소 재현해 .txt+.json 페어를 저장한다."""
    rng = np.random.default_rng(0)
    rows = []
    for market in _MARKETS:
        for _ in range(30):
            rows.append({"FEATURE_A": rng.normal(), "FEATURE_B": rng.normal(), "market": market})
    df = pd.DataFrame(rows)
    df["market"] = df["market"].astype("category")
    labels = pd.Series(rng.choice(_LABELS, size=len(df)))

    model = lgb.LGBMClassifier(objective="multiclass", num_leaves=4, min_child_samples=1, random_state=0)
    model.fit(df, labels)

    model_dir.mkdir(parents=True, exist_ok=True)
    txt_path = model_dir / f"regime_ml_{timestamp}.txt"
    json_path = model_dir / f"regime_ml_{timestamp}.json"
    model.booster_.save_model(str(txt_path))
    json_path.write_text(json.dumps({
        "boundaries": [-0.2, -0.1, 0.1, 0.2],
        "ref_scores": {label: 0.0 for label in _LABELS},
        "classes": [str(c) for c in model.classes_],
        "fold_index": fold_index,
    }), encoding="utf-8")
    return txt_path, json_path, model
```

New:
```python
def _train_and_save_tiny_model(model_dir, timestamp: str, fold_index: int = 3, performance: dict | None = None):
    """scripts/train_regime_ml.py의 실제 흐름(3마켓 풀링 -> market astype category
    -> LightGBM 학습 -> booster_.save_model)을 축소 재현해 .txt+.json 페어를 저장한다.
    performance를 None으로 두면(기본값) performance 키 자체가 없는 구형 사이드카를
    재현한다 — 명시하면 그 값을 그대로 담는다."""
    rng = np.random.default_rng(0)
    rows = []
    for market in _MARKETS:
        for _ in range(30):
            rows.append({"FEATURE_A": rng.normal(), "FEATURE_B": rng.normal(), "market": market})
    df = pd.DataFrame(rows)
    df["market"] = df["market"].astype("category")
    labels = pd.Series(rng.choice(_LABELS, size=len(df)))

    model = lgb.LGBMClassifier(objective="multiclass", num_leaves=4, min_child_samples=1, random_state=0)
    model.fit(df, labels)

    model_dir.mkdir(parents=True, exist_ok=True)
    txt_path = model_dir / f"regime_ml_{timestamp}.txt"
    json_path = model_dir / f"regime_ml_{timestamp}.json"
    model.booster_.save_model(str(txt_path))
    sidecar = {
        "boundaries": [-0.2, -0.1, 0.1, 0.2],
        "ref_scores": {label: 0.0 for label in _LABELS},
        "classes": [str(c) for c in model.classes_],
        "fold_index": fold_index,
    }
    if performance is not None:
        sidecar["performance"] = performance
    json_path.write_text(json.dumps(sidecar), encoding="utf-8")
    return txt_path, json_path, model
```

- [ ] **Step 2: `model_performance` 전달을 검증하는 실패하는 테스트 작성**

`tests/test_regime_ml_service.py`에 추가(기존 `test_predict_current_ml_regime_returns_valid_response`
바로 아래):

```python
def test_predict_current_ml_regime_includes_performance_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    performance = {
        "folds": [{"fold_index": 5, "n_train": 100, "n_test": 20, "correlation": 0.12}],
        "pooled_correlation": 0.12,
        "pooled_hit_rate": {label: 0.2 for label in _LABELS},
    }
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5, performance=performance)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    assert result["model_performance"] == performance


def test_predict_current_ml_regime_performance_is_none_for_legacy_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    _train_and_save_tiny_model(tmp_path, "20260827T052047Z", fold_index=5)  # performance 없음(기본값)

    fake_raw_df = pd.DataFrame({
        "close": [1.0] * 5,
        "candle_time": pd.date_range("2026-08-27T01:00:00", periods=5, freq="h"),
    })
    monkeypatch.setattr(regime_ml_service, "load_market_training_data", lambda *a, **k: fake_raw_df)

    def _fake_build_feature_matrix(df, market, half_life_bars):
        rng = np.random.default_rng(1)
        return pd.DataFrame({
            "FEATURE_A": rng.normal(size=len(df)),
            "FEATURE_B": rng.normal(size=len(df)),
            "market": pd.Categorical([market] * len(df)),
        })

    monkeypatch.setattr(regime_ml_service, "build_feature_matrix", _fake_build_feature_matrix)

    result = predict_current_ml_regime("KRW-ETH", "minutes60")

    assert result["model_performance"] is None
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python -m pytest tests/test_regime_ml_service.py -k "includes_performance or performance_is_none" -v`
Expected: FAIL — `KeyError: 'model_performance'`

- [ ] **Step 4: `predict_current_ml_regime` 반환값에 model_performance 추가**

`backend/regime_ml_service.py`의 `predict_current_ml_regime()` 반환문을 교체.

Old:
```python
    return {
        "predicted_category": predicted_category,
        "probs": probs,
        "model_trained_at": _parse_trained_at(model_path),
        "model_fold_index": sidecar["fold_index"],
        "bar_time": bar_time,
    }
```

New:
```python
    return {
        "predicted_category": predicted_category,
        "probs": probs,
        "model_trained_at": _parse_trained_at(model_path),
        "model_fold_index": sidecar["fold_index"],
        "bar_time": bar_time,
        "model_performance": sidecar.get("performance"),
    }
```

- [ ] **Step 5: 새 테스트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_service.py -k "includes_performance or performance_is_none" -v`
Expected: PASS

- [ ] **Step 6: 기존 테스트에도 model_performance 단언 추가(하위 호환 명시)**

`tests/test_regime_ml_service.py`의 `test_predict_current_ml_regime_returns_valid_response` 끝에
한 줄 추가.

Old(마지막 두 줄):
```python
    assert result["model_trained_at"] == datetime(2026, 8, 27, 5, 20, 47, tzinfo=timezone.utc).isoformat()
    assert result["bar_time"] == datetime(2026, 8, 27, 5, 0, 0, tzinfo=timezone.utc).isoformat()
```

New:
```python
    assert result["model_trained_at"] == datetime(2026, 8, 27, 5, 20, 47, tzinfo=timezone.utc).isoformat()
    assert result["bar_time"] == datetime(2026, 8, 27, 5, 0, 0, tzinfo=timezone.utc).isoformat()
    assert result["model_performance"] is None
```

- [ ] **Step 7: 전체 스위트 통과 확인**

Run: `python -m pytest tests/test_regime_ml_service.py -v`
Expected: PASS (전체)

- [ ] **Step 8: Commit**

```bash
git add backend/regime_ml_service.py tests/test_regime_ml_service.py
git commit -m "$(cat <<'EOF'
feat: ML 현재예측 API 응답에 model_performance 필드 추가

기존 사이드카 로딩 경로를 재사용해 성능 지표를 노출(A2 2/3). 구형
사이드카(performance 키 없음)는 .get()으로 방어해 None으로 폴백.
EOF
)"
```

---

## Task 3: 프론트엔드 — 타입 + 모델 성능 섹션 UI

**Files:**
- Modify: `frontend/lib/types/eda.ts`
- Modify: `frontend/components/RegimeMlCurrentPrediction.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/regime/ml-current-prediction` 응답의 `model_performance` 필드(Task 2가 추가) — `{"folds": [{"fold_index": number, "n_train": number, "n_test": number, "correlation": number|null}], "pooled_correlation": number|null, "pooled_hit_rate": Record<RegimeCategory, number|null>} | null`.

- [ ] **Step 1: 타입 추가**

`frontend/lib/types/eda.ts`에서 `MlCurrentPrediction` 정의를 교체.

Old:
```typescript
export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
}
```

New:
```typescript
export interface MlFoldPerformance {
  fold_index: number;
  n_train: number;
  n_test: number;
  correlation: number | null;
}

export interface MlModelPerformance {
  folds: MlFoldPerformance[];
  pooled_correlation: number | null;
  pooled_hit_rate: Record<RegimeCategory, number | null>;
}

export interface MlCurrentPrediction {
  predicted_category: RegimeCategory;
  probs: Record<RegimeCategory, number>;
  bar_time: string;
  model_trained_at: string;
  model_fold_index: number;
  model_performance: MlModelPerformance | null;
}
```

- [ ] **Step 2: TypeScript 컴파일 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 기존 에러 없음(이 타입 변경만으로는 아직 컴파일 에러가 나지 않아야 함 —
`MlCurrentPrediction`을 쓰는 곳이 `RegimeMlCurrentPrediction.tsx` 하나뿐이고 아직
`model_performance`를 읽지 않으므로 optional 여부와 무관하게 통과)

- [ ] **Step 3: 모델 성능 섹션 UI 추가**

`frontend/components/RegimeMlCurrentPrediction.tsx`의 데이터 렌더 블록(캡션 `<p>` 태그
직후)에 섹션을 추가. 파일 상단에 카테고리별 hit-rate 표시용 헬퍼도 함께 추가한다.

Old(파일 9번째 줄, `CATEGORY_ORDER` 선언부):
```typescript
const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];
const TRAINED_MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'];
```

New:
```typescript
const CATEGORY_ORDER: RegimeCategory[] = ['급상승', '완만상승', '횡보', '완만하락', '급하락'];
const TRAINED_MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'];

function formatPct(value: number | null): string {
  return value === null ? '-' : `${(value * 100).toFixed(1)}%`;
}

function formatCorrelation(value: number | null): string {
  return value === null ? '-' : value.toFixed(3);
}
```

Old(캡션 `<p>` 태그, 현재 파일 100~102행):
```typescript
          <p className="text-xs text-muted-foreground">
            {market} {formatTimeframe(timeframe)} 기준, {formatDateTime(data.bar_time)} 봉 데이터. (모델: {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index})
          </p>
        </>
      ) : null}
    </div>
  );
}
```

New:
```typescript
          <p className="text-xs text-muted-foreground">
            {market} {formatTimeframe(timeframe)} 기준, {formatDateTime(data.bar_time)} 봉 데이터. (모델: {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index})
          </p>
          <div className="mt-4 border-t pt-3">
            <h3 className="mb-2 text-xs font-semibold text-muted-foreground">모델 성능</h3>
            {data.model_performance ? (
              <>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left font-medium">fold</th>
                      <th className="text-right font-medium">train</th>
                      <th className="text-right font-medium">test</th>
                      <th className="text-right font-medium">상관계수</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.model_performance.folds.map((fold) => (
                      <tr
                        key={fold.fold_index}
                        className={fold.fold_index === data.model_fold_index ? 'font-semibold' : ''}
                      >
                        <td className="text-left">{fold.fold_index}</td>
                        <td className="text-right tabular-nums">{fold.n_train.toLocaleString()}</td>
                        <td className="text-right tabular-nums">{fold.n_test.toLocaleString()}</td>
                        <td className="text-right tabular-nums">{formatCorrelation(fold.correlation)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-xs text-muted-foreground">
                  풀링 상관계수: {formatCorrelation(data.model_performance.pooled_correlation)}
                </p>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  {CATEGORY_ORDER.map((label) => (
                    <span key={label}>
                      {label} {formatPct(data.model_performance!.pooled_hit_rate[label])}
                    </span>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">성능 지표 없음(모델을 재학습하면 표시됩니다)</p>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: TypeScript 컴파일 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 로컬에서 사이드카 재학습(성능 지표가 실제로 채워진 모델 생성)**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`
Expected: 정상 종료, `data/regime_ml_models/`에 새 `.txt`+`.json` 페어 생성.
생성된 `.json` 파일에 `performance` 키가 있는지 확인:

Run: `python -c "import json,glob; f=sorted(glob.glob('data/regime_ml_models/*.json'))[-1]; d=json.load(open(f, encoding='utf-8')); print(f); print(list(d.keys())); print(d['performance']['pooled_correlation'])"`
Expected: `performance`가 키 목록에 있고, `pooled_correlation`이 숫자 또는 `None` 출력.

- [ ] **Step 6: 개발 서버 기동 후 브라우저에서 확인**

Run: `cd frontend && npm run dev` (백그라운드로 실행하거나 별도 터미널)
백엔드도 실행 중이어야 함(`uvicorn backend.main:app --reload` 등 기존 실행 방식 사용).

브라우저에서 `/regime` 탭 진입 → 코인 KRW-BTC/ETH/XRP 중 하나 + 1시간봉 선택 →
"ML 현재예측" 카드 하단에 "모델 성능" 섹션이 나타나는지 확인:
- fold 표에 행이 1개 이상 있고, `model_fold_index`와 같은 fold 행이 굵게 표시되는지
- "풀링 상관계수: 0.xxx" 형식으로 숫자가 보이는지(계산 불가 시 "-")
- 카테고리별 hit-rate 5개가 퍼센트로 보이는지

Expected: 위 4가지가 화면에서 육안으로 확인됨. 콘솔에 새 에러 없음.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/types/eda.ts frontend/components/RegimeMlCurrentPrediction.tsx
git commit -m "$(cat <<'EOF'
feat: ML 현재예측 카드에 모델 성능(fold별 상관계수+풀링 hit-rate) 섹션 추가

backend/regime_ml_service.py가 노출하는 model_performance를 표로 렌더링(A2 3/3).
EOF
)"
```

---

## 완료 후 확인

- `docs/regime-ml-backlog.md`의 A2 항목은 이 계획 완료로 해소됨. 백로그 파일 자체
  갱신은 사용자 판단(다음 우선순위 E로 넘어가기 전, 완료 표시 여부 확인 권장).
- `push_regime_ml_model.sh`로 AWS에 배포하기 전, Step 5에서 재학습한 로컬 모델이
  최신인지 확인(코드와 모델을 항상 함께 배포 — `upbit-v1-regime-ml-backlog-cleanup`
  메모리의 교훈).
