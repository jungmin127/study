# 장세 판별 ML — 캘린더/환율/금리 피처 추가 설계 (2026-08-31)

## 배경

`docs/regime-ml-backlog.md`의 잔여 후보 c-2(로지스틱회귀 baseline + LightGBM
하이퍼파라미터 튜닝) 착수 전에, 사용자가 새로 요청한 신규 피처 축(캘린더/시간,
거시경제 지표)을 먼저 실험하기로 결정. 기존 피처(`engine/regime_ml_features.py`)는
가격/거래량 기반 기술지표와 코인 자기상대적 피처(변동성/유동성 백분위,
베타중립/cross-sectional)만 다루고 있고, 캘린더성·거시경제 축은 아직 없다.

**과거 교훈 — 반드시 지킬 원칙**: `LISTING_AGE_BARS`(상장 후 경과봉)와
`FEAR_GREED_CMC`(전 마켓 공유 매크로 시계열)는 둘 다 "그럴듯해 보이지만 실제로는
워크포워드 fold 위치를 암묵적으로 알려주는 프록시"로 작동해 제거가 오히려
성능을 올렸다(`engine/regime_ml_features.py` 모듈 독스트링 참고). 이번에 추가하는
피처도 예외 없이 **ablation으로 개별 검증 후에만 채택**한다 — "이론적으로
타당해 보임"은 채택 근거가 되지 않는다.

현재 baseline: 20마켓 풀링, 이진분류(하락/하락아님), walk-forward pooled
weighted kappa **0.096**(②모델 성능 개선 라운드 종료 시점, `docs/regime-ml-backlog.md`).

## 범위 — 신규 피처 후보 (3개 카테고리, 14개)

| 카테고리 | 피처명 | 정의 |
|---|---|---|
| 캘린더(KST) | `HOUR_SIN`/`HOUR_COS` | 시간대(0~23) 주기인코딩 |
| | `DOW_SIN`/`DOW_COS` | 요일(월~일) 주기인코딩 |
| | `MONTH_SIN`/`MONTH_COS` | 월(1~12) 주기인코딩 |
| | `DAY_OF_MONTH_SIN`/`DAY_OF_MONTH_COS` | 월중 일자(1~31) 주기인코딩 |
| 환율 | `USDKRW_RETURN` | 공식 USD/KRW 환율의 수익률(pct_change) |
| | `USDKRW_VOLATILITY` | 위 수익률의 EWM 변동성(halflife=half_life_bars) |
| | `UPBIT_FX_SPREAD` | 업비트 암묵환율(`usdt_close`) vs 공식환율의 괴리율(%) |
| 금리 | `US_KR_RATE_SPREAD` | 미국 기준금리 − 한국 콜금리(대리지표) |
| | `YIELD_CURVE_SPREAD` | 미국 10Y-2Y 국채금리차 |
| | `HOURS_SINCE_RATE_DECISION` | 미국/한국 금리 시계열 중 더 최근에 값이 바뀐 시점으로부터 경과 시간(시간 단위) |

캘린더 8 + 환율 3 + 금리 3 = 14개.

## 데이터 소스 — `macro_data_service.py` (신규)

두 provider 모두 **API 키 불필요**, `external_data_service.py`의 캐싱(parquet)
+ 재시도 + `merge_asof(direction="backward")` 패턴을 그대로 재사용한다.

### FRED (Federal Reserve Economic Data)

- 엔드포인트: `https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES_ID}&cosd={start}&coed={end}`
- 시리즈:
  - `FEDFUNDS` — 미국 연방기금금리(월간)
  - `T10Y2Y` — 미국 10Y-2Y 국채금리차(일간)
  - `IRSTCI01KRM156N` — 한국 콜금리/은행간금리(월간, OECD 경유). **한국은행
    기준금리 자체가 아니라 콜금리 대리지표**임을 코드 주석에 명시한다 —
    2008년 한국은행이 콜금리를 기준금리의 운영목표로 직접 관리하기 시작한
    이후로는 두 값이 사실상 동일하게 움직이지만, 완전히 같은 수치는 아니다.
- 월간 시리즈(FEDFUNDS, IRSTCI01KRM156N)는 월초 값이 그 달 내내 유효한 것으로
  보고 `merge_asof(backward)`로 시간봉에 전파한다.

### Frankfurter (구 frankfurter.app, 현재 frankfurter.dev)

- 엔드포인트: `https://api.frankfurter.dev/v1/{start}..{end}?from=USD&to=KRW`
- ECB 기준 공식 환율, 영업일 기준 일 1회 갱신(주말·공휴일 결측 — `merge_asof(backward)`로
  자연 처리).
- 과거 스펙(`docs/superpowers/specs_v1/2026-07-29-korea-premium-external-data-design.md`)에서
  조사만 되고 실제 구현은 안 된 상태였음 — 이번에 처음 구현.

### 함수 시그니처 (초안)

```python
def get_fed_funds_rate(start: datetime, end: datetime) -> pd.DataFrame: ...       # columns: date, fed_funds_rate_value
def get_us_yield_curve_spread(start: datetime, end: datetime) -> pd.DataFrame: ... # columns: date, treasury_yield_spread_value
def get_kr_call_rate(start: datetime, end: datetime) -> pd.DataFrame: ...          # columns: date, kr_call_rate_value
def get_usdkrw_rate(start: datetime, end: datetime) -> pd.DataFrame: ...           # columns: date, usdkrw_rate_value

def merge_fred_series(df: pd.DataFrame, series_df: pd.DataFrame, value_col: str) -> pd.DataFrame: ...  # merge_asof backward, 4개 함수가 공유
def merge_usdkrw_rate(df: pd.DataFrame, rate_df: pd.DataFrame) -> pd.DataFrame: ...
```

