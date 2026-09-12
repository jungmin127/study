---
name: live-trading-retrospective
description: Use when the user wants a retrospective or review of live trading
  performance, asks why live trading has been losing money, or wants to understand
  recurring loss patterns — repeated buy-then-stop-loss cycles in a falling market
  ("계단식 손절"), or exiting too early with small gains during a strong uptrend
  ("찔끔 익절"). Triggers on "라이브 회고", "라이브 매매 회고", "왜 손실났는지", "실거래
  복기" and similar requests.
---

# 라이브 매매 회고

이 스킬은 **분석 + 개선안 제안까지만** 한다 — 전략 조건이나 코드를 직접
수정하지 않는다(사용자가 별도로 요청하면 그때 진행한다).

## 1. AWS 라이브 서버의 trading.db 사본 가져오기

원본을 절대 건드리지 않고 읽기 전용 사본만 로컬로 받는다:

```bash
scp -i ~/Downloads/upbit-server-key.pem \
    ubuntu@43.202.162.151:/opt/study/260711-upbit-v1/data/trading.db \
    /tmp/aws_trading_retrospective.db
```

(pem 키 경로/서버 IP가 바뀌었으면 `deploy/UPDATE.md`, `.env`의
`DEPLOY_SERVER_HOST`를 확인해 그 값을 쓴다.) **로컬 `data/trading.db`를 쓰지
않는다** — 그건 개발/그리드서치용 별개 이력이다.

## 2. 분석 스크립트 실행

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python .claude/skills/live-trading-retrospective/retrospective.py /tmp/aws_trading_retrospective.db
```

세 섹션을 출력한다(각 섹션의 정확한 로직은 스크립트 참고):
1. 마켓별 전체 성과 + 청산 사유별(`close_reason`) 분포
2. 계단식 손절 반복 패턴 — 같은 전략이 연속 3회 이상 손실(`realized_pnl_pct < 0`)로
   청산된 구간(`close_reason`이 `stop_loss_pct`가 아니라 `signal`이어도 잡아낸다 —
   STOP_LOSS_PCT 조건 자체를 안 쓰는 전략도 매수/매도 신호만으로 같은 패턴을
   만들 수 있기 때문)
3. 조기 익절 패턴 — `take_profit_pct`로 청산된 뒤 72시간 내 가격이 3% 넘게
   더 오른 경우(캔들 데이터를 실제로 조회해 확인)

## 3. 결과 해석 + 개선안 제시

스크립트 출력을 그대로 붙여넣지 말고, 아래 세 관점으로 정리해서 보고한다.

**전체 회고("왜 실패했나")**
- 마켓별/사유별 표에서 손실이 집중된 코인·사유를 짚는다.
- `close_reason` 분포에서 `stop_loss_pct`가 압도적으로 많으면, 진입 조건
  자체가 하락장에서도 매수 신호를 내고 있다는 뜻이다.

**계단식 손절 반복("매수-손절-매수-손절")**
- 스크립트가 찾은 연속 손절 구간을 짚고, 원인을 설명한다: 매수 조건에
  추세/장세 필터가 없어 하락장에서도 오실레이터 과매도 신호만 보고 진입하는
  경우가 흔하다.
- 개선안 후보(코드 수정은 하지 않고 이렇게 제안만 한다):
  1. 매수 조건에 장세 필터 추가 — 이미 있는 ADX 기반 장세 판별
     (`engine/regime_adx_service.py`, `/regime` 탭)을 활용해, 하락 장세로
     판별된 동안은 해당 코인 자동매매를 일시정지하거나 `regime-strategy-pipeline`
     skill로 하락장 전용 전략을 따로 발굴해 자동스왑을 켜는 방법
  2. 손절 후 즉시 재진입을 막는 쿨다운(예: N봉 동안 재매수 금지) — 지금
     조건트리엔 이런 지표가 없으니 신규 지표로 검토 가능(제안만, 구현은
     사용자가 별도 요청할 때)
  3. 손절폭을 넓히거나(-5%→-8% 등), 변동성에 따라 동적으로 조정

**조기 익절("찔끔 먹고 나옴")**
- 스크립트가 찾은 "익절 후에도 더 오른" 사례를 짚는다.
- 개선안: 고정 `TAKE_PROFIT_PCT` 대신 `TRAILING_STOP_STEP_PCT`(계단식
  트레일링 스탑, `docs/superpowers/specs_v2/2026-09-11-trailing-stop-step-design.md`)로
  매도조건을 바꾸면 상승 추세에서는 이익을 계속 따라가다가 꺾일 때만
  청산된다 — 해당 코인/장세로 `regime-strategy-pipeline`을
  `--trailing-stop-step-pcts`와 함께 다시 돌려 최적 step을 찾는 것도 방법.

## 주의사항

- 이 회고는 항상 AWS 서버 사본을 써야 한다. 로컬 `data/trading.db`는 별개
  이력이라 회고 대상으로 쓰면 안 된다.
- `/tmp/aws_trading_retrospective.db`는 분석 후 삭제해도 된다(다음에 다시
  받으면 된다).
- 3번(조기 익절) 분석은 `upbit_data_service.get_candles()`로 실제 캔들을
  조회하므로, 익절 건수가 많으면 API 호출도 그만큼 늘어난다(로컬 캐시가
  있으면 캐시를 먼저 쓴다).
