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
