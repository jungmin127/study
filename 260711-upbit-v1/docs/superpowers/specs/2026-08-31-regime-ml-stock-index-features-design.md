# 장세 판별 ML — 주가지수 수익률 피처 추가 설계 (2026-08-31)

## 배경

`docs/regime-ml-backlog.md` 우선순위0 액션아이템 2번 — 사용자가 제안한
코스피/코스닥/S&P500/다우존스/나스닥 지수 피처를 시도한다. 현재 baseline은
캘린더3+환율1 신호가 채택된 상태의 pooled weighted kappa **0.106**
(`docs/superpowers/plans/2026-08-31-regime-ml-safe-signal-retry.md` 결과).

**과거 교훈**: `LISTING_AGE_BARS`/`FEAR_GREED_CMC`/캘린더8개/환율3개/금리3개 —
전 마켓 공유 시계열 또는 레벨(절대 수준)/횡단면 스프레드 형태의 피처는
walk-forward fold와 얽혀 성능을 깎아먹는 패턴이 5번 반복 확인됐다
(`docs/regime-ml-backlog.md` 우선순위0 조사 결론). 진짜 원인은 "공유 여부"가
아니라 "레벨이냐 진짜 시간축 차분(수익률)이냐"다 — 반드시 수익률 형태로만
시도한다.

## 사전 조사 — eta² 측정 (완료, 이 설계에 선행)

우선순위0과 동일 방법론(KRW-ETH 1개 마켓, 2024-01-01~현재, 5-fold,
`eta² = 피처값의 fold간 분산 / 전체 분산`)으로 FRED 일간 종가의 `pct_change()`를
측정(scratchpad 1회성 스크립트, 커밋 안 함):

| 피처 | eta²(fold) | 판정 |
|---|---|---|
| `SP500_RETURN`(FRED SP500) | 0.0002 | 안전 |
| `DJIA_RETURN`(FRED DJIA) | 0.0002 | 안전 |
| `NASDAQ_RETURN`(FRED NASDAQCOM) | 0.0002 | 안전 |

세 지수 모두 `USDKRW_RETURN`(0.0001, 이미 채택)과 동급으로 안전 — 백로그가
예상했던 "환율/금리 중간 위험도"보다 훨씬 낮다. 순수 t 대 t-1 수익률(differencing)
형태라 결론3(진짜 효과 있는 변환은 시간축 차분)이 그대로 적용된 것으로 해석된다.

## 범위

**포함**: S&P500(`SP500_RETURN`), 다우존스(`DJIA_RETURN`), 나스닥종합(`NASDAQ_RETURN`)
3개 — FRED가 무료·키불필요 일간 종가 시리즈로 전부 제공.

**제외(이번 라운드 아님)**: 코스피/코스닥 — FRED에 없음. 조사 중 Stooq(무의존성
CSV 대안)가 JS 프루프오브워크 봇차단으로 막혀 있는 것을 확인(2026-08-31 실측,
`curl`로 HTML 챌린지 페이지만 응답). yfinance(Yahoo 비공식 API)는 실제 동작
확인했으나 신규 pip 의존성이라 사용자가 이번 라운드는 보류 결정 —
`docs/regime-ml-backlog.md`에 별도 후보로 기록.

## 데이터 소스 — `macro_data_service.py` 확장

기존 `_fetch_fred_csv`/`_parse_fred_csv`/`_get_fred_series`/`merge_fred_series`
패턴을 그대로 재사용(4번째~6번째 FRED 시리즈 추가일 뿐, 함수 구조 변경 없음).

- `SP500_SERIES_ID = "SP500"` — S&P500 일간 종가
- `DJIA_SERIES_ID = "DJIA"` — 다우존스 산업평균 일간 종가
- `NASDAQ_SERIES_ID = "NASDAQCOM"` — 나스닥종합지수 일간 종가

```python
def get_sp500_index(start: datetime, end: datetime) -> pd.DataFrame: ...   # columns: date, sp500_close_value
def get_djia_index(start: datetime, end: datetime) -> pd.DataFrame: ...    # columns: date, djia_close_value
def get_nasdaq_index(start: datetime, end: datetime) -> pd.DataFrame: ...  # columns: date, nasdaq_close_value
```

`_get_fred_series`를 그대로 호출(캐시 파일명만 `fred_sp500`/`fred_djia`/`fred_nasdaq`로
추가), `merge_fred_series`도 그대로 재사용 — 신규 헬퍼 불필요.

## 아키텍처 — 데이터 흐름

