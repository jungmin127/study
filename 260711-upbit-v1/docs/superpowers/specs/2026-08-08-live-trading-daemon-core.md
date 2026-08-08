# 라이브 트레이딩 서브플랜⑤-4b — daemon.py(핵심 메인루프) Design Spec

## 배경 및 목표

`docs/superpowers/specs/2026-08-04-live-trading-foundation-design.md`(이하 "기반 스펙")의
1단계 로드맵 "5. 트레이딩 엔진 코어"의 마지막 조각이다. ⑤-1(DB+자금관리+리스크관리)~⑤-3
(주문실행)이 "신호평가 → 주문실행"의 단발 흐름을, ⑤-4a(`reconciler.py`)가 "내부 상태 vs
실제 거래소 상태 대조"를 각각 순수 함수/모듈로 완성했다. 이 스펙은 그것들을 실제로 24/7
계속 돌리는 상주 프로세스 `trading/daemon.py`를 설계한다 — 기반 스펙 결정3("FastAPI와
완전히 분리된 상주 데몬")이 지금까지 미뤄온 마지막 조각이다.

**⑤-4는 원래 daemon.py 전체를 한 서브플랜으로 부르던 이름이지만, 브레인스토밍 중 범위가
너무 커서 사용자와 함께 다시 쪼갰다:** 캔들 마감 기준으로 도는 "핵심 루프"(이 스펙,
⑤-4b)와, 기반 스펙이 명시적으로 "캔들 주기와 분리"라고 못박은 실시간 손절/익절(ticker
스트림 기반, ⑤-4c로 후속 분리)이다. reconciler(⑤-4a)를 daemon보다 먼저 뗀 것과 같은
이유 — 심각도가 높은 자동매매 코드를 한 번에 다루기엔 범위가 커서, 범위가 작을수록 리뷰
품질이 좋다(사용자 확정).

## 범위

**이 스펙에서 확정하는 것:**
- `trading/daemon.py`의 전체 구조 — 태스크셋 매니저, 전략별 루프, NTP 체크 루프
- 전략 폴링 주기 산정 방식(봉타임 비례)
- `evaluate_signals()`(동기+블로킹)를 asyncio 이벤트루프 안에서 안전하게 부르는 방법
- 캔들 조회(`upbit_data_service.get_candles()`)가 사실상 매 호출마다 네트워크를 탄다는
  사실 확인 + 그 경로에 자체 rate limit을 추가하는 방식
- 서킷브레이커 실제 트립 판정(`risk_manager.check_circuit_breaker()`)을 실제로 호출하는
  지점 확정(⑤-1부터 미뤄온 항목이자, 지금까지 아무 데도 안 불리던 걸 이번에 발견)
- `trading/db.py`/`trading/reconciler.py`/`upbit_data_service.py`에 필요한 작은 추가
- 전략별 예외 격리 정책

**이 스펙에서 다루지 않는 것(⑤-4c 및 후속 서브플랜에서):**
- 실시간 손절/익절(`STOP_LOSS_PCT`/`TAKE_PROFIT_PCT`) — ticker 스트림 구독,
  `upbit_ws.py` 연동 전부 ⑤-4c.
- 승인/제어 API("일시정지/재개/청산" 버튼), "라이브 전략 관리" 프론트엔드 — ⑥(UX).
  daemon은 DB의 `status` 컬럼만 읽으므로 ⑥이 나중에 그 컬럼에 쓰기만 하면 daemon 쪽은
  변경 없이 그대로 동작한다.
- 텔레그램 알림 — 2단계.
- PM2/Docker 배포, 실제 프로세스 매니저 연동 — 4단계(운영 하드닝). 이 스펙은
  `python -m trading.daemon`으로 직접 실행 가능한 것까지만 다룬다.

## 핵심 결정

### 결정 1 — daemon.py 범위를 ⑤-4b(핵심)/⑤-4c(실시간 손절익절)로 분리

위 "배경 및 목표" 참고. reconciler와 마찬가지로, ⑤-4c는 이 스펙이 만드는 태스크셋
매니저/전략별 루프 구조에 새 백그라운드 요소(ticker 구독 + 실시간 평가)를 얹기만 하면
되므로, 지금 미리 확장 여지를 만들어두되 구현은 미룬다.

### 결정 2 — 전략 집합은 주기적 재조회로 동적 관리한다(사용자 확정)

`live_strategies.status`가 `approved→running`으로 바뀌어도(승인 API는 ⑥ 몫이지만, DB
행 자체는 나중에 생길 수 있다) daemon 재시작 없이 자동으로 새 전략을 처리 대상에 포함시켜야
한다는 게 기반 스펙 결정3의 취지("대시보드가 재시작/재배포돼도 매매는 끊기지 않는다"와
대칭되는, "새 전략이 추가돼도 데몬은 안 끊긴다"). 태스크셋 매니저 루프가 20초마다
`db.list_active_strategies()`(신규, `status IN ('running','paused')`)를 다시 조회해
새 전략엔 `asyncio.create_task()`, 더 이상 대상이 아닌 전략엔 `task.cancel()`을 적용한다.

### 결정 3 — 전략별 루프 하나가 캔들처리+reconciler+지정가감시를 순차 실행한다

기반 스펙의 의사코드는 "전략별 [캔들 처리] 태스크 + 공통 백그라운드 루프 3개"로 그려져
있지만, 그대로 구현하면 같은 전략에 대해 [캔들 처리](`order_executor.enter()`/`exit()`
호출)와 [Reconciler](`check_manual_intervention()`)가 서로 다른 태스크에서 동시에 돌 수
있다 — ⑤-4a 최종리뷰가 daemon.py에 남긴 전제조건("reconciler를 enter()/exit()와 동시에
돌리지 말 것")을 무너뜨리는 구조다.

**결정:** 전략별로 asyncio 태스크를 하나만 둔다. 그 태스크 안에서 매 틱마다 순서대로
① `evaluate_signals()`+`handle_signal_result()`(새 봉일 때만) ② 매도체결 시
`check_circuit_breaker()` ③ 일정 시간(20초)마다 `check_manual_intervention()`+
`sync_pending_limit_orders()`를 **한 코루틴 안에서 순차 실행**한다. 동시성 충돌이
구조적으로 원천 차단된다(같은 코루틴 안에서 순서대로 실행되므로 서로 겹칠 수 없음)는 게
"공통 백그라운드 루프"로 따로 쪼개는 것보다 단순하면서 더 안전하다.

### 결정 4 — 전략별 폴링 주기는 봉타임에 비례한다: `clamp(duration // 12, 5, 60)`초

처음엔 5초 고정을 검토했으나(사용자 초기 선택), 아래 결정6에서 확인한 실제 API 부하
문제 때문에 재검토해 봉타임 비례로 바꿨다(사용자 확정, 재검토 후 결정).

`upbit_data_service.timeframe_duration(timeframe)`(⑤-2가 이미 공개화)로 봉 길이를 구해
`max(5, min(60, duration_seconds // 12))`초로 클램프한다. 1분봉=5초, 3분봉=15초,
5분봉=25초, 15분봉 이상은 전부 60초 상한에 걸려 60초로 통일된다. 15분봉 이상(실제 이
프로젝트에서 그리드서치로 많이 검증되는 1시간/1일봉 포함)의 호출량이 5초 고정 대비
최대 12배 줄어들면서도, 어떤 봉타임이든 감지 지연이 60초를 넘지 않는다.

### 결정 5 — `evaluate_signals()` 호출은 `asyncio.to_thread()`로 감싼다

`signal_engine.evaluate_signals()`는 동기 함수이고 내부에서 `get_candles()`(동기,
블로킹 `httpx.Client`+`time.sleep` 기반 재시도)를 호출한다. daemon.py는 전략마다
asyncio 태스크를 동시에 돌리는 구조인데, 이 동기 함수를 그냥 직접 호출하면 그 네트워크
호출이 끝날 때까지 **이벤트루프 전체**가 멈춰서 다른 모든 전략의 처리가 같이 지연된다
(전략별 동시성이 사실상 깨짐). `await asyncio.to_thread(signal_engine.evaluate_signals,
strategy_id)`로 감싸 별도 스레드에서 돌리면 이벤트루프가 막히지 않는다.

### 결정 6 — 공개 시세 API(캔들 조회)에 자체 rate limit을 추가한다

**발견한 문제:** `upbit_data_service.get_candles(market, timeframe, start, end)`는
`end`가 항상 "지금"이므로(daemon이 최신 상태를 계속 폴링하니까) `_compute_gaps()`가
`end > cache_end`(마지막 확정봉)를 거의 항상 참으로 판단해 **매 호출마다 실제로
`_fetch_range()`(네트워크 호출)를 탄다** — 캐시가 있어도 새 봉이 실제로 생겼는지와
무관하게 매번이다. 게다가 이 경로는 `trading/upbit_client.py`의 `TokenBucket`(인증
필요한 Exchange API 전용, `_DEFAULT_BUCKET`/`_ORDER_BUCKET`)의 보호를 전혀 받지 않는
완전히 별도 경로다(모듈도 별개, `upbit_data_service.py`는 `trading/` 밖에 있고 공개
엔드포인트를 동기 `httpx`로 직접 호출).

**실제 업비트 한도 확인**(docs.upbit.com/kr/reference/rate-limits, 2026-08-08 조회):
캔들 조회(초/분/일/주/월/연)는 전부 `candle` 그룹에 속하고 **IP당 초당 10회**가 한도다
(참고로 Exchange API `default` 그룹은 포켓당 30/s, `order` 그룹은 8/s — 기존
`trading/upbit_client.py`가 이미 정확히 반영). 전략 수가 늘어날수록 이 한도를 넘길
위험이 선형으로 커지는데, 결정4의 폴링 주기 절감만으로는 절대 상한이 보장되지 않는다
(여러 전략의 폴링 타이밍이 우연히 겹치면 순간적으로 초과 가능).

**결정:** `upbit_data_service.py`에 결정4·5와 마찬가지로 스레드(동기) 기반 토큰버킷
`_SyncTokenBucket`(rate_per_sec=10)을 새로 추가하고 `_fetch_page()` 진입 시
`.acquire()`한다. `trading/upbit_client.TokenBucket`(asyncio 기반)을 그대로 재사용하지
않고 별도 동기 클래스를 만드는 이유는, `get_candles()` 호출 체인 전체가 동기 함수라서
`await`를 쓸 수 없기 때문이다 — 결정5의 `asyncio.to_thread()` 덕분에 이 블로킹
`time.sleep()` 기반 대기가 스레드 안에서만 일어나고 이벤트루프는 막지 않는다(두 결정이
서로 맞물려 일관된 실행 모델을 이룸).

### 결정 7 — 서킷브레이커 실제 트립 판정을 daemon이 호출한다(⑤-1부터 미뤄온 항목)

**발견한 문제:** `risk_manager.check_circuit_breaker()`(일일손실률/연속손실이 한도를
넘었는지 실제로 판정해서 `tripped=1`+`live_strategies.status='paused'`로 바꾸는 함수)가
현재 코드베이스 어디에서도 호출되지 않는다. `order_executor.handle_signal_result()`는
`is_circuit_tripped_today()`(이미 트립됐는지 조회만)와 `record_trade_result()`(장부
갱신만)만 쓴다 — 지금 상태로는 전략이 손실 한도를 아무리 넘어도 서킷브레이커가 절대
안 걸린다. ⑤-1 최종리뷰가 "daily_loss_limit_pct 부호 미검증"만 ⑤-3/⑥로 넘겼을 뿐, 이
호출 자체가 통째로 빠져있다는 건 이번 세션에 처음 발견했다.

**결정:** `order_executor.py`(이미 리뷰·병합된 모듈)는 건드리지 않는다. 대신 daemon의
전략별 루프가 `handle_signal_result()` 반환값의 `sell_action == "exited"`를 확인해,
그 직후 `risk_manager.check_circuit_breaker(strategy_id, risk_config)`를 호출한다 —
"포지션이 청산될 때마다"라는 기반 스펙 문구와 정확히 일치하는 지점이고, 이미 병합된
`order_executor.py`를 다시 여는 것보다 안전하다(리뷰 범위를 daemon.py로 좁게 유지).

### 결정 8 — 전략 태스크의 예외는 로그만 남기고 격리한다(사용자 확정)

한 전략의 처리 중 예상 못 한 예외(네트워크 순간 장애, 파싱 오류 등)가 나도 데몬 전체나
다른 전략에 영향을 주지 않는다 — `try/except Exception`으로 루프 본문을 감싸고 로그만
남긴 뒤 다음 폴링에서 같은 전략을 다시 시도한다. 일시적 장애는 무시하고, 계속 실패하는
전략을 자동으로 멈추는 로직(연속 N회 실패 시 paused)은 이번 스펙에 넣지 않는다(사용자가
명시적으로 "일시적 장애는 무시" 쪽을 선택 — 과설계 방지).

### 결정 9 — `reconciler.py`의 `_sync_pending_limit_orders`를 public화한다

⑤-4a는 이 함수를 `hydrate_state()`(재시작 시 1회) 전용 내부 헬퍼로만 노출했다. 하지만
기반 스펙의 "[주문상태 감시]" 백그라운드 루프는 재시작 때뿐 아니라 **데몬이 계속 도는
동안에도** 지정가(`limit`, 타임아웃 없음) 모드로 방치된 주문을 주기적으로 확인해야
한다(⑤-3 설계 스펙 결정4 각주: "이 감시 루프의 역할은 재시작 후 복구 + limit 모드로
방치된 주문 감시로 좁혀진다"). daemon이 이 로직을 다시 구현(중복)하는 대신, 앞머리
언더스코어를 떼어 `reconciler.sync_pending_limit_orders(strategy, *, client=None) ->
list[dict]`로 공개 API화하고 daemon이 재사용한다. 동작은 완전히 동일(순수 rename), 새
테스트는 필요 없다(기존 테스트를 새 이름으로 갱신).

### 결정 10 — NTP 드리프트 체크는 로그 전용으로 간단히 넣는다(사용자 확정)

기반 스펙 "인프라 세부사항"의 안전장치 — JWT 서명 인증 실패를 예방하기 위해 로컬 시각과
업비트 서버 시각의 오차를 자체 점검한다. 업비트 응답의 `Date` HTTP 헤더와 로컬 UTC
시각을 비교하는 가벼운 함수를 `upbit_data_service.py`에 추가하고(인증 불필요한 공개
엔드포인트 재사용), 데몬 시작 시 + 10분마다 오차가 500ms를 넘으면 로그만 남긴다(알림/
자동조치는 2단계 텔레그램 이후).

## `trading/daemon.py`

```python
_TASK_REFRESH_INTERVAL_SEC = 20     # 태스크셋 재조회 주기(결정2)
_RECONCILE_INTERVAL_SEC = 20        # 전략별 루프 안에서 reconciler를 부르는 주기(결정3)
_NTP_CHECK_INTERVAL_SEC = 600       # 10분(결정10)
_NTP_DRIFT_THRESHOLD_SEC = 0.5      # 기반 스펙 예시 임계치(결정10)


def _poll_interval_sec(timeframe: str) -> float:
    """봉타임에 비례한 폴링 주기(결정4). 1분봉=5초, 3분봉=15초, 15분봉 이상은 60초 상한."""


async def _run_strategy_loop(strategy_id: str) -> None:
    """전략 하나를 담당하는 유일한 태스크(결정3). hydrate_state() 1회 → 무한루프(새 봉
    처리 → 매도체결 시 서킷브레이커 판정(결정7) → 20초마다 reconciler 2종 호출(결정9) →
    봉타임 비례 sleep(결정4)). status가 running/paused가 아니게 되면 스스로 종료한다
    (태스크셋 매니저의 다음 스캔을 기다리지 않고 즉시 반응). 예외는 로그만 남기고
    다음 틱에 재시도(결정8)."""


async def _task_set_manager_loop() -> None:
    """20초마다 db.list_active_strategies()를 다시 조회해 태스크 집합을 갱신한다(결정2).
    새 전략 → create_task(_run_strategy_loop), 더 이상 대상 아님 → task.cancel()."""


async def _run_ntp_check_loop() -> None:
    """시작 직후 1회 + 10분마다 upbit_data_service.get_server_time_offset_sec()을
    asyncio.to_thread로 호출, 임계치 초과 시 로그(결정10)."""


async def main() -> None:
    """logging 기본 설정 후 _task_set_manager_loop()와 _run_ntp_check_loop()를
    asyncio.gather로 동시 구동."""


if __name__ == "__main__":
    asyncio.run(main())
```

## 다른 모듈에 추가할 것

```python
# trading/db.py
def list_active_strategies() -> list[dict]:
    """live_strategies WHERE status IN ('running', 'paused') 전부 반환(결정2)."""


# upbit_data_service.py
class _SyncTokenBucket:
    """trading.upbit_client.TokenBucket과 동일한 토큰버킷 알고리즘을 동기(threading.Lock
    + time.sleep)로 구현한 버전(결정6) — get_candles() 호출 체인이 전부 동기라
    asyncio 기반 TokenBucket을 그대로 못 쓴다."""

_CANDLE_BUCKET = _SyncTokenBucket(rate_per_sec=10)  # 업비트 candle 그룹 실제 한도, IP 단위

def get_server_time_offset_sec() -> float:
    """공개 엔드포인트 응답의 Date 헤더 vs 로컬 UTC 시각 차이(초, 양수=로컬이 느림).
    인증 불필요(결정10)."""


# trading/reconciler.py — rename만(결정9)
async def sync_pending_limit_orders(strategy: dict, *, client=None) -> list[dict]:
    """기존 _sync_pending_limit_orders와 동일. 언더스코어만 제거해 공개 API화."""
```

## 에러 처리

- 전략별 루프 본문 예외 → 로그만, 다음 틱 재시도(결정8). `hydrate_state()` 자체가
  실패하면(태스크 시작 직후) 그 태스크는 재시도 없이 종료하고, 다음 태스크셋 매니저
  스캔에서 다시 시도된다(그 사이 이 전략은 처리되지 않지만 20초 이내로 재시도됨).
- `_task_set_manager_loop()` 자체의 예외(`db.list_active_strategies()` 실패 등)도
  로그만 남기고 다음 스캔에서 재시도 — 이 루프가 죽으면 새 전략을 영영 못 집는다.
- `_run_ntp_check_loop()` 예외도 로그만, 다음 체크에서 재시도.
- `_CANDLE_BUCKET`이 초당 10회를 넘기면 `.acquire()`가 자연스럽게 대기한다(예외 아님,
  `_fetch_page()`의 기존 429 재시도와 별개의 사전 예방 장치).
- 태스크셋 매니저가 `task.cancel()`한 태스크 안에서 `asyncio.CancelledError`가 자연스럽게
  전파되도록 두고 별도로 삼키지 않는다(취소가 실제로 먹혀야 함).

## 테스트 전략

- `_poll_interval_sec()`: 순수 함수 — 1분/3분/5분/15분/1시간/4시간/1일봉 경계값을
  골든테스트로 검증(결정4의 clamp 공식).
- `db.list_active_strategies()`: `tests/test_trading_db.py` 관례대로 running/paused/
  draft/approved/stopped 섞어 넣고 running+paused만 반환되는지 확인.
- `_SyncTokenBucket`: `trading/upbit_client.TokenBucket`의 기존 테스트 패턴을 동기
  버전으로 그대로 이식(주입 가능한 clock/sleep으로 실제 대기 없이 결정론적 검증).
- `get_server_time_offset_sec()`: `httpx.MockTransport`로 가짜 `Date` 헤더를 주고 오차
  계산 검증(`tests/test_upbit_client.py`의 기존 MockTransport 패턴 재사용).
- `_run_strategy_loop()`: `signal_engine.evaluate_signals`/`order_executor.
  handle_signal_result`/`reconciler.hydrate_state`/`check_manual_intervention`/
  `sync_pending_limit_orders`/`risk_manager.check_circuit_breaker`를 전부
  monkeypatch해 호출 순서·조건(새 봉 아닐 때 handle_signal_result 스킵, 매도체결 아닐
  때 check_circuit_breaker 스킵, 20초 안 지났을 때 reconciler 스킵)을 검증. 무한루프라
  N번 반복 후 `asyncio.CancelledError`를 주입해 종료시키는 테스트 헬퍼가 필요(또는
  status를 'stopped'로 바꿔 자연 종료 경로 사용).
- `_task_set_manager_loop()`: `_run_strategy_loop`를 monkeypatch(즉시 반환하는 더미
  코루틴)해 새 전략 감지 시 태스크 생성, 대상 이탈 시 `cancel()` 호출을 검증.
- 최종 통합 확인: `engine/` 미의존 확인(order_executor/reconciler와 동일한 AST 검사
  스크립트), 전체 회귀(`python -m pytest -q`).

## 자기 검토(스펙 완성도)

- 플레이스홀더 없음 — 10개 핵심 결정 각각 "왜"(발견한 실제 문제 포함)와 기각한 대안을
  남겼다.
- **이번 세션에 코드를 직접 읽고 발견한 문제 2건**(결정6의 캔들조회 무방비 상태, 결정7의
  서킷브레이커 미호출)을 스펙에 명시하고 근거(파일:함수, 실제 업비트 문서 조회 결과)를
  남겼다 — 나중에 "왜 이 결정을 했는지" 재확인할 수 있게.
- 결정3(전략별 루프 하나로 통합) ↔ 결정5(to_thread) ↔ 결정6(동기 토큰버킷)이 하나의
  일관된 실행 모델("무거운 I/O는 스레드로, 그 안에서 동기적으로 순서대로")로 수렴함을
  확인했다.
- 스코프 경계: 실시간 손절/익절·ticker 구독은 ⑤-4c로, 승인/제어 UI는 ⑥으로 명확히
  넘겼다 — daemon.py는 `status` 컬럼을 읽기만 하므로 ⑥이 나중에 그 컬럼에 쓰기만
  하면 daemon 쪽 변경이 필요 없다는 인터페이스 계약을 명시했다.
- 기존 모듈과의 인터페이스 일치 확인: `signal_engine.evaluate_signals`/
  `order_executor.handle_signal_result`/`reconciler.hydrate_state`/
  `check_manual_intervention`/`risk_manager.check_circuit_breaker`/
  `upbit_data_service.timeframe_duration` 전부 이미 구현된 시그니처를 그대로
  재사용하며 새로 바꾸는 게 없다(`reconciler.sync_pending_limit_orders`의 rename만
  예외, 동작 불변).
