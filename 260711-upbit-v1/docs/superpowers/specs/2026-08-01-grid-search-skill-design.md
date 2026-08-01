# Grid Search 스킬 설계

- 작성일: 2026-08-01
- 상태: 승인 대기 (사용자 리뷰 전)

## 목적

사용자가 `grid search [코인명],[운용자금],[봉데이터],[운용기간],[상위N개]` 형태로 자연어 명령을 내리면(예: `grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20`), 매수 지표 1개 + 매도 지표 1개의 모든 조합에 지표별 threshold 그리드를 적용해 수익률을 계산하고, 상위 N개만 "백테스트 결과"에 저장하는 Claude Code 스킬을 만든다. 연산은 로컬 pandas/backtrader 스크립트가 수행하므로 Claude 토큰 소모는 대화(파싱·보고)에 국한된다 — 이게 이 기능의 핵심 전제.

## 결정된 사항 (사용자 승인)

### 지표 범위 (1단계)

오실레이터 5개(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R — 값이 코인 절대시세와 무관하게 고정 범위에 갇히는 것들) + 매도 전용 3개(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS). 카탈로그의 "오실레이터" UI 카테고리는 실제로 11개(BB_upper/middle/lower, MACD_line/signal, ATR 포함)지만, 이 6개는 값이 코인 절대시세에 종속돼(BB=가격 자체, MACD=스케일이 가격 종속, ATR=변동성 크기) 그리드 설계가 달라지므로 1단계 범위 밖. 가격대(SMA/EMA/BB/FIB/PIVOT/VPVR) 계열, 제로크로스(MACD 등), 상관관계(BTC/USDT_CORRELATION), 외부데이터(FEAR_GREED_CMC/KOREA_PREMIUM), 거래량 계열(OBV 등)도 1단계 범위 밖 — 확장 시 정규화 지표(BB_PERCENT_B, MACD_PPO, ATR_PCT) 추가가 선행 조건.

**범위 확장 시 트레이드오프**: 실측 결과 전체 35개 지표 + 전체 교차 그리드는 ETH/1시간봉/약 1,460캔들 기준 271,405개 조합, 약 23시간 소요. 오실레이터 5개로 좁히면 2,565개 조합, 8.9분. 이 차이 때문에 1단계 범위를 오실레이터 5개로 확정함.

### 그리드 정의

period 그리드는 5개 오실레이터 공통 `[10, 14, 20]`. threshold는 지표별 원래 배경지식 5단계 값 중 처음/중간/끝 3개, 매수(`<`)는 낮은 쪽, 매도(`>`)는 높은 쪽 값을 사용한다.

| 지표 | period | 매수 threshold (`<`) | 매도 threshold (`>`) |
|---|---|---|---|
| RSI | 10, 14, 20 | 20, 30, 40 | 60, 70, 80 |
| STOCH_K | 10, 14, 20 | 10, 20, 30 | 70, 80, 90 |
| STOCH_D | 10, 14, 20 | 10, 20, 30 | 70, 80, 90 |
| CCI | 10, 14, 20 | -140, -100, -60 | 60, 100, 140 |
| WILLIAMS_R | 10, 14, 20 | -90, -80, -70 | -30, -20, -10 |

매도 전용 3종(period 없음, threshold 4단계):

| 지표 | 연산자 | threshold 그리드 |
|---|---|---|
| STOP_LOSS_PCT | `<=` | -3, -5, -7, -10 |
| TAKE_PROFIT_PCT | `>=` | 5, 10, 15, 20 |
| HOLDING_PERIOD_BARS | `>=` | 5, 10, 20, 40 |

매수 조건 45개(5지표×3period×3threshold) × 매도 조건 57개(오실레이터 45 + 매도전용 12) = **2,565개 조합**. 2026-08-01 세션에서 ETH/1시간봉/2026-06-01~현재로 이 그리드 전체를 실제로 돌려 8.9분에 완료, 상위 20개를 `run_backtest_cached()`로 저장까지 검증함(1위: 매수 STOCH_D(p10~20)<10 / 매도 RSI(p14)>80, +15.46%). 이 20개는 제목에 `[Grid]` 접두사가 붙어 지금도 "백테스트 결과"에 남아있고, 이번 구현 완료본과 비교용으로 그대로 둔다.