1. `engine/regime_ml_data.py::load_market_training_data`에 기존 4개 fetch+merge
   호출 다음, 3개 신규 fetch+merge를 추가 — 원시 컬럼 `sp500_close_value`,
   `djia_close_value`, `nasdaq_close_value`를 df에 붙인다.
2. `engine/regime_ml_features.py::build_feature_matrix`가 원시 컬럼의
   `pct_change(fill_method=None)`만 계산한다(`USDKRW_RETURN`과 완전히 동일한
   패턴) — 레벨이나 지수간 스프레드는 만들지 않는다:
   ```python
   features["SP500_RETURN"] = df["sp500_close_value"].pct_change(fill_method=None)
   features["DJIA_RETURN"] = df["djia_close_value"].pct_change(fill_method=None)
   features["NASDAQ_RETURN"] = df["nasdaq_close_value"].pct_change(fill_method=None)
   ```

## 검증 방법론 (기존 관행 재사용)

- `scripts/train_regime_ml.py` 실데이터 walk-forward 재학습, pooled weighted
  kappa 1순위·macro F1 2순위. baseline **0.106**.
- 3개 지수를 한 그룹으로 묶어 추가 → 실측 → 개선/유지되면 채택, 악화되면
  `git revert HEAD`로 즉시 폐기(재확인 질문 없이 자동 진행 — 기존 라운드
  정책과 동일).
- **eta²가 사전에 셋 다 거의 동일하게 안전(0.0002)했으므로, 그룹 채택 시
  leave-one-out은 생략**한다 — 개별 위험도 차이가 없어 그룹 내부에
  `LISTING_AGE_BARS`류 숨은 위험 신호가 섞여 있을 가능성이 낮다(안전
  신호 재시도 라운드와 동일 판단 기준).
- 최종 채택 피처 세트의 AWS 배포 여부는 검증 종료 후 별도로 사용자에게
  확인(`[[upbit-v1-dont-push-on-empirical-regression]]`,
  `[[upbit-v1-deploy-check-open-positions-first]]` 원칙).

## 알려진 한계 (수정 안 함, 기존 패턴 재사용)

`merge_fred_series`가 FRED CSV의 `date`를 그 날짜 자정(UTC)으로 두고
`merge_asof(backward)`로 병합한다. 미국장 실제 종가 확정 시각(미 동부 4pm ET ≈
UTC 21시경)보다 최대 21시간 먼저 그날 종가가 "이미 나온 값"처럼 붙는 룩어헤드가
있다 — 이미 `T10Y2Y`/`USDKRW`(Frankfurter)에도 동일하게 존재하는 기존 구조적
한계이며, 이번 라운드에서 새로 만드는 문제는 아니다. 사용자 결정(2026-08-31):
기존 패턴 그대로 재사용, 이번 라운드에서 수정하지 않는다.

## 에러 처리

- 개별 fetch 실패 시 해당 컬럼 NaN 처리 후 계속 진행 — 기존 4개 FRED/Frankfurter
  시리즈와 동일 패턴. API 키 불필요라 키 미설정 실패 케이스 없음.

## 테스트

- `tests/test_macro_data_service.py`: 신규 3개 fetch 함수의 캐싱/파싱 (기존
  `get_fed_funds_rate` 등 테스트 패턴 재사용).
- `tests/test_regime_ml_features.py`: `SP500_RETURN`/`DJIA_RETURN`/`NASDAQ_RETURN`이
  `pct_change(fill_method=None)`과 일치하는지 검증(`USDKRW_RETURN` 테스트와
  동일 패턴).
- `tests/test_regime_ml_data.py`: `load_market_training_data`가 3개 신규 원시
  컬럼을 포함해 반환하는지 확인.
- 알려진 gotcha: `tests/test_train_regime_ml.py`/`tests/test_regime_ml_service.py`의
  합성 fixture가 새 raw 컬럼 부재로 깨질 수 있음(이전 라운드에서 반복 발생) —
  발생 시 각 파일에 컬럼 한 줄씩 최소 침습으로 추가.

## 범위 밖

- 코스피/코스닥 지수 피처 — 데이터 소스 미정(yfinance 신규 의존성 필요),
  별도 세션에서 데이터 소스부터 재조사.
- c-2(로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝) — 별도 진행.
- 룩어헤드(최대 21시간) 수정 — 사용자 결정으로 이번 스코프 아님.
- 최종 채택 피처 세트의 AWS 라이브 배포 실행 — 검증 종료 후 별도 확인.
