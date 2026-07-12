# 서브프로젝트 2-1 — 백테스팅 전략 EDA 대시보드 설계

- 작성일: 2026-07-12
- 상태: 승인 대기 (사용자 리뷰 전)
- 선행 문서: `2026-07-12-upbit-local-cache-design.md`(서브1), `2026-07-12-upbit-backtest-engine-design.md`(서브2) — 둘 다 구현 완료

## 배경 및 목적

서브2(룰 기반 백테스팅 엔진)가 완료되어 `get_candles()` + `run_backtest_cached()`로 개별 백테스트는 실행할 수 있지만, "어떤 전략을 어떤 코인·봉타입에 썼을 때 성과가 좋은가"를 한눈에 비교하거나 시간에 따라 추적할 수단은 없다.

다음 단계인 서브3(통계/ML 모델링)로 넘어가기 전에, 주요 룰 기반 전략들에 대한 EDA(탐색적 데이터 분석) 성격의 작업이 필요하다:

- 전략×코인×봉타입 조합별 수익률을 반복 실행해도 매번 새로 계산하지 않고 히스토리로 쌓아 추적하고 싶다.
- 여러 전략을 섞었을 때(신호 결합) 어떤 코인이 가장 성과가 좋은지, 사람이 일일이 비교하지 않아도 자동으로 계산해주는 프레임워크가 필요하다.
- 이 결과들을 웹 대시보드로 지속적으로 확인하고 싶다.

**이번 스펙의 목적**: 신호(지표) 기반 전략 여러 개를 코인×봉타입 조합에 걸쳐 스윕 실행하고, 결과 히스토리를 저장하며, FastAPI+Next.js 대시보드로 조회하는 서브프로젝트를 설계한다.

## 스코프

- **서브1 (로컬 시세 캐시)**: 완료.
- **서브2 (룰 기반 백테스팅 엔진)**: 완료.
- **서브2-1 (백테스팅 전략 EDA 대시보드, 본 스펙의 범위)**: 신호 세트 3~4개 구현, 스윕 프레임워크, 히스토리 저장, FastAPI+Next.js 대시보드(4개 화면 + 서브3용 플레이스홀더 탭 1개).
- **서브3 (통계/ML 모델링, 향후·범위 밖)**: 대시보드에 "모델 정확도" 탭 자리만 예약해둔다. 실제 예측값/실측값 비교, 정확도 지표, 스키마, API는 서브3을 별도로 설계할 때 채운다.
- **서브4 (자동매매 엔진, 향후·범위 밖)**: 변경 없음.

## 이전 설계와의 관계 / 재사용

- `get_candles()`(서브1), `run_backtest_cached()`(서브2)를 그대로 소비한다 — 캐시 hit이면 재실행 없이 즉시 반환되므로, 스윕을 반복 실행해도 API 호출이나 backtrader 실행이 중복되지 않는다.
- 서브2의 "전략은 `bt.Strategy`를 코드로 직접 작성, JSON 조건 트리 없음" 기조를 유지한다. 신호도 설정 파일이 아니라 Python 함수/클래스로 직접 등록한다.
- 서브2의 `backtest_results.db`(SQLite)에 테이블 하나(`sweep_history`)만 추가한다 — 별도 DB 파일을 만들지 않는다.

## 아키텍처

```
signals.py (신규)                         engine/sweep.py (신규)
  - MacdCrossSignal                          run_sweep(markets, timeframes,
  - RsiReboundSignal                                     signal_sets, start, end,
  - SmaCrossSignal                                       risk_config)
  - BollingerBandSignal                        │
                                                ├─ 각 (market, timeframe, signal_set) 조합마다
engine/strategies.py (신규)                    │    get_candles() → run_backtest_cached()
  - SignalStrategy(bt.Strategy)                └─ 결과를 sweep_history 테이블에 INSERT (append-only)
      signals: list[Signal]
      매수: 모든 signal.should_buy() 참(AND)
      매도: 하나라도 signal.should_sell() 참(OR)

backend/ (FastAPI)                          frontend/ (Next.js App Router + shadcn/ui + Tailwind)
  - GET /api/v1/eda/heatmap                    ① 전략×코인×봉타입 수익률 테이블/히트맵
  - GET /api/v1/eda/ranking                    ② 혼합전략 코인 랭킹
  - GET /api/v1/eda/history                    ③ 특정 조합 시간대별 수익률 추이
  - GET /api/v1/backtests/{run_id}              ④ equity curve / 거래내역 상세
                                                ⑤ 모델 정확도 (플레이스홀더, "서브3 진행 후 공개")
```

