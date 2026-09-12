---
name: regime-strategy-pipeline
description: 코인 하나를 지정하면 장세(하락/횡보/상승)별 grid search를 자동으로
  돌려 TP/SL을 부착한 최종 전략을 전략 라이브러리에 매핑한다. "장세전략 파이프라인
  <코인>", "<코인> 전략 자동발굴" 같은 요청에 사용한다.
---

# 장세 전략 자동 발굴 파이프라인

설계 문서: `docs/superpowers/specs_v2/2026-09-06-regime-strategy-auto-pipeline-design.md`

1. 사용자가 준 코인명을 마켓코드(`KRW-XXX`)로 변환한다(`engine/regime_adx_constants.py`의
   `MAJOR_MARKETS` 참고). 목록에 없는 코인이면 지원하지 않는다고 안내하고 중단한다.
2. 로컬에서 이미 실행 중인 grid search(웹 탭 `/grid-search`의 job 큐)가 있는지
   사용자에게 확인한다 — 있으면 멀티프로세싱 워커 리소스가 겹치니 끝난 뒤
   실행하라고 안내한다.
3. `--history-start`는 사용자가 명시하지 않으면 이번 달 1일로 기본값을 잡되,
   실행 전 사용자에게 확인한다. 그 외 옵션은 아래 표의 기본값을 그대로 쓰되,
   사용자가 조정을 원하면 반영한다.

   | 플래그 | 기본값 | 설명 |
   | --- | --- | --- |
   | `--market` | (필수) | 마켓코드 (예: `KRW-ETH`) |
   | `--history-start` | (필수) | 시작일 `YYYY-MM-DD` (이 날짜 이후 세그먼트만 사용) |
   | `--capital` | `10000000` | 운용자금(원), 0보다 커야 함 |
   | `--min-days` | `10` | 세그먼트 최소 길이(일), 1 이상이어야 함 |
   | `--stop-loss-pct` | `-5.0` | 손절 퍼센트 — **반드시 음수** |
   | `--take-profit-pct` | `8.0` | 익절 퍼센트 — **반드시 양수** |
   | `--candidate-pool` | `20` | 재검증할 후보 개수, 1 이상이어야 함 |
   | `--trailing-stop-step-pcts` | (없음, 미적용) | 계단식 트레일링 스탑 step(%) 후보 목록, 콤마 구분 (예: `2,3,4,5`) |

   `--stop-loss-pct`/`--take-profit-pct` 부호가 잘못되면(예: 둘 다 양수) 그리드
   서치가 시작되기 전에 즉시 에러로 중단된다 — 부호가 뒤집히면 손절 조건이
   거의 항상 참이 되어 퇴화 전략이 만들어지기 때문.

   `--trailing-stop-step-pcts`를 지정하면 매도조건에 `TRAILING_STOP_STEP_PCT`
   조건(`손절선 = (floor(고점/step) - 1) × step`, 계단식으로 이익을 잠금)을
   SL/TP와 함께 OR로 붙인다. 후보 × step 조합을 전부 백테스트해, 거래횟수
   조건을 만족하는 조합 중 **최종 수익률이 가장 높은 것**을 채택한다(그리드
   서치 철학 — 순서상 먼저 통과하는 게 아니라 전체 중 최적을 고른다. 이
   "전체 조합 중 최고 성과 채택" 원칙은 `pick_final_strategy()`의 기본
   동작이라 이 옵션을 안 써도 SL/TP만으로 후보를 고를 때도 동일하게
   적용된다). 지정하지 않으면 계단식 트레일링을 붙이지 않는다(기존 동작).
   현재는 백테스트 전용이며, 실거래 daemon에는 연동되어 있지 않다
   (`trading/signal_engine.py`가 `position_peak_return_pct`를 넘기지 않아
   라이브에서는 이 조건이 항상 False로 무시된다 — 연동은 별도 플랜 범위).
4. 다음 명령을 `run_in_background: true`로 실행한다(코인 하나당 grid search가
   6개 카테고리 전체를 도는데 수 시간~십수 시간 걸릴 수 있다):
   ```
   PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
       --market <마켓코드> --history-start <YYYY-MM-DD> \
       [--trailing-stop-step-pcts 2,3,4,5]
   ```
5. 완료되면 stdout의 `RESULT_JSON:` 라인을 파싱해 라벨별 결과(매핑됨/스킵됨/
   실패, 기간, 수익률, 거래횟수, 채택된 트레일링스탑 step(있는 경우), 사유)를
   표로 정리해 사용자에게 보여준다. 행에 "[!] 진행중/짧은 구간" 표시가 있으면,
   해당 장세 구간이 아직 진행중이거나 최소 길이 미만이라 앞선 장세가 섞여
   들어갔을 수 있다는 뜻이니 함께 안내한다.
6. 마지막에 반드시 안내한다: "라이브 배포는 이 파이프라인의 범위 밖입니다 —
   `/strategy-library`에서 결과를 확인하고, 만족스러우면 라이브 전략을 만들거나
   기존 전략에 자동스왑을 켜주세요." 같은 코인/장세 조합으로 다시 실행하면
   기존 매핑을 덮어쓴다(upsert)는 점도 함께 안내한다.
