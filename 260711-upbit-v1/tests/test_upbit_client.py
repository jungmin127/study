import hashlib
from urllib.parse import unquote, urlencode

import jwt
import pytest

import trading.upbit_client as upbit_client
from trading.upbit_client import UpbitCredentialsError, _build_jwt_headers, TokenBucket


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
