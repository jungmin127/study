# 업비트 백테스팅 엔진 설계 (룰 기반, 결과 캐싱 포함)

- 작성일: 2026-07-12
- 상태: 승인 대기 (사용자 리뷰 전)
- 선행 문서: `2026-07-12-upbit-local-cache-design.md` (서브프로젝트 1 — 로컬 시세 캐시, 본 스펙은 그 결과물인 `get_candles()`를 입력으로 사용)

## 배경 및 목적

`2026-07-12-upbit-local-cache-design.md`(서브프로젝트 1)는 과거 시세를 로컬 Parquet 캐시에 쌓는 수단만 다뤘다. 그 데이터를 가지고 실제로 "백테스팅을 통한 매매 룰 설정과 모델링"을 하는 부분은 범위 밖이었다.

이번 스펙은 그 다음 단계, 즉 캐시된 시세로 규칙 기반 전략을 실행하고 성과를 평가하는 백테스팅 엔진을 다룬다. 목표는 규칙 기반 전략부터 시작해 점차 통계/ML 모델링으로 확장하는 것이며, 이번 스펙은 그 첫 단계(규칙 기반)에 한정한다.

과거 중단했던 `backtesting_1` 프로젝트(`C:\Users\jungm\project\backtesting_1`)는 Next.js + FastAPI + SQLite + backtrader로 만든 풀스택 웹앱(노코드 전략 빌더 UI 포함)이었다. 이번 프로젝트는 로컬 캐시와 동일하게 "인프라 최소화" 기조를 따르므로, backtrader 엔진 로직(`engine/runner.py`)은 그대로 재사용하되 웹 서버·UI·동적 전략 생성 레이어는 두지 않는다.

## 스코프

- **서브프로젝트 1 — 로컬 시세 캐시**: 완료(별도 문서).
- **서브프로젝트 2 — 룰 기반 백테스팅 엔진 (본 스펙의 범위)**: 로컬 스크립트/노트북에서 `bt.Strategy`를 직접 작성해 실행하고, 결과를 로컬 SQLite에 캐싱해 동일 조건 재실행을 피한다.
- **서브프로젝트 3 — 통계/ML 모델링 (향후, 범위 밖)**: 룰 기반 엔진이 안정화된 뒤 별도 스펙으로 설계. 가격 데이터에서 피처를 뽑아 방향성/수익률을 예측하는 모델 학습·검증.
- **서브프로젝트 4 — 자동매매 엔진 (향후, 범위 밖)**: 상시 구동 인프라 필요, 별도 설계.

## 이전 프로젝트(backtesting_1)와의 차이

| 항목 | backtesting_1 | 이번 설계 |
|---|---|---|
| 실행 형태 | Next.js UI → FastAPI → backtrader | Python 스크립트/노트북에서 함수 직접 호출 |
| 전략 정의 | UI의 JSON 조건 트리 → `strategy_builder.py`가 동적으로 `bt.Strategy` 생성 | `bt.Strategy` 서브클래스를 코드로 직접 작성 |
| 결과 저장 | SQLite (전략/실행/결과/거래 테이블, 히스토리 UI 제공) | SQLite (동일 스키마 계열, UI 없이 조회는 SQL/노트북) |
| 재실행 방지 | 없음(요청 시 항상 재실행) | 캐시 키(전략 소스+파라미터+데이터 조건) 기준 hit 시 재실행 생략 |
| 백테스팅 엔진 코어 | backtrader (`engine/runner.py`) | 동일 — `runner.py`를 그대로 이식 |

## 아키텍처

```
get_candles()  (서브프로젝트 1)
    │  DataFrame [candle_time, open, high, low, close, volume]
    ▼
run_backtest_cached(df, strategy_cls, risk_config, strategy_params)
    │
    ├─ 캐시 키 계산 (strategy 소스코드 + params + market/timeframe/기간 + risk_config 해시)
    ├─ data/backtest_results.db (SQLite) 조회
    │     hit  → 저장된 결과 반환, backtrader 실행 안 함
    │     miss → 아래 실행
    │
    ├─ run_backtest(df, strategy_cls, risk_config, strategy_params)   ← backtesting_1/engine/runner.py 이식
    │     bt.Cerebro 설정 → 전략 실행 → EquityAnalyzer/TradeLogger/Sharpe/DrawDown 추출
    │
    └─ 결과를 SQLite에 저장 후 반환
```

- 모듈: `upbit_backtest_engine.py` (또는 `engine/runner.py` + `engine/cache.py`로 분리 — 구현 단계에서 결정)
- `runner.py`의 `PandasDataWithExtra`, `FractionalPercentSizer`, `EquityAnalyzer`, `TradeLogger`, `run_backtest()`는 그대로 재사용. 컬럼명만 캐시 모듈 출력(`candle_time`)에 맞춰 `open_time` 대신 매핑.
- 전략 등록 레이어(`strategy_builder.py`, JSON 조건 트리)는 만들지 않는다. UI가 없으므로 이 변환 레이어는 불필요한 간접 계층이다.

## 인터페이스