- **신호(Signal) 인터페이스**: 각 신호는 (a) `setup(strategy)` — `__init__`에서 필요한 `bt.indicators.*`를 등록, (b) `should_buy(strategy) -> bool`, (c) `should_sell(strategy) -> bool` 세 메서드를 갖는다. 단독 실행(View: 개별 전략 비교)과 혼합 실행(View: 혼합전략 랭킹) 모두 신호 개수만 다를 뿐 같은 `SignalStrategy` 클래스를 쓴다 — 별도의 "단일 전략용 클래스"를 두지 않는다(신호 1개짜리 `SignalStrategy`가 곧 단독 실행).
- **초기 신호 4개**: MACD 골든크로스(매수)/데드크로스(매도), RSI 과매도(30) 반등(매수)/과매수(70) 하회(매도), SMA 단기·장기 골든크로스(매수)/데드크로스(매도), 볼린저밴드 하단 이탈 후 복귀(매수)/상단 이탈(매도). 각각 독립 모듈 함수/클래스로 등록되므로 이후 신호 추가가 쉽다(등록 리스트에 추가만 하면 됨).
- **`engine/sweep.py`**: 호출부가 넘긴 `markets`(코인 리스트, 서브1과 동일하게 설정 파일 없이 인자로 전달)와 `timeframes` 리스트, `signal_sets`(개별 신호 리스트 + 혼합 신호 조합 리스트를 함께 넘김)를 전부 조합해 반복 실행. 캐시는 `run_backtest_cached()`가 담당하므로 `run_sweep()`을 반복 호출해도 새 조합만 실제로 실행된다.
- **`sweep_history` 테이블**(append-only): `id, run_id(FK→backtest_runs.id), signal_set_name, is_combined, market, timeframe, start, end, return_rate, sharpe, max_drawdown, swept_at`. 같은 조합을 여러 날짜에 스윕하면 매번 새 row가 쌓여 시간대별 추이(③ 화면)를 그릴 수 있다.
- **백엔드**: FastAPI가 `engine/sweep.py`, `engine/cache.py`를 직접 import해 SQLite를 조회한다. 스윕 실행은 API가 아니라 스크립트에서 `run_sweep()`을 직접 호출하는 방식으로 시작한다(대시보드 버튼 트리거는 향후 확장).
- **프론트엔드**: `backtesting_1`과 동일한 스택(Next.js App Router, shadcn/ui, Tailwind, TradingView Lightweight Charts)을 재사용한다.

## 인터페이스

```python
# signals.py
class Signal(Protocol):
    def setup(self, strategy: bt.Strategy) -> None: ...
    def should_buy(self, strategy: bt.Strategy) -> bool: ...
    def should_sell(self, strategy: bt.Strategy) -> bool: ...

# engine/strategies.py
class SignalStrategy(bt.Strategy):
    """signals가 1개면 단독 전략, 여러 개면 매수 AND / 매도 OR로 결합된 혼합 전략."""
    params = (("signals", []),)

# engine/sweep.py
def run_sweep(
    markets: list[str],
    timeframes: list[str],
    signal_sets: list[tuple[str, list[Signal]]],  # (표시용 이름, 신호 리스트)
    start: datetime,
    end: datetime,
    risk_config: dict,
) -> None:
    """조합별로 get_candles() + run_backtest_cached() 실행 후 sweep_history에 기록."""
```

