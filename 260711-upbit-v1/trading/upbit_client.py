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
import logging
import os
import time
import uuid
from urllib.parse import unquote, urlencode

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

UPBIT_BASE_URL = "https://api.upbit.com/v1"

RETRY_ATTEMPTS = 3
RATE_LIMIT_BACKOFF_SECONDS = 1.0


class UpbitCredentialsError(Exception):
    """UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 환경변수가 설정되지 않았을 때."""


class UpbitRateLimitError(RuntimeError):
    """업비트 API가 재시도 한도(RETRY_ATTEMPTS) 내에서도 계속 429를 반환했을 때. RuntimeError를
    상속해 기존 호출부의 광범위한 except RuntimeError를 깨지 않으면서도, 명시적으로 이 실패를
    구분해 잡고 싶은 호출부(예: 향후 주문 실행기)가 UpbitRateLimitError로 특정할 수 있게 한다."""


class TokenBucket:
    """rate_per_sec 속도로 토큰을 채우는 비동기 토큰버킷. acquire()는 토큰이 있으면 즉시
    반환하고, 없으면 다음 토큰이 채워질 때까지 대기한다. clock/sleep을 주입할 수 있어
    테스트에서 실제 시간 흐름 없이 결정론적으로 검증 가능하다. 프로세스 전역 싱글턴으로
    공유되는 것을 전제로 하며, 이 데몬의 단일 asyncio 이벤트루프 안에서만 사용해야 한다
    (내부 락이 첫 acquire() 시점에 실행 중인 루프에 바인딩되므로, 서로 다른 이벤트루프가
    동시에 같은 버킷을 두고 경쟁하는 상황은 지원 대상이 아니다)."""

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
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
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
    다른 프로세스의 동시 사용 등) 방어적으로 재시도한다. 재시도할 때마다 JWT(nonce 포함)를
    새로 만들고 bucket.acquire()도 다시 거쳐야 한다 — 그렇지 않으면 429로 스로틀링이 필요한
    바로 그 순간에 재시도 요청들이 토큰버킷을 우회하고, nonce가 재사용된 요청을 업비트가
    거부할 위험도 생긴다."""
    close_client = client is None
    client = client or httpx.AsyncClient(timeout=10)
    url = f"{UPBIT_BASE_URL}{path}"

    try:
        for attempt in range(RETRY_ATTEMPTS):
            headers = _build_jwt_headers(params)
            await bucket.acquire()
            if method == "POST":
                resp = await client.request(method, url, json=params, headers=headers)
            else:
                resp = await client.request(method, url, params=params, headers=headers)
            if resp.status_code == 429:
                logger.warning(
                    "업비트 429 재시도 %d/%d: %s %s", attempt + 1, RETRY_ATTEMPTS, method, path,
                )
                await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        logger.error("업비트 API 호출 실패(429 재시도 소진): %s %s", method, path)
        raise UpbitRateLimitError(f"업비트 API 호출 실패 (429 재시도 소진): {method} {path}")
    finally:
        if close_client:
            await client.aclose()


async def get_accounts(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    return await _request("GET", "/accounts", bucket=_DEFAULT_BUCKET, client=client)


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
    "UpbitRateLimitError",
    "TokenBucket",
    "get_accounts",
    "get_order_chance",
    "create_order",
    "cancel_order",
    "get_order",
    "list_open_orders",
    "list_closed_orders",
]