**정정 (2026-08-01, 플랜 작성 중 발견)**: `engine/indicators/momentum.py`의 `create_stoch_k`/`create_stoch_d`는 `period`가 아니라 `k_period`(및 `d_period`, 기본 3 고정)를 파라미터로 받는다. 위 프로토타입 실행은 5개 지표 전부에 `{"period": p}`를 넘겼기 때문에 STOCH_K/STOCH_D는 실제로는 `period` 값이 무시되고 항상 `k_period=14`로 계산됐다 — period 10/14/20이 이 두 지표에서는 아무 차이도 만들지 않았다(이 세션에서 관찰된 "STOCH_D period 10/14/20이 전부 동일 트레이드" dedup 사례의 실제 원인으로 추정됨). 이번 구현에서는 `scripts/grid_search.py`가 STOCH_K/STOCH_D에 한해 `{"k_period": p}`로 넘기도록 고쳐 period 그리드가 실제로 작동하게 한다. 그리드 표의 나머지 내용(period 값 `[10,14,20]`, threshold 값)은 변경 없음 — 파라미터 키만 지표별로 올바르게 매핑한다.

### 매수/매도 짝짓기

서로 다른 지표 전 교차(full cross product) — 같은 지표를 양쪽에 쓰는 조합도 포함해 모든 (매수지표, 매도지표) 쌍을 시도한다.

### 저장 방식

전체 조합을 계산하되 `engine.cache.save_result`/`run_backtest_cached`로 DB에 쓰는 건 상위 N개뿐 — 나머지는 메모리에서 계산 후 버린다(`backtest_runs` 테이블에 수천 건이 쌓이는 것을 피함).

### 중복 제거 (dedup)

거래 리스트를 `(진입 시각, 청산 시각)` 튜플의 튜플로 정규화해 dedup key로 쓴다. 같은 key를 가진 조합 그룹에서는 매수·매도 period가 가장 작은 조합을 대표로 남긴다(더 빠르게 반응하는, 즉 짧은 지표에 덜 종속되는 조합이 일반적으로 선호되므로). 동률이면 먼저 계산된 조합. 대표 조합들만 수익률 내림차순 정렬 후 상위 N개를 선택한다. 저장 시 `description`에 "동일 매매를 만든 조합 M개 중 대표" 문구를 덧붙여 추적 가능하게 한다. 거래 0건 조합(매수 조건이 구간 내내 한 번도 안 맞음)은 저장 후보에서 제외한다.

### 운용기간 입력

명령어에 시작일~종료일을 직접 지정(`2026-06-01~2026-07-31`). duration 형식(`최근 3개월` 등)은 1단계에서 채택하지 않는다.

### 명령어 표기

코인명/봉데이터 모두 한글로 입력받고(`이더리움`, `1시간`), Claude가 내부적으로 마켓코드(`KRW-ETH`)/timeframe 코드(`minutes60`)로 매핑한다.

### 곁다리 수정 — `summarizeGroup()` params 표기

`frontend/lib/condition-summary.ts`의 `summarizeGroup()`이 지표 `params`(period 등)를 렌더링하지 않아, "백테스트 결과" 목록에서 RSI(period=10)과 RSI(period=14)가 둘 다 `RSI<20`으로 보여 구분이 안 되는 문제를 grid search 결과를 보다가 발견했다. 이번 계획에 포함해 함께 고친다.

## 설계

### 1. `scripts/grid_search.py` (신규)

`scripts/run_eda_sweep.py`와 같은 컨벤션의 CLI 스크립트(수동 실행 스모크 테스트 대상). argparse 기반 named flags:

| 플래그 | 필수 | 설명 |
|---|---|---|
| `--market` | O | 마켓코드 (예: `KRW-ETH`) |
| `--timeframe` | O | timeframe 코드 (예: `minutes60`) |
| `--capital` | O | 운용자금(원) |
| `--start` | O | 시작일 (`YYYY-MM-DD`) |
| `--end` | O | 종료일 (`YYYY-MM-DD`) |
| `--top-n` | X (기본 20, 상한 50) | 저장할 상위 개수. 50 초과 입력 시 50으로 캡. |

