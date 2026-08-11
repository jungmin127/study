"""
trading/upbit_ws.py

업비트 공개 WebSocket(ticker 채널, 인증 불필요) 구독. 캔들은 이 모듈이 다루지 않는다 — 업비트
공개 WS에는 캔들 채널이 없고(공개 채널은 ticker/trade/orderbook뿐), 신호평가용 봉 마감 감지는
데몬이 기존 upbit_data_service.get_candles()를 REST로 폴링해서 처리하기로 확정했다(스펙 서브플랜④
플랜 문서 참고). 이 모듈의 ticker 스트림은 손절/익절 실시간 감지 전용이다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

import websockets

logger = logging.getLogger(__name__)

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

RECONNECT_BASE_DELAY_SECONDS = 1.0
RECONNECT_MAX_DELAY_SECONDS = 30.0


async def stream_ticker(markets: list[str], *, url: str = UPBIT_WS_URL) -> AsyncIterator[dict]:
    """markets의 실시간 ticker 이벤트를 무한히 yield한다. 연결이 끊기면(정상 종료·비정상
    종료 둘 다) 지수 백오프로 재연결한다 — 서버가 정상적으로 연결을 끊는 경우(배포 시
    연결 순환 등)도 재연결 폭주를 막기 위해 백오프를 똑같이 적용해야 한다. websockets의
    async for는 정상 종료(ConnectionClosedOK)를 예외 없이 반복문 종료로 처리하므로,
    백오프 로직을 except 절 안이 아니라 try/except 블록 뒤에 둬서 두 경우 모두 커버한다.
    ticker는 손절/익절 실시간 감지 전용이라 재연결 사이에 발생한 tick은 유실될 수 있지만,
    캔들 기반 신호는 이 스트림과 무관하게 REST 폴링으로 별도 처리되므로 영향 없다."""
    subscribe_msg = json.dumps(
        [{"ticket": str(uuid.uuid4())}, {"type": "ticker", "codes": markets}]
    )
    delay = RECONNECT_BASE_DELAY_SECONDS

    while True:
        disconnect_reason: BaseException | str = "정상 종료"
        try:
            async with websockets.connect(url) as ws:
                await ws.send(subscribe_msg)
                delay = RECONNECT_BASE_DELAY_SECONDS
                async for raw in ws:
                    data = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    yield json.loads(data)
        except (websockets.exceptions.WebSocketException, OSError, json.JSONDecodeError) as exc:
            disconnect_reason = exc
        logger.warning("ticker WS 연결 끊김(%s), %.1f초 후 재연결", disconnect_reason, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY_SECONDS)


__all__ = ["stream_ticker", "UPBIT_WS_URL"]
