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
