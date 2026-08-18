# 매수 수수료 소급 재계산 백필 절차

`scripts/backfill_entry_fee.py`를 상시 가동 중인 AWS 서버의 실제 `trading.db`에
적용하는 절차다. 실거래 데이터를 직접 고치는 1회성 작업이라 순서를 반드시 지킨다.
스크립트 자체의 매칭/보정 로직은 `scripts/backfill_entry_fee.py` 모듈 docstring 참고.

## 1. daemon 먼저 정지

이 스크립트는 살아있는 writer에 대한 락(lock)이 없다 — daemon이 같은 DB에 동시에
쓰는 상태로 실행하면 안 된다.

```bash
ssh -i <다운로드한-키파일>.pem ubuntu@<탄력적 IP>
sudo systemctl stop daemon
systemctl status daemon   # inactive (dead)인지 확인
```

backend/frontend는 굳이 멈추지 않아도 된다(둘 다 `trading.db`에 쓰지 않고 읽기만
한다) — 다만 백필 도중에는 저널/전략 목록 페이지 숫자가 과도기 상태로 보일 수
있으니 참고만 한다.

## 2. 복사본으로 드라이런

원본을 직접 만지기 전에 복사본에 대고 먼저 드라이런(기본값, `--apply` 없이)해서
무엇이 바뀔지 확인한다.

```bash
cd /opt/study/260711-upbit-v1
cp data/trading.db /tmp/trading.db.dryrun-copy
python3 -c "
import trading.db as db
from pathlib import Path
db.DB_PATH = Path('/tmp/trading.db.dryrun-copy')
from scripts import backfill_entry_fee as bf
bf.run(apply=False)
" | tee /tmp/backfill-dryrun.log
```

`/tmp/backfill-dryrun.log`를 끝까지 읽는다. 특히 다음 두 종류의 경고 줄을 놓치지
않는다 — 둘 다 "그 항목은 건드리지 않고 넘어간다"는 뜻이라 위험하지는 않지만, 왜
매칭/집계가 안 됐는지 납득이 되는지 확인한다:

- `건너뜀 (주문/포지션 개수 불일치): live_strategy_id=...` — 그 전략 전체가
  스킵됐다는 뜻.
- `경고: daily_performance ...` (행 없음 또는 거래 수 불일치) — 그 날짜의
  daily_performance는 덮어쓰지 않았다는 뜻.

출력이 납득되지 않으면(예상보다 훨씬 많은 전략/날짜가 건너뛰어짐 등) 여기서 멈추고
원인을 먼저 파악한다 — 3단계로 넘어가지 않는다.

## 3. 실제 DB에 적용

드라이런 출력이 납득되면 실제 `trading.db`에 대고 `--apply`로 실행한다.

```bash
cd /opt/study/260711-upbit-v1
python3 scripts/backfill_entry_fee.py --apply | tee /tmp/backfill-apply.log
```

스크립트가 실행 첫 줄에서 자동으로 백업을 만든다 — `data/trading.db.bak-<타임스탬프>`
(원본과 같은 디렉터리, 즉 `/opt/study/260711-upbit-v1/data/`). 백업 경로가 출력되면
꼭 기록해둔다(4단계에서 필요할 수 있다).

## 4. 도중에 에러가 나거나 중단되면 — 재실행 금지, 백업 복원

`--apply` 실행이 에러로 죽거나(Ctrl+C, SSH 끊김 등) 중간에 멈추면, **스크립트를
다시 실행하지 않는다.** 이미 보정된 포지션은 멱등성 가드 덕분에 재실행해도 이중
차감되지는 않지만, 그 포지션들의 daily_performance 재계산은 전략 단위로 포지션
보정이 다 끝난 뒤에 한 번에 도는 구조라 — 중간에 죽으면 "포지션은 고쳐졌는데 그
날짜의 daily_performance는 아직 반영 안 된" 상태로 남을 수 있고, 이 상태에서
재실행해도 다시 채워지지 않는다(이미 entry_fee가 채워진 포지션은 다음 실행에서
매칭은 되지만 재계산 대상에서는 빠진다). 안전하게 복구하는 방법은 딱 하나다:

```bash
sudo systemctl stop daemon   # 아직 안 멈췄다면
cp data/trading.db.bak-<타임스탬프> data/trading.db
```

백업으로 되돌린 뒤, 에러 원인을 먼저 파악하고 스크립트를 고친 다음 2단계(드라이런)
부터 다시 시작한다.

## 5. 재개 및 검증

```bash
sudo systemctl start daemon
systemctl status daemon backend frontend   # 셋 다 active (running)인지 확인
```

프론트엔드(또는 `curl`)로 매매일지 API를 확인해 `cumulative_pnl`이 그럴듯한
값인지 점검한다:

```bash
curl -s http://127.0.0.1:8000/api/v1/journal/summary | head -c 500
curl -s "http://127.0.0.1:8000/api/v1/journal/markets/KRW-BTC" | head -c 500
```

`/tmp/backfill-apply.log`에 찍힌 포지션별 보정 로그(`realized_pnl X -> Y`) 중
아는 거래 한두 건을 골라, 위 API 응답의 해당 거래 `realized_pnl`이 로그에 찍힌
`Y` 값과 일치하는지 직접 대조한다.