내부 흐름:
1. `upbit_data_service.get_candles(market, timeframe, start, end)`로 캔들 1회 조회.
2. 위 "그리드 정의"대로 매수/매도 조건 리스트 생성, 전 교차. `params` 딕셔너리의 period 키는 지표별로 `create_fn`이 실제로 읽는 이름을 써야 한다 — STOCH_K/STOCH_D는 `{"k_period": p}`, 나머지(RSI/CCI/WILLIAMS_R)는 `{"period": p}` (`engine/indicators/momentum.py` 참고).
3. 각 조합을 `engine.runner.run_backtest(df, ConditionTreeStrategy, risk_config, {...})`로 계산 — `risk_config`는 `engine.sweep.DEFAULT_RISK_CONFIG`에 `initial_capital`만 덮어씀.
4. dedup(위 로직) → 수익률 내림차순 정렬 → 상위 N개.
5. 상위 N개만 `engine.cache.run_backtest_cached()`로 저장, 제목에 `[Grid]` 접두사.

출력은 `run_eda_sweep.py`처럼 사람이 읽는 진행률 로그(`매수조건 5/45 완료 (270/2565건, 45초 경과)`)를 print하다가, 계산 완료 후 마지막 줄에 기계 파싱용 JSON 요약을 한 줄 출력한다:

```
RESULT_JSON: {"total_combos": 2565, "elapsed_sec": 534, "saved": [{"rank": 1, "run_id": "...", "return_pct": 15.46, "title": "..."}]}
```

SKILL.md는 stdout 전체를 사람이 읽는 로그로 사용자에게 보여주되, 최종 구조화 보고는 이 JSON 한 줄만 파싱해서 만든다.

### 2. `.claude/skills/grid-search/SKILL.md` (신규, 한국어 작성)

트리거 패턴: `grid search [코인명],[운용자금],[봉데이터],[운용기간],[상위N개]` — 콤마 구분, 순서 고정. `상위N개`는 생략 가능(기본 20).

필드별 파싱 규칙:

| 필드 | 예시 | 변환 규칙 |
|---|---|---|
| 코인명 | `이더리움` | Claude 자체 지식으로 매핑(이더리움→ETH→`KRW-ETH`). 별도 룩업 테이블 없음. 모호하면 되물음. |
| 운용자금 | `1000만원` | Claude 산술로 원화 정수 변환(`10000000`). `1억`, `500만원` 등 한글 단위 지원. |
| 봉데이터 | `1시간` | 고정 매핑표: `1분→minutes1`, `3분→minutes3`, `5분→minutes5`, `15분→minutes15`, `30분→minutes30`, `1시간→minutes60`, `4시간→minutes240`, `1일→days`. (`upbit_data_service`가 지원하는 timeframe만 — 그 외 단위는 미지원 안내) |
| 운용기간 | `2026-06-01~2026-07-31` | `~` 구분 시작일/종료일 그대로 사용. duration 표현 미지원. |
| 상위N개 | `20` | 생략 시 20, 50 초과 시 50으로 캡하고 캡 사실을 사용자에게 안내. |

필수 필드(N 제외 4개) 중 하나라도 누락/모호하면 스크립트를 실행하지 않고 사용자에게 되물음 — 부분 입력으로 진행하지 않는다.

실행 전, 매핑 결과(마켓코드/timeframe/자본금/기간/N)를 표로 보여주고 사용자 확인을 받은 뒤에만 `scripts/grid_search.py`를 백그라운드로 실행한다. 확인 요청 시 예상 소요시간(~9분, 2,565개 조합 기준)도 함께 안내한다. 완료 후 `RESULT_JSON` 라인을 파싱해 상위 N개 결과를 요약 보고하고, "백테스트 결과" 페이지에서 확인 가능하다고 안내한다.

### 3. `frontend/lib/condition-summary.ts` — `summarizeGroup()` 수정

