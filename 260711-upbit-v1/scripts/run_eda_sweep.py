"""
수동 통합 스모크 테스트. 실제 Upbit API로 소수 코인을 스윕하고 결과를 sweep_history에 채운다.
Run: python scripts/run_eda_sweep.py
"""
from datetime import datetime, timedelta, timezone

from engine.sweep import run_sweep
from signals import SIGNAL_REGISTRY


def main() -> None:
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=90)

    solo_sets = [(name, [signal], False) for name, signal in SIGNAL_REGISTRY.items()]
    combined_set = ("mixed_all", list(SIGNAL_REGISTRY.values()), True)

    run_sweep(
        markets=["KRW-BTC", "KRW-ETH"],
        timeframes=["days"],
        signal_sets=[*solo_sets, combined_set],
        start=start,
        end=end,
    )
    print("스윕 완료. FastAPI(uvicorn backend.main:app --port 8000)와 "
          "Next.js(cd frontend && npm run dev)를 띄운 뒤 http://localhost:3000 에서 확인하세요.")


if __name__ == "__main__":
    main()
