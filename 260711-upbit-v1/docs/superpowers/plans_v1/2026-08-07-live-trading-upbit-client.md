# 라이브 트레이딩 서브플랜④ — Upbit 연동 (trading/upbit_client.py + trading/upbit_ws.py) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recomm된) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **워크트리를 만들지 말고 main 브랜치에서 직접 작업한다** (사용자 지시, [[upbit-v1-worktree-workflow-changed]]).

**Goal:** 업비트 Private REST API(JWT 인증, 주문/계좌 관련)를 호출하는 `trading/upbit_client.py`와
공개 WebSocket(ticker, 실시간 현재가)을 구독하는 `trading/upbit_ws.py`를 만든다. 이 두 모듈이
서브플랜⑤(트레이딩 엔진 코어)가 주문 실행·잔고 조회·실시간 손절/익절 감지에 쓸 기반이다.

**Architecture:** `docs/superpowers/specs_v1/2026-08-04-live-trading-foundation-design.md`의 1단계
로드맵 중 서브플랜③(`docs/superpowers/plans_v1/2026-08-07-live-trading-indicators-b-group.md`, main에
커밋됨) 다음 순서인 서브플랜④다.

**봉(캔들) 마감 감지 방식 확정(이 플랜 작성 세션에서 사용자 결정):** 업비트 공개 WebSocket에는
캔들 채널이 없다(공개 채널은 `ticker`/`trade`/`orderbook`뿐 — 업비트 공식 문서
`https://docs.upbit.com/kr/reference/websocket-guide` 확인). 신호평가용 캔들 감지는 **WS 체결
틱을 직접 집계하지 않고, 기존 `upbit_data_service.get_candles()`를 데몬(서브플랜⑤)이 짧은 주기로
REST 폴링**해서 새 봉 마감을 감지하는 방식으로 확정한다 — WS 틱 집계는 봉 경계 처리·재연결 시
누락 체결 보충·상태 유지 로직이 훨씬 복잡해지고 버그 여지가 크다는 게 이유. `upbit_data_service.py`는
이미 백테스트에서 검증된 캔들 조회·캐싱·gap-fill 로직을 그대로 갖고 있으므로(스펙 결정 1과 같은
"기존 검증된 코드 재사용" 원칙), **이 서브플랜은 캔들 조회 코드를 새로 만들지 않는다** — 그래서
`trading/upbit_ws.py`의 책임이 실시간 ticker(현재가) 구독 하나로 좁혀진다. 실시간 ticker는 스펙의
"리스크 청산(손절/익절)은 캔들 마감을 기다리지 않는다 — ticker 스트림마다 별도로 평가한다"
요구사항 전용이다(이 평가 로직 자체는 서브플랜⑤ `signal_engine.py`의 몫, 이 플랜은 스트림
제공까지만).

**Rate Limit 그룹 재정의(스펙 원문보다 정확하게, 이 플랜 작성 세션에서 업비트 공식 문서
`https://docs.upbit.com/kr/reference/rate-limits.md` 확인):** 스펙은 "엔드포인트 그룹별(주문/계좌/
시세) 토큰버킷"이라고 개념적으로 적었지만, 실제 업비트 Exchange API(사설/인증 필요 엔드포인트)는
그룹이 2개뿐이다 — `default`(계좌조회/주문조회/주문취소 등 대부분, 초당 30회)와 `order`(주문
생성, 초당 8회). "시세" 그룹(Quotation API, 초당 10회, IP 단위)은 공개 캔들 조회용인데,
`upbit_data_service.py`가 이미 자체 재시도/백오프로 그 엔드포인트를 다루고 있어(이 플랜이 위에서
캔들 코드를 새로 안 만들기로 확정했으므로) **이 클라이언트가 신경 쓸 필요가 없다**. 그래서
`trading/upbit_client.py`는 `default`/`order` 2개 토큰버킷만 구현한다.

**Tech Stack:** Python, `httpx`(이미 사용 중, `AsyncClient`), `PyJWT`(신규 — JWT 서명),
`websockets`(신규 — 공개 WS 구독), `python-dotenv`(신규 — 로컬 개발용 `.env` 로딩),
`asyncio`, `pytest`, `pytest-asyncio` 스타일의 `async def test_*` (아래 참고).

## Global Constraints

- **API 키는 환경변수로만 보관**(`UPBIT_ACCESS_KEY`/`UPBIT_SECRET_KEY`), 코드·저장소에 절대
  커밋하지 않는다(스펙 "추가로 고려해야 할 항목 - 보안" 절). 로컬 개발 편의를 위해
  `python-dotenv`로 `.env` 파일을 로딩하되(`.gitignore`에 추가, `.env.example`만 커밋), 배포
  환경(PM2/Docker)에서는 OS 환경변수를 그대로 쓴다(dotenv는 이미 설정된 환경변수를 덮어쓰지
  않는다 — `load_dotenv()` 기본 동작).
- 이 서브플랜은 `engine/`을 전혀 import하지 않는다(업비트 클라이언트는 지표/조건평가와
  무관하므로 스펙 결정 1의 제약 대상 자체가 아니지만, 명시적으로 언급 — `trading/upbit_client.py`·
  `trading/upbit_ws.py` 둘 다 `engine`을 import하지 않는다).