현재(`condition-summary.ts:19`) `` `${c.indicator}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}` ``로 `c.params`를 무시한다. `params`가 비어있지 않으면 지표명 뒤에 `(key=value, ...)`를 덧붙이도록 수정:

```ts
const paramStr = Object.entries(c.params).length
  ? `(${Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(', ')})`
  : '';
`${c.indicator}${paramStr}${OPERATOR_SYMBOLS[c.operator]}${c.threshold}`
```

결과 예: `RSI(period=10)<20 and STOCH_D(period=14)>70`. `params`가 없는 지표(STOP_LOSS_PCT 등)는 기존과 동일하게 표시. 이 함수는 재귀 순수 문자열 유틸이라 `summarizeGroup`을 쓰는 모든 화면(백테스트 결과 목록, 상세 페이지 등)에 자동 반영된다.

## 검증 절차 (구현 완료 후)

1. `PYTHONPATH=. python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20` 직접 실행. **주의**: STOCH_K/STOCH_D 파라미터 키 수정(위 "정정" 참고) 때문에 2026-08-01 세션 프로토타입의 정확한 수치(1위 STOCH_D<10/RSI>80, +15.46%)와 동일할 필요는 없다 — STOCH_D의 period 그리드가 이번엔 실제로 작동하므로 결과가 달라질 수 있다. 확인할 것은 (a) 총 조합 수 2,565개가 전부 계산되는지, (b) RESULT_JSON 라인이 정상 출력되는지, (c) 저장된 20건이 "백테스트 결과"에 `[Grid]` 접두사로 나타나는지, (d) 저장된 항목 중 동일 거래 시퀀스 중복이 없는지(dedup 정상 동작).
2. SKILL.md를 통해 자연어 명령(`grid search 이더리움,1000만원,1시간,2026-06-01~2026-07-31,20`)으로 1번과 동일한 스크립트 호출·결과가 재현되는지 확인.
3. 프론트 "백테스트 결과"에서 params 표기(`RSI(period=10)<20` 형태) 확인.

## 범위 밖

- 오실레이터 5개 + 매도전용 3개 외 지표(가격대/제로크로스/상관관계/외부데이터/거래량 계열) — 2단계 확장 대상, 정규화 지표(BB_PERCENT_B, MACD_PPO, ATR_PCT) 추가가 선행 조건.
- duration 형식 기간 입력(`최근 3개월` 등) — 명시적 날짜 범위만 지원.
- N에 대한 하한 없음(0 이하 등 비정상 입력은 스크립트 argparse 레벨에서 거부).
- `condition-summary.ts` 외 다른 화면의 지표 표기 방식 변경 — 이번 범위는 `summarizeGroup()` 한 함수.

## Self-Review 결과

- **스펙 커버리지**: 이전 세션 브레인스토밍에서 결정된 항목(지표 범위, 그리드 정의, dedup, 저장 방식, 곁다리 수정)과 이번 세션에서 추가로 확정한 디테일(CLI 인자, SKILL.md 파싱 규칙, 실행 전 확인, 출력 형식, dedup 대표 선택 기준, N 기본값/상한)이 모두 반영됨.
- **내부 정합성**: "매수/매도 짝짓기 = 전 교차"와 "오실레이터는 매수=`<`저값/매도=`>`고값 고정" 조합 규칙이 상충하지 않는지 확인 — 전 교차는 "어떤 지표를 매수/매도에 쓰는가"의 교차이고, 그리드 정의는 "그 지표를 매수/매도 어느 쪽에 쓰든 매수엔 저값+`<`, 매도엔 고값+`>`를 쓴다"는 지표별 조건 생성 규칙이라 서로 다른 층위 — 모순 없음.
- **실측 근거**: 2,565 조합 수치, 8.9분 소요, 상위 1위 결과가 실제 스크립트(`grid_search_oscillator5.py`) 실행 로그와 일치함을 확인.
- **대상 파일 목록**: `scripts/grid_search.py`(신규), `.claude/skills/grid-search/SKILL.md`(신규), `frontend/lib/condition-summary.ts`(수정).