`merge_fred_series`를 공용 헬퍼로 두고 4개 시리즈가 재사용 — `external_data_service.merge_fear_greed`와
동일한 merge_asof 패턴이라 중복 함수 4개를 만들 필요가 없다.

## 아키텍처 — 데이터 흐름

1. `engine/regime_ml_data.py::load_market_training_data`가 기존 aux 데이터(BTC/USDT
   종가, fear&greed, funding rate, korea premium)를 붙이는 자리에 이어서
   `macro_data_service`의 4개 fetch+merge를 호출 — 원시 컬럼
   `fed_funds_rate_value`, `treasury_yield_spread_value`, `kr_call_rate_value`,
   `usdkrw_rate_value`를 df에 추가한다.
2. `engine/regime_ml_features.py::build_feature_matrix`가 이 원시 컬럼과
   `df["candle_time"]`으로 14개 파생 피처를 계산한다(순수 함수, I/O 없음 — 기존
   구조 그대로 유지).
   - 캘린더: `df["candle_time"].dt.tz_convert("Asia/Seoul")`로 KST 변환 후
     `hour`/`dayofweek`/`month`/`day` 추출 → `sin(2*pi*x/period)`/`cos(...)`.
   - `USDKRW_RETURN` = `usdkrw_rate_value.pct_change(fill_method=None)`,
     `USDKRW_VOLATILITY` = 위 수익률의 `.ewm(halflife=half_life_bars).std()`
     (기존 `RAW_SCORE` 계산과 동일한 패턴).
   - `UPBIT_FX_SPREAD` = `(df["usdt_close"] / df["usdkrw_rate_value"] - 1) * 100`
     (`compute_korea_premium_value`와 같은 비율 공식 스타일).
   - `US_KR_RATE_SPREAD` = `fed_funds_rate_value - kr_call_rate_value`.
   - `YIELD_CURVE_SPREAD` = `treasury_yield_spread_value`(이미 스프레드라 그대로 통과).
   - `HOURS_SINCE_RATE_DECISION`: `fed_funds_rate_value`와 `kr_call_rate_value` 각각의
     변화 시점(`diff() != 0`)을 찾아, 각 시점에서 "가장 최근 변화 시점까지의 경과
     시간(시간 단위)"을 계산한 뒤 두 시리즈 중 작은 값(더 최근에 변한 쪽)을 취한다.

## 검증 방법론 (기존 관행 재사용)

- `docs/superpowers/specs_v1/2026-08-31-regime-ml-performance-improvement-design.md`와
  동일한 프로토콜: scratchpad 1회성 스크립트가 `scripts/train_regime_ml.py`의
  walk-forward 루프를 재사용, pooled weighted kappa 1순위·macro F1 2순위 지표.
- 순서: **① 캘린더 8개 그룹 추가 → ② 환율 3개 그룹 추가 → ③ 금리 3개 그룹 추가**,
  각 단계는 직전 단계까지 채택된 피처 위에 누적한다(②모델 성능 개선 라운드와
  동일한 누적 채택 방식).
- 그룹 추가 후 kappa가 개선되면, 그룹 내부에서 개별 피처를 하나씩 빼보는
  leave-one-out으로 그룹 안에 무의미하거나 해로운 피처가 섞여 있는지 확인한다
  (그룹째 채택/폐기만 하면 `LISTING_AGE_BARS`류 숨은 위험 피처를 놓칠 수 있음).
- 각 단계 사이 재확인 질문 없이 자동으로 다음 단계 진행, 세션 끝에 최종 채택
  피처 목록과 최종 kappa를 요약 보고.
- 최종 모델의 AWS 배포 여부는 전체 작업 종료 후 별도로 사용자에게 확인
  (`[[upbit-v1-dont-push-on-empirical-regression]]` 원칙).

## 에러 처리

- 개별 fetch 실패(네트워크 오류, 시리즈 없음 등) 시 해당 컬럼을 NaN으로 채우고
  계속 진행 — 기존 `fear_greed`/`binance` 패턴과 동일, LightGBM이 결측을
  네이티브로 처리하므로 별도 방어 불필요.
- API 키가 없어서 생기는 실패 케이스 자체가 없음(두 provider 모두 키 불필요) —
  ②라운드까지 있었던 "키 미설정 조기 실패" 처리는 이번 설계에서 해당 없음.

## 테스트

- `tests/test_macro_data_service.py`(신규): 4개 fetch 함수의 캐싱/파싱, 공용
  `merge_fred_series`/`merge_usdkrw_rate`의 merge_asof 정합성(미래 데이터가
  과거로 새지 않는지) — `tests/test_external_data_service.py` 패턴 재사용.
- `tests/test_regime_ml_features.py`: 14개 파생 피처 계산 검증(순수함수라 합성
  mock 데이터로 경계값 포함 테스트 — 예: 자정 직전/직후 `HOUR_SIN` 연속성,
  `HOURS_SINCE_RATE_DECISION`이 변화 시점에서 0으로 리셋되는지).
- `tests/test_regime_ml_data.py`: `load_market_training_data`가 4개 신규 원시
  컬럼을 포함해 반환하는지 확인.

## 범위 밖

- c-2(로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝) — 이 라운드
  완료 후 별도 진행.
- c-1(CUSUM 이벤트 샘플링), c-3(메타 레이블링), c-4(threshold 튜닝 실효성 개선) —
  기존 백로그 순서 유지, 이번 스코프 아님.
- ECOS API 연동 — FRED 콜금리 대리지표로 대체하기로 결정, 재검토 시에만 별도 진행.
- 최종 채택 피처 세트의 AWS 라이브 배포 실행 — 검증 종료 후 별도 확인.