- REST 호출은 전부 `async def` 함수(데몬이 asyncio 이벤트루프에서 여러 전략을 동시에 관리하기
  때문, 스펙 결정 6). 이 저장소의 기존 데이터서비스(`external_data_service.py`,
  `binance_data_service.py`, `upbit_data_service.py`)는 전부 동기(`httpx.Client`)인데, 이 파일만
  비동기인 이유는 데몬 메인루프가 asyncio 기반으로 설계됐기 때문(스펙 "메인 루프"·"프로세스
  토폴로지" 절) — 이 서브플랜에서 새로 도입하는 비동기 패턴을 다른 기존 동기 서비스에
  소급 적용하지 않는다.
- `trading/upbit_client.py`와 `trading/upbit_ws.py`는 각각 **하나의 파일로 유지**한다(스펙 모듈
  구조 절, 서브플랜②·③과 동일 관례).
- Rate limit 토큰버킷은 모듈 전역 싱글턴(`_DEFAULT_BUCKET`/`_ORDER_BUCKET`)이다 — 함수 호출마다
  새로 만들지 않는다(스펙 "REST 호출은 전부 하나의 async 큐를 통과" 요구사항의 핵심 — 데몬 안의
  모든 전략 태스크가 같은 두 버킷을 공유해야 프로세스 전체 요청량이 실제로 제한된다).
- 429(rate limit 초과) 응답은 토큰버킷이 선제적으로 막아주는 게 정상 경로지만, 방어적으로 각
  요청 함수에도 재시도 백오프를 둔다(기존 `external_data_service.py`/`binance_data_service.py`와
  동일한 `RETRY_ATTEMPTS`/`RATE_LIMIT_BACKOFF_SECONDS` 패턴 재사용).
- 커밋은 태스크 단위로 작게, 테스트가 통과한 뒤에만 한다.

---

## 확정된 업비트 REST 엔드포인트(이 플랜 작성 세션에서 공식 문서로 직접 확인, `https://api.upbit.com` 기준)

| 기능 | 메소드 | 경로 | 출처 |
|---|---|---|---|
| 계좌(잔고) 조회 | GET | `/v1/accounts` | `docs.upbit.com/kr/reference/get-balance` |
| 주문 가능 정보 | GET | `/v1/orders/chance` | `docs.upbit.com/kr/reference/available-order-information` |
| 주문 생성 | POST | `/v1/orders` | `docs.upbit.com/kr/reference/new-order` |
| 개별 주문 조회 | GET | `/v1/order` (단수) | `docs.upbit.com/kr/reference/get-order` |
| 주문 취소 | DELETE | `/v1/order` (단수) | `docs.upbit.com/kr/reference/cancel-order` |
| 체결대기 주문 목록 | GET | `/v1/orders/open` | `docs.upbit.com/kr/reference/list-open-orders` |
| 종료 주문 목록 | GET | `/v1/orders/closed` | `docs.upbit.com/kr/reference/list-closed-orders` |
| 공개 WebSocket | — | `wss://api.upbit.com/websocket/v1` | `docs.upbit.com/kr/reference/websocket-guide` |

주문 생성(`POST /v1/orders`)의 파라미터는 `ord_type`에 따라 다르다: `market`(필수) +
`side`(필수, `bid`|`ask`) + `ord_type`(필수, `limit`|`price`|`market`|`best`) — `limit`은
`volume`+`price` 둘 다 필요, `price`(시장가 매수)는 `price`만, `market`(시장가 매도)는 `volume`만
필요(스펙의 시장가/지정가/지정가+타임아웃 3모드 중 시장가·지정가에 대응 — `best`는 이 프로젝트
스펙에 없는 모드라 이 플랜에서 다루지 않는다).

업비트 JWT 인증은 **GET/DELETE든 POST든 관계없이** 파라미터를 `urlencode()` 후 `unquote()`한
문자열의 SHA512 해시(`query_hash`)를 JWT payload에 넣는 방식이 동일하다 — HTTP 요청 자체는
GET/DELETE면 쿼리스트링으로, POST면 JSON 바디로 같은 파라미터를 보낸다(업비트 공식 예제 코드의
표준 패턴).

---

## File Structure

- **Create:** `trading/upbit_client.py` — JWT 인증 헬퍼, `TokenBucket` 레이트리미터, `_request()`
  공통 코어, 7개 엔드포인트 함수(`get_accounts`/`get_order_chance`/`create_order`/`get_order`/
  `cancel_order`/`list_open_orders`/`list_closed_orders`).
- **Create:** `trading/upbit_ws.py` — `stream_ticker()` async generator(재연결 지수 백오프 포함).
- **Modify:** `requirements.txt` — `PyJWT`/`websockets`/`python-dotenv` 추가.
- **Modify:** `.gitignore` — `.env` 추가.
- **Create:** `.env.example` — `UPBIT_ACCESS_KEY`/`UPBIT_SECRET_KEY` 플레이스홀더(실제 키 아님,
  커밋 대상).
- **Create:** `tests/test_upbit_client.py` — JWT/토큰버킷/7개 엔드포인트 함수 테스트(`httpx.MockTransport`
  기반, 실제 네트워크 호출 없음).
- **Create:** `tests/test_upbit_ws.py` — `stream_ticker()` 테스트(로컬 `websockets.serve()` 테스트
  서버 기반, 재연결 검증 포함).

---

### Task 1: 의존성 + 환경변수 로딩 + JWT 인증 헬퍼

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `trading/upbit_client.py`
- Create: `tests/test_upbit_client.py`

**Interfaces:**
- Consumes: 없음(이 서브플랜의 첫 태스크).
- Produces: `trading.upbit_client.UpbitCredentialsError`(예외 클래스),
  `trading.upbit_client._build_jwt_headers(query: dict | None = None) -> dict[str, str]`(이후
  Task 3의 `_request()`가 재사용).

- [x] **Step 1: 의존성 추가**

`requirements.txt`에 다음 3줄 추가(파일 끝):
```
PyJWT>=2.8
websockets>=12.0
python-dotenv>=1.0
```

Run: `pip install -r requirements.txt`
Expected: `PyJWT`/`websockets`/`python-dotenv` 설치 확인(이미 `websockets`/`python-dotenv`는
이 환경에 설치돼 있을 수 있음 — `PyJWT`만 새로 설치될 가능성이 높다).

- [x] **Step 2: `.gitignore`/`.env.example` 추가**

`.gitignore` 파일 끝에 추가:
```
.env
```

`.env.example`(신규 파일):
```
UPBIT_ACCESS_KEY=your_upbit_access_key_here
UPBIT_SECRET_KEY=your_upbit_secret_key_here
```

- [x] **Step 3: 실패하는 테스트 작성**

`tests/test_upbit_client.py`(신규 파일):
```python
import hashlib
from urllib.parse import unquote, urlencode

import jwt
import pytest

import trading.upbit_client as upbit_client
from trading.upbit_client import UpbitCredentialsError, _build_jwt_headers


def test_build_jwt_headers_without_query(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    headers = _build_jwt_headers()
    assert headers["Authorization"].startswith("Bearer ")
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
    assert payload["access_key"] == "test-access"
    assert "nonce" in payload
    assert "query_hash" not in payload


def test_build_jwt_headers_with_query_includes_correct_hash(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    query = {"market": "KRW-BTC", "side": "bid"}
    headers = _build_jwt_headers(query)
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
    expected_query_string = unquote(urlencode(query, doseq=True))
    expected_hash = hashlib.sha512(expected_query_string.encode()).hexdigest()
    assert payload["query_hash"] == expected_hash
    assert payload["query_hash_alg"] == "SHA512"


def test_build_jwt_headers_raises_when_credentials_missing(monkeypatch):
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    with pytest.raises(UpbitCredentialsError):
        _build_jwt_headers()


def test_build_jwt_headers_nonce_is_unique_per_call(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    token1 = _build_jwt_headers()["Authorization"]
    token2 = _build_jwt_headers()["Authorization"]
    assert token1 != token2
```

- [x] **Step 4: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.upbit_client'`

- [x] **Step 5: `trading/upbit_client.py` 구현**

```python
"""
trading/upbit_client.py

업비트 Private REST API(주문/계좌 관련, JWT 인증 필요) 비동기 클라이언트. 공개 캔들 조회는
다루지 않는다 — 그건 이미 upbit_data_service.py가 검증된 캐싱/gap-fill 로직으로 처리하며,
이 서브플랜이 새로 만들지 않기로 확정했다(이 플랜 문서의 "봉 마감 감지 방식 확정" 절 참고).

인증: 업비트 JWT 방식 — access_key + nonce(UUID) + (파라미터가 있으면) query_hash(SHA512)를
페이로드에 담아 secret_key로 서명한다. GET/DELETE/POST 관계없이 query_hash 계산 방식은 동일
(urlencode 후 unquote한 문자열의 SHA512).

Rate Limit: 업비트 Exchange API의 실제 그룹 2개(default 30req/s, order 8req/s)에 맞춘 토큰버킷
2개를 모듈 전역으로 유지한다 — 데몬 안의 모든 전략 태스크가 같은 버킷을 공유해야 프로세스
전체 요청량이 실제로 제한된다.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from urllib.parse import unquote, urlencode

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

UPBIT_BASE_URL = "https://api.upbit.com/v1"

RETRY_ATTEMPTS = 3
RATE_LIMIT_BACKOFF_SECONDS = 1.0


class UpbitCredentialsError(Exception):
    """UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 환경변수가 설정되지 않았을 때."""


def _build_jwt_headers(query: dict | None = None) -> dict[str, str]:
    access_key = os.environ.get("UPBIT_ACCESS_KEY")
    secret_key = os.environ.get("UPBIT_SECRET_KEY")
    if not access_key or not secret_key:
        raise UpbitCredentialsError(
            "UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 환경변수가 설정되지 않았습니다"
        )

    payload: dict[str, str] = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    if query:
        query_string = unquote(urlencode(query, doseq=True))
        payload["query_hash"] = hashlib.sha512(query_string.encode()).hexdigest()
        payload["query_hash_alg"] = "SHA512"

    token = jwt.encode(payload, secret_key)
    return {"Authorization": f"Bearer {token}"}
```

- [x] **Step 6: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 4개 테스트 전부 PASS

- [x] **Step 7: 커밋**

```bash
git add requirements.txt .gitignore .env.example trading/upbit_client.py tests/test_upbit_client.py
git commit -m "feat: upbit_client에 JWT 인증 헬퍼 추가 + 의존성/환경변수 설정"
```

---

### Task 2: `TokenBucket` 레이트리미터

**Files:**
- Modify: `trading/upbit_client.py`
- Modify: `tests/test_upbit_client.py`

**Interfaces:**
- Consumes: 없음(독립 유틸리티).
- Produces: `trading.upbit_client.TokenBucket(rate_per_sec: float, capacity: float | None = None,
  *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] =
  asyncio.sleep)` — `.acquire() -> None`(async) 메소드. Task 3이 이 클래스로 `_DEFAULT_BUCKET`/
  `_ORDER_BUCKET` 싱글턴 2개를 만든다.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_client.py` 파일 끝에 추가:
```python
from trading.upbit_client import TokenBucket


def _fake_clock_and_sleep():
    fake_time = [0.0]

    def clock() -> float:
        return fake_time[0]

    async def sleep(seconds: float) -> None:
        fake_time[0] += seconds

    return fake_time, clock, sleep


async def test_token_bucket_allows_burst_up_to_capacity():
    fake_time, clock, sleep = _fake_clock_and_sleep()
    bucket = TokenBucket(rate_per_sec=2, capacity=2, clock=clock, sleep=sleep)
    await bucket.acquire()
    await bucket.acquire()
    assert fake_time[0] == 0.0  # 용량 안에서는 대기 없음


async def test_token_bucket_waits_when_capacity_exhausted():
    fake_time, clock, sleep = _fake_clock_and_sleep()
    bucket = TokenBucket(rate_per_sec=2, capacity=2, clock=clock, sleep=sleep)
    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()  # 3번째는 1/rate = 0.5초 대기해야 함
    assert fake_time[0] == pytest.approx(0.5)


async def test_token_bucket_refills_over_time():
    fake_time, clock, sleep = _fake_clock_and_sleep()
    bucket = TokenBucket(rate_per_sec=1, capacity=1, clock=clock, sleep=sleep)
    await bucket.acquire()
    fake_time[0] += 2.0  # 시간이 흘러 토큰이 다시 채워짐
    await bucket.acquire()
    assert fake_time[0] == 2.0  # 추가 대기 없이 바로 소비


async def test_token_bucket_default_capacity_equals_rate():
    bucket = TokenBucket(rate_per_sec=5)
    assert bucket._capacity == 5
```

이 파일 맨 위에 `import asyncio`가 없다면 추가하지 않아도 된다(테스트 함수들이 `async def`이므로
pytest-asyncio 또는 이 저장소의 asyncio 테스트 설정에 의존 — Step 3에서 실행 방법 확인).

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v -k token_bucket`
Expected: FAIL — `ImportError: cannot import name 'TokenBucket'`

(참고: 만약 `async def` 테스트 함수가 pytest에 의해 "코루틴이 await되지 않음" 경고와 함께
스킵되면, 이 저장소에 `pytest-asyncio`가 없다는 뜻이다 — `requirements.txt`에
`pytest-asyncio>=0.23`을 추가하고 저장소 루트 `pytest.ini`에 `asyncio_mode = auto`를 추가한
뒤 다시 실행한다. 기존 `pytest.ini` 내용을 확인하고 섹션이 이미 있으면 값만 맞춘다.)

- [x] **Step 3: `trading/upbit_client.py`에 `TokenBucket` 추가**

`class UpbitCredentialsError(Exception):` 블록 바로 다음, `def _build_jwt_headers` 앞에 추가:
```python
class TokenBucket:
    """rate_per_sec 속도로 토큰을 채우는 비동기 토큰버킷. acquire()는 토큰이 있으면 즉시
    반환하고, 없으면 다음 토큰이 채워질 때까지 대기한다. clock/sleep을 주입할 수 있어
    테스트에서 실제 시간 흐름 없이 결정론적으로 검증 가능하다."""

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        *,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ) -> None:
        self._rate = rate_per_sec
        self._capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_seconds = (1 - self._tokens) / self._rate
                await self._sleep(wait_seconds)


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 8개 테스트 전부 PASS(Task 1의 4개 + 이 태스크의 4개)

- [x] **Step 5: 커밋**

```bash
git add trading/upbit_client.py tests/test_upbit_client.py requirements.txt pytest.ini
git commit -m "feat: upbit_client에 TokenBucket 레이트리미터 추가"
```

(`requirements.txt`/`pytest.ini`는 Step 2에서 `pytest-asyncio`를 추가했을 때만 diff에 포함된다.)

---

### Task 3: `_request()` 코어 + `get_accounts()`

**Files:**
- Modify: `trading/upbit_client.py`
- Modify: `tests/test_upbit_client.py`

**Interfaces:**
- Consumes: Task 1의 `_build_jwt_headers`, Task 2의 `TokenBucket`.
- Produces: `trading.upbit_client._DEFAULT_BUCKET`/`_ORDER_BUCKET`(모듈 전역 `TokenBucket`
  싱글턴), `trading.upbit_client._request(method: str, path: str, *, params: dict | None = None,
  bucket: TokenBucket, client: httpx.AsyncClient | None = None) -> dict | list`(async, 이후
  모든 엔드포인트 함수가 재사용), `trading.upbit_client.get_accounts(*, client:
  httpx.AsyncClient | None = None) -> list[dict]`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_client.py` 파일 끝에 추가:
```python
import httpx

from trading.upbit_client import get_accounts


def _mock_async_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_get_accounts_returns_parsed_json(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/accounts"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json=[{"currency": "KRW", "balance": "100000.0"}])

    async with _mock_async_client(handler) as client:
        result = await get_accounts(client=client)

    assert result == [{"currency": "KRW", "balance": "100000.0"}]


async def test_get_accounts_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    monkeypatch.setattr(upbit_client, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=[])

    async with _mock_async_client(handler) as client:
        result = await get_accounts(client=client)

    assert calls["count"] == 2
    assert result == []


async def test_get_accounts_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    monkeypatch.setattr(upbit_client, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async with _mock_async_client(handler) as client:
        with pytest.raises(RuntimeError):
            await get_accounts(client=client)


async def test_get_accounts_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "internal"}})

    async with _mock_async_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await get_accounts(client=client)


async def test_get_accounts_goes_through_default_bucket(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    calls = {"count": 0}

    class _SpyBucket:
        async def acquire(self) -> None:
            calls["count"] += 1

    monkeypatch.setattr(upbit_client, "_DEFAULT_BUCKET", _SpyBucket())

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _mock_async_client(handler) as client:
        await get_accounts(client=client)

    assert calls["count"] == 1
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v -k get_accounts`
Expected: FAIL — `ImportError: cannot import name 'get_accounts'`

- [x] **Step 3: `trading/upbit_client.py`에 구현 추가**

`_build_jwt_headers` 함수 바로 뒤에 추가:
```python
_DEFAULT_BUCKET = TokenBucket(rate_per_sec=30)
_ORDER_BUCKET = TokenBucket(rate_per_sec=8)


async def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    bucket: TokenBucket,
    client: httpx.AsyncClient | None = None,
) -> dict | list:
    """업비트 Private REST 호출 공통 코어. query_hash는 GET/POST/DELETE 관계없이 params로
    계산하고, 실제 전송은 GET/DELETE면 쿼리스트링으로 POST면 JSON 바디로 한다(업비트 인증
    방식의 표준 패턴). bucket.acquire()로 선제적으로 스로틀링한 뒤에도 429가 오면(클럭 오차,
    다른 프로세스의 동시 사용 등) 방어적으로 재시도한다."""
    headers = _build_jwt_headers(params)
    close_client = client is None
    client = client or httpx.AsyncClient(timeout=10)
    url = f"{UPBIT_BASE_URL}{path}"

    try:
        await bucket.acquire()
        for attempt in range(RETRY_ATTEMPTS):
            if method == "POST":
                resp = await client.request(method, url, json=params, headers=headers)
            else:
                resp = await client.request(method, url, params=params, headers=headers)
            if resp.status_code == 429:
                await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"업비트 API 호출 실패 (429 재시도 소진): {method} {path}")
    finally:
        if close_client:
            await client.aclose()


async def get_accounts(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    return await _request("GET", "/accounts", bucket=_DEFAULT_BUCKET, client=client)


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 13개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/upbit_client.py tests/test_upbit_client.py
git commit -m "feat: upbit_client에 _request 코어 + get_accounts 추가"
```

---

### Task 4: `get_order_chance()` + `create_order()`

**Files:**
- Modify: `trading/upbit_client.py`
- Modify: `tests/test_upbit_client.py`

**Interfaces:**
- Consumes: Task 3의 `_request`, `_DEFAULT_BUCKET`, `_ORDER_BUCKET`.
- Produces: `get_order_chance(market: str, *, client=None) -> dict`, `create_order(market: str,
  side: str, ord_type: str, *, volume: str | None = None, price: str | None = None,
  time_in_force: str | None = None, identifier: str | None = None, client=None) -> dict`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_client.py` 파일 끝에 추가:
```python
from trading.upbit_client import create_order, get_order_chance


async def test_get_order_chance_sends_market_query_param(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/orders/chance"
        assert dict(request.url.params) == {"market": "KRW-BTC"}
        return httpx.Response(200, json={"market": {"id": "KRW-BTC"}})

    async with _mock_async_client(handler) as client:
        result = await get_order_chance("KRW-BTC", client=client)

    assert result == {"market": {"id": "KRW-BTC"}}


async def test_create_order_limit_sends_volume_and_price_as_json_body(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/orders"
        import json as json_module
        body = json_module.loads(request.content)
        assert body == {
            "market": "KRW-BTC", "side": "bid", "ord_type": "limit",
            "volume": "0.01", "price": "50000000",
        }
        return httpx.Response(200, json={"uuid": "abc-123", "state": "wait"})

    async with _mock_async_client(handler) as client:
        result = await create_order(
            "KRW-BTC", "bid", "limit", volume="0.01", price="50000000", client=client,
        )

    assert result == {"uuid": "abc-123", "state": "wait"}


async def test_create_order_market_sell_omits_price(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        import json as json_module
        body = json_module.loads(request.content)
        assert body == {"market": "KRW-BTC", "side": "ask", "ord_type": "market", "volume": "0.01"}
        return httpx.Response(200, json={"uuid": "def-456", "state": "wait"})

    async with _mock_async_client(handler) as client:
        await create_order("KRW-BTC", "ask", "market", volume="0.01", client=client)


async def test_create_order_goes_through_order_bucket_not_default(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")
    calls = {"order": 0, "default": 0}

    class _SpyBucket:
        def __init__(self, key: str) -> None:
            self._key = key

        async def acquire(self) -> None:
            calls[self._key] += 1

    monkeypatch.setattr(upbit_client, "_ORDER_BUCKET", _SpyBucket("order"))
    monkeypatch.setattr(upbit_client, "_DEFAULT_BUCKET", _SpyBucket("default"))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"uuid": "x"})

    async with _mock_async_client(handler) as client:
        await create_order("KRW-BTC", "bid", "price", price="10000", client=client)

    assert calls == {"order": 1, "default": 0}
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v -k "order_chance or create_order"`
Expected: FAIL — `ImportError: cannot import name 'get_order_chance'`

- [x] **Step 3: `trading/upbit_client.py`에 구현 추가**

`async def get_accounts(...)` 함수 바로 뒤에 추가:
```python
async def get_order_chance(market: str, *, client: httpx.AsyncClient | None = None) -> dict:
    return await _request(
        "GET", "/orders/chance", params={"market": market}, bucket=_DEFAULT_BUCKET, client=client
    )


async def create_order(
    market: str,
    side: str,
    ord_type: str,
    *,
    volume: str | None = None,
    price: str | None = None,
    time_in_force: str | None = None,
    identifier: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    params: dict[str, str] = {"market": market, "side": side, "ord_type": ord_type}
    if volume is not None:
        params["volume"] = volume
    if price is not None:
        params["price"] = price
    if time_in_force is not None:
        params["time_in_force"] = time_in_force
    if identifier is not None:
        params["identifier"] = identifier
    return await _request("POST", "/orders", params=params, bucket=_ORDER_BUCKET, client=client)


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 17개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/upbit_client.py tests/test_upbit_client.py
git commit -m "feat: upbit_client에 get_order_chance/create_order 추가"
```

---

### Task 5: `cancel_order()` + `get_order()`

**Files:**
- Modify: `trading/upbit_client.py`
- Modify: `tests/test_upbit_client.py`

**Interfaces:**
- Consumes: Task 3의 `_request`, `_DEFAULT_BUCKET`.
- Produces: `cancel_order(*, uuid: str | None = None, identifier: str | None = None, client=None)
  -> dict`, `get_order(*, uuid: str | None = None, identifier: str | None = None, client=None) ->
  dict`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_client.py` 파일 끝에 추가:
```python
from trading.upbit_client import cancel_order, get_order


async def test_cancel_order_by_uuid_sends_delete_with_uuid_param(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/v1/order"
        assert dict(request.url.params) == {"uuid": "abc-123"}
        return httpx.Response(200, json={"uuid": "abc-123", "state": "wait"})

    async with _mock_async_client(handler) as client:
        result = await cancel_order(uuid="abc-123", client=client)

    assert result == {"uuid": "abc-123", "state": "wait"}


async def test_cancel_order_by_identifier(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"identifier": "my-order-1"}
        return httpx.Response(200, json={"identifier": "my-order-1"})

    async with _mock_async_client(handler) as client:
        await cancel_order(identifier="my-order-1", client=client)


async def test_cancel_order_raises_when_neither_uuid_nor_identifier():
    with pytest.raises(ValueError):
        await cancel_order()


async def test_get_order_sends_get_with_uuid_param(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/order"
        assert dict(request.url.params) == {"uuid": "abc-123"}
        return httpx.Response(200, json={"uuid": "abc-123", "state": "done"})

    async with _mock_async_client(handler) as client:
        result = await get_order(uuid="abc-123", client=client)

    assert result == {"uuid": "abc-123", "state": "done"}


async def test_get_order_raises_when_neither_uuid_nor_identifier():
    with pytest.raises(ValueError):
        await get_order()
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v -k "cancel_order or get_order_"`
Expected: FAIL — `ImportError: cannot import name 'cancel_order'`

- [x] **Step 3: `trading/upbit_client.py`에 구현 추가**

`async def create_order(...)` 함수 바로 뒤에 추가:
```python
def _uuid_or_identifier_params(uuid: str | None, identifier: str | None) -> dict[str, str]:
    if not uuid and not identifier:
        raise ValueError("uuid 또는 identifier 중 하나는 필요합니다")
    params: dict[str, str] = {}
    if uuid:
        params["uuid"] = uuid
    if identifier:
        params["identifier"] = identifier
    return params


async def cancel_order(
    *, uuid: str | None = None, identifier: str | None = None, client: httpx.AsyncClient | None = None
) -> dict:
    params = _uuid_or_identifier_params(uuid, identifier)
    return await _request("DELETE", "/order", params=params, bucket=_DEFAULT_BUCKET, client=client)


async def get_order(
    *, uuid: str | None = None, identifier: str | None = None, client: httpx.AsyncClient | None = None
) -> dict:
    params = _uuid_or_identifier_params(uuid, identifier)
    return await _request("GET", "/order", params=params, bucket=_DEFAULT_BUCKET, client=client)


```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 22개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/upbit_client.py tests/test_upbit_client.py
git commit -m "feat: upbit_client에 cancel_order/get_order 추가"
```

---

### Task 6: `list_open_orders()` + `list_closed_orders()`

**Files:**
- Modify: `trading/upbit_client.py`
- Modify: `tests/test_upbit_client.py`

**Interfaces:**
- Consumes: Task 3의 `_request`, `_DEFAULT_BUCKET`.
- Produces: `list_open_orders(*, market: str | None = None, states: list[str] | None = None,
  client=None) -> list[dict]`, `list_closed_orders(*, market: str | None = None, states:
  list[str] | None = None, client=None) -> list[dict]`.

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_client.py` 파일 끝에 추가:
```python
from trading.upbit_client import list_closed_orders, list_open_orders


async def test_list_open_orders_sends_market_and_states_array_params(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/orders/open"
        assert request.url.params.get("market") == "KRW-BTC"
        assert request.url.params.get_list("states[]") == ["wait", "watch"]
        return httpx.Response(200, json=[{"uuid": "a"}, {"uuid": "b"}])

    async with _mock_async_client(handler) as client:
        result = await list_open_orders(market="KRW-BTC", states=["wait", "watch"], client=client)

    assert result == [{"uuid": "a"}, {"uuid": "b"}]


async def test_list_open_orders_without_filters_sends_no_params(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {}
        return httpx.Response(200, json=[])

    async with _mock_async_client(handler) as client:
        await list_open_orders(client=client)


async def test_list_closed_orders_sends_market_and_states_array_params(monkeypatch):
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/orders/closed"
        assert request.url.params.get("market") == "KRW-BTC"
        assert request.url.params.get_list("states[]") == ["done", "cancel"]
        return httpx.Response(200, json=[{"uuid": "c"}])

    async with _mock_async_client(handler) as client:
        result = await list_closed_orders(market="KRW-BTC", states=["done", "cancel"], client=client)

    assert result == [{"uuid": "c"}]
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_client.py -v -k "list_open_orders or list_closed_orders"`
Expected: FAIL — `ImportError: cannot import name 'list_open_orders'`

- [x] **Step 3: `trading/upbit_client.py`에 구현 추가**

`async def get_order(...)` 함수 바로 뒤에 추가:
```python
def _market_and_states_params(market: str | None, states: list[str] | None) -> dict:
    params: dict = {}
    if market:
        params["market"] = market
    if states:
        params["states[]"] = states
    return params


async def list_open_orders(
    *,
    market: str | None = None,
    states: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    params = _market_and_states_params(market, states)
    return await _request("GET", "/orders/open", params=params, bucket=_DEFAULT_BUCKET, client=client)


async def list_closed_orders(
    *,
    market: str | None = None,
    states: list[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    params = _market_and_states_params(market, states)
    return await _request(
        "GET", "/orders/closed", params=params, bucket=_DEFAULT_BUCKET, client=client
    )


__all__ = [
    "UpbitCredentialsError",
    "TokenBucket",
    "get_accounts",
    "get_order_chance",
    "create_order",
    "cancel_order",
    "get_order",
    "list_open_orders",
    "list_closed_orders",
]
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_client.py -v`
Expected: 25개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/upbit_client.py tests/test_upbit_client.py
git commit -m "feat: upbit_client에 list_open_orders/list_closed_orders 추가 + __all__ 정리"
```

---

### Task 7: `trading/upbit_ws.py` — `stream_ticker()` (재연결 지수 백오프 포함)

**Files:**
- Create: `trading/upbit_ws.py`
- Create: `tests/test_upbit_ws.py`

**Interfaces:**
- Consumes: 없음(독립 모듈, `trading/upbit_client.py`와 무관 — 인증 불필요한 공개 채널).
- Produces: `trading.upbit_ws.stream_ticker(markets: list[str], *, url: str =
  UPBIT_WS_URL) -> AsyncIterator[dict]`(async generator).

- [x] **Step 1: 실패하는 테스트 작성**

`tests/test_upbit_ws.py`(신규 파일):
```python
import asyncio
import json

import pytest
import websockets

import trading.upbit_ws as upbit_ws
from trading.upbit_ws import stream_ticker


async def test_stream_ticker_sends_subscribe_message_and_yields_parsed_json():
    received_subscribe = asyncio.Future()

    async def handler(ws):
        raw = await ws.recv()
        received_subscribe.set_result(json.loads(raw))
        await ws.send(json.dumps({"type": "ticker", "code": "KRW-BTC", "trade_price": 50000000}))
        await ws.wait_closed()

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}"

        gen = stream_ticker(["KRW-BTC"], url=url)
        first_message = await anext(gen)
        await gen.aclose()

    subscribe_msg = await received_subscribe
    assert subscribe_msg[1] == {"type": "ticker", "codes": ["KRW-BTC"]}
    assert "ticket" in subscribe_msg[0]
    assert first_message == {"type": "ticker", "code": "KRW-BTC", "trade_price": 50000000}


async def test_stream_ticker_decodes_binary_frames():
    async def handler(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "ticker", "trade_price": 1}).encode("utf-8"))
        await ws.wait_closed()

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://localhost:{port}")
        message = await anext(gen)
        await gen.aclose()

    assert message == {"type": "ticker", "trade_price": 1}


async def test_stream_ticker_reconnects_after_connection_drop(monkeypatch):
    monkeypatch.setattr(upbit_ws, "RECONNECT_BASE_DELAY_SECONDS", 0.01)
    connection_count = {"n": 0}

    async def handler(ws):
        connection_count["n"] += 1
        await ws.recv()  # subscribe message
        if connection_count["n"] == 1:
            await ws.send(json.dumps({"seq": 1}))
            await ws.close()  # 첫 연결은 끊어서 재연결을 유도
        else:
            await ws.send(json.dumps({"seq": 2}))
            await ws.wait_closed()

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        gen = stream_ticker(["KRW-BTC"], url=f"ws://localhost:{port}")
        first = await anext(gen)
        second = await anext(gen)
        await gen.aclose()

    assert first == {"seq": 1}
    assert second == {"seq": 2}
    assert connection_count["n"] == 2
```

- [x] **Step 2: 테스트 실행해서 실패 확인**

Run: `python -m pytest tests/test_upbit_ws.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.upbit_ws'`

- [x] **Step 3: `trading/upbit_ws.py` 구현**

```python
"""
trading/upbit_ws.py

업비트 공개 WebSocket(ticker 채널, 인증 불필요) 구독. 캔들은 이 모듈이 다루지 않는다 — 업비트
공개 WS에는 캔들 채널이 없고(공개 채널은 ticker/trade/orderbook뿐), 신호평가용 봉 마감 감지는
데몬이 기존 upbit_data_service.get_candles()를 REST로 폴링해서 처리하기로 확정했다(스펙 서브플랜④
플랜 문서 참고). 이 모듈의 ticker 스트림은 손절/익절 실시간 감지 전용이다.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import websockets

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

RECONNECT_BASE_DELAY_SECONDS = 1.0
RECONNECT_MAX_DELAY_SECONDS = 30.0


async def stream_ticker(markets: list[str], *, url: str = UPBIT_WS_URL) -> AsyncIterator[dict]:
    """markets의 실시간 ticker 이벤트를 무한히 yield한다. 연결이 끊기면 지수 백오프로 자동
    재연결한다 — 재연결 사이에 발생한 tick은 유실될 수 있지만, ticker는 손절/익절 실시간
    감지 전용이라 몇 초의 공백은 다음 tick에서 자연 회복된다(캔들 기반 신호는 이 스트림과
    무관하게 REST 폴링으로 별도 처리되므로 영향 없음)."""
    import asyncio

    subscribe_msg = json.dumps(
        [{"ticket": str(uuid.uuid4())}, {"type": "ticker", "codes": markets}]
    )
    delay = RECONNECT_BASE_DELAY_SECONDS

    while True:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(subscribe_msg)
                delay = RECONNECT_BASE_DELAY_SECONDS
                async for raw in ws:
                    data = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    yield json.loads(data)
        except (websockets.exceptions.ConnectionClosed, OSError):
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)


__all__ = ["stream_ticker", "UPBIT_WS_URL"]
```

- [x] **Step 4: 테스트 실행해서 통과 확인**

Run: `python -m pytest tests/test_upbit_ws.py -v`
Expected: 3개 테스트 전부 PASS

- [x] **Step 5: 커밋**

```bash
git add trading/upbit_ws.py tests/test_upbit_ws.py
git commit -m "feat: upbit_ws에 stream_ticker 추가 (재연결 지수 백오프 포함)"
```

---

### Task 8: 최종 통합 확인 + 전체 회귀

**Files:**
- Modify: `trading/upbit_client.py`(문서화만)
- Modify: `trading/upbit_ws.py`(문서화만)

**Interfaces:**
- Consumes: 이 플랜의 모든 이전 태스크 산출물.
- Produces: 없음(검증 전용 태스크).

- [x] **Step 1: `.env.example`이 실제로 커밋 대상이고 `.env`는 무시되는지 확인**

Run:
```bash
git check-ignore -v .env || echo "NOT IGNORED (문제)"
git status --short .env.example
```
Expected: `git check-ignore -v .env`가 `.gitignore:<N>:.env	.env` 형태로 출력(무시되고 있음
확인). `.env.example`은 `git status`에 추적 대상으로 잡혀야 함(Task 1에서 이미 커밋됐다면
빈 출력이 정상).

- [x] **Step 2: 두 모듈이 서로 독립적으로 import 가능한지, `engine`을 안 쓰는지 확인**

Run:
```bash
python -c "
import ast
for path in ['trading/upbit_client.py', 'trading/upbit_ws.py']:
    tree = ast.parse(open(path, encoding='utf-8').read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    assert 'engine' not in names, f'{path}가 engine을 import함: {names}'
    print(path, '-> engine 미의존 확인, imports:', sorted(names))
"
```
Expected: 두 파일 모두 `engine` 미포함 출력, 에러 없이 통과.

- [x] **Step 3: 전체 테스트 스위트 실행(회귀 확인)**

Run: `python -m pytest -q`
Expected: 전부 PASS(서브플랜③까지의 기존 테스트 + 이 플랜의 `tests/test_upbit_client.py`(25개)
+ `tests/test_upbit_ws.py`(3개) 전부 포함).

- [x] **Step 4: 커밋**

이 태스크는 코드 변경이 없으면(검증만 통과하면) 커밋할 내용이 없다 — Step 1~3이 전부 통과하면
빈 diff이므로 커밋을 생략하고 바로 다음 단계(플랜 문서 체크박스 반영 커밋)로 넘어간다. 만약
검증 중 실제 코드 수정이 필요했다면(예: `engine` 의존이 실수로 들어간 경우) 그 수정을 다음
메시지로 커밋한다:
```bash
git add trading/upbit_client.py trading/upbit_ws.py
git commit -m "fix: upbit_client/upbit_ws 최종 통합 검증에서 발견된 문제 수정"
```

---

## Self-Review

**스펙 커버리지:**
- 모듈 구조 절의 `trading/upbit_client.py`(REST 인증(JWT) 클라이언트 + Throttle/Queue) →
  Task 1~6에서 JWT 인증(Task 1) + Throttle(Task 2, TokenBucket) + Queue(Task 3의 `_request` 공통
  경로 — 모든 호출이 이 함수 하나를 거치므로 "큐"의 역할을 대체) + 7개 엔드포인트(Task 3~6) 전부
  구현.
- 모듈 구조 절의 `trading/upbit_ws.py`(WebSocket 구독: 공개) → Task 7에서 구현. 원래 스펙 문구의
  "캔들/체결" 중 "캔들"은 업비트 공개 WS에 존재하지 않는 채널이라는 걸 이 플랜 작성 세션에서
  확인하고, 사용자 확정으로 캔들은 REST 폴링(별도 코드 불필요, 기존 `upbit_data_service.py`
  재사용)으로 범위를 좁혔다 — 이 플랜의 Architecture 절에 "왜"를 명시함.
- 스펙 "인프라 세부사항 - Rate Limit" → Task 2·3에서 토큰버킷 2개(`default`/`order`)로 구현,
  스펙 원문의 "주문/계좌/시세" 3분류를 업비트 공식 문서 기준 실제 2분류로 정정한 근거를 플랜
  상단에 명시.
- 스펙 "인프라 세부사항 - WebSocket 재연결" → Task 7에서 지수 백오프 재연결 구현. "끊겼다가
  재연결되면 그 사이 누락된 캔들을 REST로 보충"이라는 원문 요구는 캔들이 더 이상 WS 소스가
  아니게 되면서(REST 폴링으로 대체) 자동으로 해소됨 — WS 재연결 갭은 ticker(손절/익절 감지)
  전용이라 몇 초 공백이 캔들 무결성에 영향을 주지 않는다는 것도 플랜에 명시.
- 스펙 "추가로 고려해야 할 항목 - 보안"(API 키 환경변수만, `.env`+`.gitignore`) → Task 1에서
  정확히 이 패턴대로 구현.
- **이 플랜이 다루지 않는 것(의도적):** NTP 자체 점검(스펙 "인프라 세부사항 - NTP")은 데몬의
  주기적 헬스체크 루프에 속하는 기능이라 서브플랜⑤(트레이딩 엔진 코어, `daemon.py`)로 넘긴다 —
  이 클라이언트 라이브러리 자체의 책임이 아니다. 출금(Withdraw) 관련 엔드포인트는 스펙이
  "출금 권한 절대 안 켬"이라고 명시했으므로 애초에 구현하지 않는다(사용자 계정 설정 레벨의
  방어와 코드 레벨에서 아예 호출 함수를 안 만드는 것 둘 다로 이중 방어).

**플레이스홀더 스캔:** 없음 — 모든 스텝에 완전한 코드가 있고, 모든 REST 엔드포인트 경로는 이
플랜 작성 세션에서 업비트 공식 문서(`docs.upbit.com`)로 직접 확인한 값이다(표로 출처 명시).

**타입 일관성:** 모든 엔드포인트 함수는 `(..., *, client: httpx.AsyncClient | None = None) ->
dict | list` 패턴으로 일관되며(키워드 전용 `client` 인자로 테스트 시 mock 주입,
프로덕션에서는 내부적으로 새 클라이언트 생성), `_request()`가 모든 실제 HTTP 호출을 중앙화한다.
`TokenBucket`은 `clock`/`sleep` 주입으로 결정론적 테스트가 가능하며 프로덕션 기본값은 실제
`time.monotonic`/`asyncio.sleep`이다.

---

## 다음 서브플랜 (이 문서 이후)

⑤ **트레이딩 엔진 코어** — `signal_engine.py`(이 서브플랜④의 `upbit_ws.stream_ticker()` +
`upbit_data_service.get_candles()` REST 폴링 + 서브플랜②·③의 `live_indicators.py` +
서브플랜①의 `eval_group_values()`를 전부 결합), `order_executor.py`(이 서브플랜의
`upbit_client.create_order`/`cancel_order`/`get_order`로 시장가/지정가/지정가+타임아웃 3모드
구현, 틱사이즈 라운딩은 `upbit_client.get_order_chance()` 결과로 계산),
`position_manager.py`(복리 자금관리), `risk_manager.py`(전략별 서킷브레이커),
`reconciler.py`(이 서브플랜의 `upbit_client.get_accounts`/`list_open_orders`로 실제 거래소
상태와 내부 DB 대조), `daemon.py` 메인루프(State Hydration, NTP 자체 점검 포함). ⑥ UX.
