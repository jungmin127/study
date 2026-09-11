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
   실행 전 사용자에게 확인한다.
4. 다음 명령을 `run_in_background: true`로 실행한다(코인 하나당 grid search가
   6개 카테고리 전체를 도는데 수 시간~십수 시간 걸릴 수 있다):
   ```
   PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
       --market <마켓코드> --history-start <YYYY-MM-DD>
   ```
5. 완료되면 stdout의 `RESULT_JSON:` 라인을 파싱해 라벨별 결과(매핑됨/스킵됨/
   실패, 기간, 수익률, 거래횟수, 사유)를 표로 정리해 사용자에게 보여준다.
6. 마지막에 반드시 안내한다: "라이브 배포는 이 파이프라인의 범위 밖입니다 —
   `/strategy-library`에서 결과를 확인하고, 만족스러우면 라이브 전략을 만들거나
   기존 전략에 자동스왑을 켜주세요."
