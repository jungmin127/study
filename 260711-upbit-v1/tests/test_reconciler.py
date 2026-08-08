import trading.reconciler as reconciler
import trading.upbit_client as upbit_client


def test_coin_currency_extracts_ticker_from_market():
    assert reconciler._coin_currency("KRW-BTC") == "BTC"
    assert reconciler._coin_currency("KRW-ETH") == "ETH"


async def test_get_coin_account_returns_matching_currency(monkeypatch):
    async def fake_get_accounts(*, client=None):
        return [
            {"currency": "KRW", "balance": "100000.0", "locked": "0", "avg_buy_price": "0"},
            {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "49000000"},
        ]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    account = await reconciler._get_coin_account("KRW-BTC")

    assert account["currency"] == "BTC"
    assert account["balance"] == "0.01"


async def test_get_coin_account_returns_none_when_not_held(monkeypatch):
    async def fake_get_accounts(*, client=None):
        return [{"currency": "KRW", "balance": "100000.0", "locked": "0", "avg_buy_price": "0"}]

    monkeypatch.setattr(upbit_client, "get_accounts", fake_get_accounts)

    assert await reconciler._get_coin_account("KRW-BTC") is None
