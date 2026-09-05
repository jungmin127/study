"""
tests/test_regime_adx_constants.py

engine.regime_adx_constants.MAJOR_MARKETS의 형식(개수/포맷/중복 여부)을 검증한다.
"""
from __future__ import annotations

from engine.regime_adx_constants import MAJOR_MARKETS


def test_major_markets_has_twenty_unique_krw_markets():
    assert len(MAJOR_MARKETS) == 20
    assert len(set(MAJOR_MARKETS)) == 20
    assert all(m.startswith("KRW-") for m in MAJOR_MARKETS)


def test_major_markets_includes_required_coins():
    required = {"KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-LINK", "KRW-DOGE", "KRW-ADA"}
    assert required.issubset(set(MAJOR_MARKETS))