## 대시보드 화면 (프론트엔드)

1. **전략×코인×봉타입 수익률 테이블/히트맵**: `sweep_history`에서 조합별 최신 `return_rate`를 조회해 표/히트맵으로 표시.
2. **혼합전략 코인 랭킹**: `is_combined=true`인 행만 필터링해 `return_rate` 내림차순 정렬.
3. **조합별 시간대 수익률 추이**: 특정 (signal_set, market, timeframe) 조합의 `sweep_history` 전체 행을 시간순으로 그래프.
4. **백테스트 상세**: 테이블 행 클릭 → `run_id`로 `backtest_results`에서 equity curve/거래내역 조회해 표시.
5. **모델 정확도 (플레이스홀더)**: 네비게이션에 탭만 존재, "서브3 진행 후 공개" 안내만 표시. 스키마/API/차트는 서브3 설계 시 별도 작업.

## 데이터 흐름

1. 스크립트에서 `run_sweep(markets=[...], timeframes=[...], signal_sets=[...], ...)` 호출.
2. 각 조합에 대해 `get_candles()`로 데이터 확보 → `run_backtest_cached()`로 백테스트(캐시 hit이면 backtrader 미실행) → 결과를 `sweep_history`에 새 row로 INSERT.
3. FastAPI가 `sweep_history`/`backtest_results`를 조회해 API로 제공.
4. Next.js 대시보드가 API를 호출해 5개 탭(4개 활성 + 1개 플레이스홀더) 렌더링.

## 에러 처리

- `run_sweep()` 도중 특정 조합에서 예외(예: 존재하지 않는 마켓 코드)가 발생해도 전체 스윕이 중단되지 않고, 해당 조합만 건너뛰고 로그를 남긴 뒤 나머지 조합을 계속 진행한다 — 서브2의 "실패한 실행은 캐시하지 않는다" 규칙과 별개로, 스윕 레벨에서는 부분 실패를 허용한다.
- FastAPI 엔드포인트는 `sweep_history`/`backtest_results`가 비어 있으면(아직 스윕을 한 번도 안 돌린 경우) 빈 배열을 반환하고, 프론트엔드는 "아직 데이터 없음" 상태를 표시한다.

## 테스트

- 신호별 `should_buy`/`should_sell` 단위 테스트 — 합성 OHLCV 데이터로 각 신호가 의도한 시점에 정확히 발동하는지 확인.
- `SignalStrategy`가 신호 1개일 때(단독)와 여러 개일 때(매수 AND/매도 OR)의 동작 차이를 확인하는 테스트.
- `run_sweep()`이 조합별로 `sweep_history`에 정확히 한 row씩 추가하는지, 같은 조합을 반복 실행하면 캐시가 재사용되면서도 `sweep_history`에는 새 row가 계속 쌓이는지(append-only) 확인하는 테스트.
- FastAPI 엔드포인트 통합 테스트(TestClient) — 각 화면이 소비하는 API가 예상 스키마를 반환하는지.
- 프론트엔드는 수동 스모크 테스트(로컬에서 `npm run dev` + `uvicorn` 띄운 뒤 4개 화면 육안 확인)로 시작.

## 향후 확장 (본 스펙 범위 밖)

- 매도 결합 규칙을 OR 외에 AND/가중치까지 선택 가능하게 확장.
- 스윕 대상 코인을 호출부 지정 리스트가 아니라 Upbit KRW 마켓 전체 자동 조회로 확장.
- 대시보드에서 직접 스윕을 트리거하는 버튼/API 추가(지금은 스크립트에서 수동 실행).
- 서브3(모델링): "모델 정확도" 탭의 실제 내용 — 예측값 lineplot, 실측값과의 오차 추적. 예측 대상(가격/방향성/수익률), 예측 주기, 정확도 지표(MAE/방향 적중률 등)를 서브3 설계 시 별도로 브레인스토밍한다.