```python
def run_backtest_cached(
    df: pd.DataFrame,              # get_candles()의 반환값
    strategy_cls: type[bt.Strategy],
    risk_config: dict,             # initial_capital, commission_rate, position_sizing,
                                    # position_size, stop_loss, take_profit, trailing_stop
    strategy_params: dict | None = None,
) -> dict:
    """
    캐시 hit 시 저장된 결과를 그대로 반환.
    miss 시 backtrader로 실행 후 SQLite에 저장하고 반환.

    반환: {
        "equity_curve": list[{timestamp, value}],
        "trades": list[dict],
        "final_value": float,
        "sharpe": float,
        "max_drawdown": float,
        "from_cache": bool,
    }
    """
```

## 캐시 키

캐시 키는 다음을 해시(SHA-256)해 만든다:

- `inspect.getsource(strategy_cls)` — 전략 클래스 소스코드 전체. 로직이 바뀌면 자동으로 새 키가 되어 재실행됨(버전 이름 관리 불필요).
- `strategy_params` (정렬된 JSON)
- `market`, `timeframe`, `start`, `end` (`df`가 커버하는 요청 조건 — 호출부에서 함께 전달)
- `risk_config` (정렬된 JSON)

동일한 조합으로 다시 호출하면 SQLite에서 캐시 hit으로 바로 결과를 반환한다. 파라미터, 기간, 전략 코드 중 하나라도 바뀌면 새로운 캐시 항목으로 취급해 재실행한다.

## 데이터 저장소 (SQLite)

파일: `data/backtest_results.db` (서버 프로세스 없음, 단일 파일).

- `backtest_runs`: `id`(=캐시 키), `strategy_name`, `params_json`, `market`, `timeframe`, `start`, `end`, `risk_config_json`, `created_at`
- `backtest_results`: `run_id`, `final_value`, `sharpe`, `max_drawdown`
- `trades`: `run_id`, `entry_time`, `exit_time`, `entry_price`, `exit_price`, `return_rate`, `holding_period`, `pnl`, `force_closed`
- `equity_curve`: `run_id`, `timestamp`, `value` (또는 JSON 컬럼 하나로 단순 저장 — 구현 단계에서 결정)

## 데이터 흐름

1. 호출부가 `get_candles()`로 데이터를 가져오고, `run_backtest_cached()`에 전략 클래스·파라미터·리스크 설정과 함께 전달.
2. 캐시 키 계산.
3. SQLite `backtest_runs`에서 캐시 키 조회.
   - hit → `backtest_results`, `trades`, `equity_curve`에서 결과 로드해 반환 (`from_cache=True`).
   - miss → 4번으로.
4. `run_backtest()`(backtrader) 실행.
5. 결과를 SQLite 3개 테이블에 저장.
6. 결과 반환 (`from_cache=False`).

## 에러 처리

- 데이터 수집 실패는 이미 서브프로젝트 1(`get_candles`)에서 처리됨 — 이 레이어는 관여하지 않는다.
- 전략 코드 실행 중 예외(backtrader 런타임 에러 등)는 캐시에 저장하지 않고 그대로 호출부에 전파한다. 실패한 실행을 "성공한 결과"로 캐시하면 이후 동일 조건 조회 시 잘못된 결과를 반환하게 되므로 금지.
- SQLite 파일이 없으면 최초 호출 시 자동 생성(캐시 모듈의 Parquet 디렉토리 자동 생성과 동일한 패턴).

## 테스트

- 캐시 키 결정성 테스트: 동일 입력(전략 코드+파라미터+조건) → 동일 키, 파라미터/코드/기간 중 하나라도 다르면 다른 키.
- 캐시 hit/miss 통합 테스트: 첫 호출은 miss(backtrader 실행), 동일 조건 재호출은 hit(backtrader 미실행, `from_cache=True`).
- 전략 코드 변경 시 자동 재실행 테스트: 클래스 본문을 수정하면(같은 이름이라도) miss로 처리되는지 확인.
- 실제 전략 1개 + 짧은 기간으로 스모크 테스트 — `equity_curve`/`trades`/`sharpe`/`max_drawdown`이 정상 계산되고 SQLite에 저장되는지 확인.

## 향후 확장 (본 스펙 범위 밖)

- 서브프로젝트 3(통계/ML 모델링): 캐시된 시세에서 피처를 추출해 방향성/수익률을 예측하는 모델을 학습·검증. 본 스펙의 룰 기반 엔진과는 별도 파이프라인이 되거나, 예측 신호를 `bt.Strategy` 안에서 매매 조건으로 사용하는 형태로 연결될 수 있음 — 상세는 규칙 기반 엔진이 안정화된 뒤 별도 설계.
- 서브프로젝트 4(자동매매 엔진): 백테스팅으로 검증된 룰/모델을 상시 구동 환경에서 실행. 본 스펙과는 별개로 실시간 시세 경로가 필요.
- 캐시 결과 조회/비교를 위한 CLI나 간단한 노트북 헬퍼가 필요해지면 그때 추가 — 이번 스펙에서는 SQL 직접 조회로 충분하다고 가정.
