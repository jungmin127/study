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
