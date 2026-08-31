# 장세 판별 ML — 잔여 작업 백로그 (2026-08-31 갱신 7)

이전 라운드(2026-08-27 정리본)의 A1/A2/B/E는 전부 완료·배포됨. 2026-08-30 첫
갱신은 그 이후 진행 상황 반영 + 사용자가 새로 제시한 4개 항목 우선순위 판별.
같은 날 두 번째 갱신은 ①(fact 라벨 백테스트) 완료 결과 반영 + 사용자가 새로
요청한 "코인별 fact 장세 구간 뷰어"를 ② 착수 전 선행 작업으로 삽입. 2026-08-31
세 번째 갱신은 ②(모델 성능 개선) 세부 착수 결과 반영. 네 번째 갱신은 c-2
착수 전 사용자 요청으로 진행한 캘린더/환율/금리 신규 피처 라운드 중 캘린더
그룹 결과 반영. 다섯 번째 갱신은 이 라운드 완전 종료 — 3개 그룹 전부
폐기(순net 코드변경 0)로 확정, 구조적 원인(우선순위0) 재검토를 최우선
후보로 추가. 여섯 번째 갱신은 우선순위0 조사 결과를 반영한 안전
신호 재시도가 성공 — pooled weighted kappa **0.096→0.106으로 개선**,
이 라운드에서 처음으로 baseline을 넘긴 결과. **일곱 번째 갱신(이번)은
우선순위0 액션아이템 2번(주가지수 피처)의 S&P500/다우/나스닥 수익률
3개를 eta² 사전측정 후 시도해 채택(중립)** — pooled weighted kappa
**0.106→0.108**, 델타(+0.002)는 자연변동폭(±0.005) 이내라 확정적
개선으로 보기 어려움. AWS 배포는 사용자 확인 대기(두 라운드 모두
아직 미배포).

## 안전 신호 재시도 (2026-08-31, 우선순위0 조사 직후) — 성공, kappa 0.096→0.106

`docs/superpowers/plans/2026-08-31-regime-ml-safe-signal-retry.md`. 우선순위0
eta² 조사에서 "개별로는 안전(eta²≈0)한데 위험 신호와 한 그룹으로 묶여서
같이 폐기됐을 뿐"이라고 판단된 신호만 추려서 재시도했다.

- **캘린더 3신호(HOUR_SIN/COS, DOW_SIN/COS, DAY_OF_MONTH_SIN/COS, MONTH 제외)
  — 채택**: pooled weighted kappa **0.096 → 0.106으로 개선**(macro F1도 개선).
  eta² 사전측정(HOUR/DOW/DAY_OF_MONTH 전부 ≤0.002)이 실제 kappa 개선으로
  이어진 첫 사례 — 우선순위0 조사가 실전에서 검증됨. 커밋 `b04d940`.
- **환율 1신호(USDKRW_RETURN만) — 채택(중립)**: pooled weighted kappa=0.106으로
  변화 없음(feature importance top-15에도 안 잡힘). "개선/유지되면 채택"
  규칙대로 유지했으나, 순수 이득은 없는 중립 피처 — 서빙 경로는 Task1~3에서
  이미 무조건 fetch하던 `usdkrw_rate_value` 컬럼을 재사용할 뿐이라 신규
  운영 리스크는 없음. 커밋 `94eceea`.
- **최종 상태**: `MONTH_SIN`/`MONTH_COS`, `USDKRW_VOLATILITY`, `UPBIT_FX_SPREAD`,
  금리 3종(`US_KR_RATE_SPREAD`/`YIELD_CURVE_SPREAD`/`HOURS_SINCE_RATE_DECISION`)은
  여전히 미도입(우선순위0에서 위험 확인됨). **로컬 재학습된 모델의 pooled
  weighted kappa=0.106, macro F1 개선 — 아직 AWS 라이브 배포 안 함, 사용자
  확인 후 진행할 것**([[upbit-v1-deploy-check-open-positions-first]] 원칙 —
  배포 전 오픈포지션 확인 필수).

## 주가지수 수익률 피처 라운드 (2026-08-31, 안전 신호 재시도 직후) — 채택(중립), kappa 0.106→0.108(노이즈 범위)

`docs/superpowers/plans/2026-08-31-regime-ml-stock-index-features.md`. 우선순위0
액션아이템 2번(코스피/코스닥/S&P500/다우존스/나스닥 지수 피처, 사용자 제안)
중 FRED에서 바로 가져올 수 있는 S&P500/다우존스/나스닥종합 3개만 우선
시도했다(코스피/코스닥은 FRED에 없음, yfinance는 실제 동작 확인했으나 신규
pip 의존성이라 이번 라운드 보류 — 데이터 소스 재조사부터 별도 세션).

- **eta² 사전측정(우선순위0 방법 재사용)**: KRW-ETH 1개 마켓 5-fold 기준
  SP500/DJIA/NASDAQCOM 종가의 `pct_change()`(순수 t 대 t-1 시간축 차분,
  레벨이나 지수 간 스프레드는 처음부터 배제) 전부 eta²=0.0002로
  `USDKRW_RETURN`(0.0001)과 동급 안전 판정 — 백로그가 예상했던 "환율/금리
  중간 위험도"보다 훨씬 낮게 나왔다. 우선순위0 결론3("진짜 효과 있는 변환은
  시간축 차분")이 그대로 재현됨.
- **`SP500_RETURN`/`DJIA_RETURN`/`NASDAQ_RETURN` — 채택(중립)**: 실데이터
  20마켓 walk-forward 학습에서 pooled weighted kappa **0.106(baseline) →
  0.108**(macro F1 0.549). 결정 규칙("kappa >= baseline이면 채택")대로 유지.
  단, 3개 피처 모두 5개 fold의 feature importance(gain) top-15 어디에도
  등장하지 않았다 — `USDKRW_RETURN`(중립 채택)과 같은 패턴으로, 순수 이득이
  크지는 않지만 해롭지 않은 안전한 추가. leave-one-out은 Global Constraints/
  브리프 지시대로 생략(eta² 사전측정으로 3개 다 동급 안전 확인됨). **이번 세션
  최종 리뷰(2026-08-31)에서 추가된 지적**: 델타(+0.002)가 안전 신호 재시도
  라운드에서 실측된 자연변동폭(±0.005, 0.092~0.097)보다도 작고, 이 백로그
  자체가 정한 재검증 임계값(델타 0.01~0.02대는 seed/기간을 바꿔 재현 확인
  필요)보다도 작아 확정적 개선의 근거로 삼기는 어렵다. 게다가 이 3개 피처는
  일간 종가를 시간봉에 forward-fill하는 구조라, 이미 알려져 있던 최대 21시간
  룩어헤드(`T10Y2Y`/`USDKRW`와 동일 패턴, 설계 시 감수하기로 한 한계)가 단순히
  낙관적인 오프라인 지표를 만드는 데 그치지 않고, 학습 시점(오프라인, 해당일
  종가를 이미 본 것처럼 병합됨)과 실서빙 시점(라이브, FRED가 아직 그날 종가를
  발표하기 전이라 하루 전 종가를 보게 됨)의 실효 지연(lag)이 서로 달라지는
  train/serve skew를 만들 수 있다는 우려도 함께 제기됐다. 이 지적이 결정
  규칙의 기계적 결과(kappa가 baseline 이상이라 채택·코드에 유지)를 뒤집지는
  않는다 — 피처는 revert하지 않고 그대로 둔다. 다만 이 라운드가 스스로
  "개선"이라 자평한 확신 수준은 "중립, 미확증"으로 낮춘다.
- **최종 상태**: 로컬 재학습된 모델의 pooled weighted kappa=0.108(직전
  baseline 0.106 대비 +0.002, macro F1 0.549) — 위에서 설명했듯 이 델타는
  자연변동폭 이내라 확정적 개선으로 보기 어려움. 아직 AWS 라이브 배포 안 함,
  사용자 확인 후 진행할 것([[upbit-v1-deploy-check-open-positions-first]]
  원칙 — 배포 전 오픈포지션 확인 필수). `KOSPI`/`KOSDAQ` 2개 지수는 여전히
  미도입(FRED 미제공, 스코프 밖).

## 캘린더/환율/금리 피처 라운드 (2026-08-31, c-2 착수 전 삽입) — 종료, 3개 그룹 전부 폐기(위 재시도로 일부 회수됨)

`docs/superpowers/specs/2026-08-31-regime-ml-macro-calendar-features-design.md`/
`docs/superpowers/plans/2026-08-31-regime-ml-macro-calendar-features.md`.
subagent-driven-development로 진행, Task 1~7 전부 완료. Task 1~3(FRED/Frankfurter
데이터 서비스 + 학습 로더 배선)의 인프라 코드는 그대로 남아있으나(다음에 매크로
피처를 다시 시도할 때 재사용 가능), Task 4~6(캘린더/환율/금리 3개 피처 그룹)은
**전부 실측에서 baseline 미만으로 나와 revert** — 이 라운드의 순net 코드 변경은
0이다(모델/서빙 로직 변경 없음, 배포 여부 결정도 필요 없음).

- **캘린더 그룹(시간대/요일/월/월중, KST 기준 sin/cos 8개) — 폐기**: 구현
  자체는 TDD 전부 통과(수식/KST변환/테스트 독립성 리뷰로 버그 없음 확인)했으나,
  실데이터 20마켓 walk-forward 학습에서 pooled weighted kappa가 **0.096(baseline)
  → 0.060으로 악화**, macro F1도 0.534→0.528로 하락. Feature importance(gain)
  상위권에 `DAY_OF_MONTH_SIN`/`DAY_OF_MONTH_COS`(1~2위), `MONTH_SIN`/`MONTH_COS`,
  `DOW_SIN`/`DOW_COS`가 모두 올라온 것이 근거 — 캘린더 값 자체는 주기적으로
  반복되지만, 워크포워드 fold가 실제 달력 시간순으로 나뉘기 때문에 특정
  월/월중 패턴이 특정 fold(학습구간)에만 통하는 프록시로 작동해 out-of-fold
  일반화를 오히려 깎아먹었다 — 이미 제거된 `LISTING_AGE_BARS`/`FEAR_GREED_CMC`와
  동일한 "fold-position leakage" 패턴(단조증가가 아니라 주기적 반복이어도
  재현됨, 사전 예상과 다름). 결정규칙대로 `git revert HEAD`로 폐기(커밋
  `f383608`→`a2909c2`), leave-one-out은 그룹 미채택이라 생략. **baseline은
  0.096 그대로 유지**, 다음 그룹(환율)도 이 baseline 기준으로 평가.
- **환율 그룹(USDKRW_RETURN/USDKRW_VOLATILITY/UPBIT_FX_SPREAD) — 폐기**:
  구현 자체는 리뷰에서 3개 수식 전부 기존 `RAW_SCORE` 컨벤션과 byte-for-byte
  일치 확인(버그 아님). 실데이터 20마켓 학습에서 pooled weighted kappa
  **0.096(baseline) → 0.062로 악화**, macro F1 0.530. `UPBIT_FX_SPREAD`/
  `USDKRW_VOLATILITY`가 전 fold gain 1~2위였는데도 out-of-fold 성능은
  나빠졌다 — 캘린더 그룹과 동일하게 "gain 상위권 = 성능 기여 신뢰 불가"
  패턴 반복(이제 LISTING_AGE_BARS/FEAR_GREED_CMC/캘린더/환율까지 4번째
  사례). 결정규칙대로 `git revert HEAD`로 폐기(커밋 `2a5e57b`→`08b34cf`).
  leave-one-out 생략. **baseline은 0.096 그대로 유지**, 다음 그룹(금리)도
  이 baseline 기준으로 평가. 프로세스 노트: Task 3이 `tests/test_train_regime_ml.py`/
  `tests/test_regime_ml_service.py`의 합성 fixture를 새 raw 컬럼에 맞춰
  동기화해두지 않아서, 이 피처를 실제로 쓰는 태스크가 매번 그 fixture를
  최소 침습으로 손대야 했다(revert되면 함께 원복). 금리 그룹(Task 6)에서도
  재발 가능성 있음.
- **금리 그룹(US_KR_RATE_SPREAD/YIELD_CURVE_SPREAD/HOURS_SINCE_RATE_DECISION)
  — 폐기**: 구현 자체는 리뷰에서 `_hours_since_last_change` 헬퍼(NaN 처리,
  두 시리즈 중 최신 변경 선택하는 `min()` 로직)를 손계산으로 직접 검증해
  버그 없음 확인. 실데이터 20마켓 학습에서 pooled weighted kappa
  **0.096(baseline) → 0.050으로 악화**(3개 그룹 중 가장 큰 악화폭), macro F1
  0.525. 3개 피처 전부 gain 1~3위였는데도 out-of-fold 성능은 최악 — 이제
  LISTING_AGE_BARS/FEAR_GREED_CMC/캘린더/환율/금리까지 **5번째** 동일 패턴
  사례. 결정규칙대로 `git revert HEAD`로 폐기(커밋 `495e239`→`ebf79d6`).
  leave-one-out 생략.
- **라운드 종합 결론**: 폐기된 5개 피처(LISTING_AGE_BARS, FEAR_GREED_CMC,
  캘린더 8개, 환율 3개, 금리 3개) 전부 "**전 마켓에 공유되는 시간축 또는
  매크로 외부 시계열**" 성격이었다 — 반면 유지 중인 피처들(RAW_SCORE,
  VPIN_SCORE, VOLATILITY_PERCENTILE, BETA_NEUTRAL_RETURN 등)은 각 마켓
  자신의 OHLCV에서 파생된 자기참조적/상대적 값이다. **다음에 같은 유형의
  피처(글로벌 주가지수 — 코스피/코스닥/S&P/다우/나스닥 등, 원자재 가격 등)를
  또 시도하기 전에, 개별 피처 단위 ablation보다 "왜 전역 공유 시계열이 이
  walk-forward 설정에서 구조적으로 해로운가"에 대한 근본 원인부터 조사하는
  걸 강력 권장.**
  - **외부 검증(사용자가 별도 세션에서 리서치, 2026-08-31)**: 시계열 이진분류
    모델 선택 리서치 결과가 두 가지를 재확인해줬다 — (1) 우리 접근(LightGBM+
    피처엔지니어링, walk-forward 검증)이 이 유형의 문제("원시 신호 분류"가
    아니라 "KPI 하락 예측")에서 실무 표준과 일치, (2) **반복적으로 피처를
    넣었다 뺐다 하며 OOF kappa로 비교하는 것 자체가 다중비교(selection
    bias) 위험** — 하락 이벤트가 희귀하면 kappa 분산이 커서 0.02~0.05 차이는
    노이즈일 수 있다는 경고(`docs/ML_Regime_Switching_Additional_Improvements.md`
    1-3절 "다중 시행에 의한 selection bias — PBO"와 동일 내용, 독립 소스에서
    재확인됨). 이번 라운드의 실측 델타(-0.034~-0.046)는 ②라운드에서 이미
    측정된 자연변동폭(±0.005 수준, 0.092~0.097)의 7~9배라 노이즈로 보기
    어렵고, 3개 그룹이 독립적으로 전부 같은 방향(gain 높음+kappa 나쁨)으로
    나온 것도 노이즈 가설과 안 맞음 — 이번 폐기 결정 자체는 신뢰할 만하다.
    다만 **앞으로 델타가 애매하게 작을 때(0.01~0.02대)는 seed/기간을 바꿔
    재현되는지 확인하는 절차를 추가**할 필요가 있다는 점은 새 액션아이템으로
    남긴다.

## 완료된 것 (더 이상 백로그 아님)

- **A2(모델 성능 표기)** — SHIPPED&PUSHED 2026-08-28. ML 카드에 macro F1/weighted
  kappa 노출.
- **E(규칙기반 제거)** — SHIPPED&PUSHED 2026-08-28. 장세 판별은 ML 전용.
- **A1(재학습 자동화 셀프서비스 UI)** — SHIPPED&PUSHED 2026-08-28. 관리자 패널에서
  학습 시작/배포 가능.
- **B(마켓 확장)** — 2026-08-29 14마켓 확장 후 2026-08-30 세션에서 재설계:
  3클래스→**이진분류(하락/하락아님)** 전환 + **20마켓**으로 재확장(TRUMP 제외,
  거래대금 안정성 스크리닝 통과한 SHIB/SUI/SEI/NEAR/ETC/STX/HBAR 추가). walk-forward
  pooled weighted kappa **0.072→0.097**. **AWS 라이브 배포 완료**(macro F1 0.538).
  UI도 "다음 60봉 내 하락확률" 히어로 카드로 개편, 관리자 패널을 카드 안에 통합.
  상세: `[[upbit-v1-regime-ml-market-expansion-b]]` 메모리.
- **coinness.com 뉴스/bull-bear 감성 피처 실험** — 시도했으나 AWS WAF에 IP 차단당해
  중단. 재시도 안 하기로 결정.
- **① 과거 fact 라벨 기반 백테스트 성과 분석(Phase 1)** — SHIPPED 2026-08-30(88bf02b),
  `scripts/analyze_regime_fact_performance.py`. BTC/XLM `minutes60` 저장 거래
  1,344건을 진입 시점 fact 라벨(하락/하락아님, Triple Barrier)로 재분류: 하락 진입
  279건(승률 41.6%, 평균 -0.06%, 총기여 -17.7%) vs 하락아님 진입 1,065건(승률
  73.2%, 평균 +1.66%, 총기여 +1767.1%). 전략별 랭킹에서 "하락" 상위 10개 전략과
  "하락아님" 상위 10개 전략이 **완전히 다름(겹침 0개)**.
  **결론**: 장세별 전략 성과가 실제로 크게 갈린다 — ②(모델 성능 개선) 착수 근거로
  충분. 단, "하락" 라벨 자체가 "진입 후 60봉 내 실제로 하락 경계를 먼저 터치"라는
  정의라 이 구간 진입 거래의 저조한 성과는 어느 정도 동어반복적("하락 구간을
  피하면 이득"은 확인되지만, "하락 구간에서 다른 전략으로 갈아 끼우면 정확히
  얼마나 더 버는가"는 run당 표본이 5~11건으로 얕아 미확정). 정확한 기대수익
  크기가 필요해지면 그때 Phase 2(오라클 전환 백테스트)를 별도 세션에서
  브레인스토밍. 설계: `docs/superpowers/specs/2026-08-30-regime-fact-label-backtest-analysis-design.md`
- **선행 작업 — 코인별 fact 장세 구간 뷰어** — SHIPPED 2026-08-30(bb482a9), 2태스크
  플랜(백엔드 `/api/v1/regime/fact-segments` + 프론트 `/regime` 탭 카드). 코인별로
  fact 기준 "하락"/"하락아님" 구간을 캔들 색칠 차트+표로 직접 확인 가능, 표에서
  그리드서치 폼으로 구간 프리필 복사도 지원. `/analysis` 탭의 추세 구간 차트/표
  패턴 재사용.
- **② 모델 성능 개선 착수** — SHIPPED 2026-08-31(설계:
  `docs/superpowers/specs/2026-08-31-regime-ml-performance-improvement-design.md`,
  플랜: `docs/superpowers/plans/2026-08-31-regime-ml-performance-improvement.md`).
  `docs/ML_Regime_Switching_Additional_Improvements.md` 우선순위 1~4번을 순차
  ablation. **pooled weighted kappa 0.097(세션 시작 baseline) → 0.096(최종)**,
  macro F1 0.538→0.534. **결론: kappa 자체는 거의 안 움직였다** — 세션 내
  변동폭(0.092~0.097)이 대부분 실행마다 `TRAIN_END=datetime.now()`로 최신 데이터가
  조금씩 늘어나며 생기는 자연 변동 수준이라, 코드 변경의 순효과를 명확히 분리하기
  어려웠다.
  - **채택**: ①threshold 튜닝+확률보정(`engine/regime_ml_calibration.py`,
    `backend/regime_ml_service.py`), ②vol_t self-inclusion 버그 수정(`.shift(1)`,
    기술부채 항목 해결), ②sample uniqueness 가중치(AFML), ③베타중립
    cross-sectional 피처(`BETA_NEUTRAL_RETURN`/`CROSS_SECTIONAL_RANK`, 서빙까지 배선
    — 예측 시 20마켓 전체 로드로 지연시간 증가함).
  - **폐기(revert)**: ④캔들 결측구간 라벨 NaN 처리 — 20마켓 전체 결측 총량이
    0.1% 기준선은 넘었지만(0.2~0.4%), 실제 결측은 분기말 거래소 정기점검으로 보이는
    4~7시간짜리가 대부분이라 마스킹 코드의 심각도 임계값(n_bars*1.5=90시간)에 전혀
    못 미쳐 **실데이터에서 단 하나의 라벨도 마스킹하지 않았다**(revert 전/후 재학습
    결과가 소수점까지 완전히 동일 — kappa 0.092=0.092). 즉 이 항목의 "회귀"로 보였던
    -0.004는 실제로는 이 코드 때문이 아니라 데이터 drift로 보인다. 코드는 git
    히스토리(커밋 575dd37, revert b677072)에 남아있으니 나중에 더 큰 결측(정기점검
    아닌 실제 장애 등)이 확인되면 재검토.
  - **threshold 튜닝의 실효성 한계**: 목표 precision 55%가 실측상 달성 불가능
    (그리드 내 최고 precision이 threshold=0.70에서 39%대, recall 10.8%). 폴백
    로직이 threshold=0.90(precision 40%대, recall 0.3~0.6%)을 채택하는데, 사실상
    "하락" 경고를 거의 안 내는 수준이라 이 상태로는 실전에서 쓸모가 제한적임.
    `TARGET_DOWN_PRECISION`(현재 0.55, `scripts/train_regime_ml.py`)을 낮추거나,
    선택 로직에 최소 표본수 필터를 추가하는 게 다음 후보.
  - **남은 후보(다음 세션)**: (c-1) CUSUM 이벤트 샘플링(sample uniqueness가
    kappa를 거의 못 올려서 재고 대상), (c-2) 로지스틱회귀 baseline 비교 +
    LightGBM 하이퍼파라미터 튜닝(문서 우선순위 5번, 미착수), (c-3) 메타 레이블링
    (문서 우선순위 6번, 미착수), (c-4) threshold 튜닝 실효성 개선(위 참고).

## 다음 세션 작업 후보

### 우선순위 0 — "전역 공유 매크로 시계열이 왜 해로운가" 구조적 원인 — **조사 완료(2026-08-31)**

**방법**: `eta² = 피처값의 fold간 분산 / 전체 분산`(one-way ANOVA 방식)으로 "이
피처 하나만 보고 지금이 몇 번째 walk-forward fold인지 얼마나 잘 맞출 수 있는가"를
정량화(scratchpad 1회성 스크립트, 커밋 안 함). KRW-ETH 1개 마켓, 2024-01-01~현재,
5-fold 기준 실측.

**핵심 발견 — "shared macro" 가설은 틀렸다, 진짜 원인은 "레벨 비교값"이다**:

| 피처 | eta²(fold) | 판정 |
|---|---|---|
| `HOUR_SIN`/`DOW_SIN` | 0.0000 | 완전 안전 |
| `USDKRW_RETURN`(진짜 1스텝 수익률) | 0.0001 | 완전 안전 |
| `DAY_OF_MONTH_SIN` | 0.0024 | 안전 |
| 기존 유지 피처(`VPIN_SCORE` 0.003, `RAW_SCORE` 0.004, `VOLATILITY_PERCENTILE` 0.077) | 0.003~0.08 | 기준선(안전~경계) |
| `USDKRW_VOLATILITY`(EWM std) | 0.060 | 경계 |
| `UPBIT_FX_SPREAD` | 0.306 | 위험 |
| **`close_raw_level`(마켓 자기 자신의 원시가격 — 지금까지 피처로 쓴 적 없는 대조군)** | **0.479** | **위험(자기참조적인데도!)** |
| `usdkrw_rate_value`(원시레벨) | 0.576 | 위험 |
| `MONTH_SIN` | 0.613 | 위험 |
| `US_KR_RATE_SPREAD` | 0.679 | 위험 |
| `fed_funds_rate_value`(원시레벨) | 0.905 | 매우 위험 |
| `YIELD_CURVE_SPREAD`(원시 pass-through) | 0.910 | 매우 위험 |

**결론 1 — "전역 공유"가 원인이 아니다**: 대조군으로 넣은 `close_raw_level`(마켓
자기 자신의 종가, 절대 공유값 아님, 지금까지 피처로 쓴 적도 없음)이 eta²=0.479로
`UPBIT_FX_SPREAD`보다도 높게 나왔다. 진짜 원인은 "여러 마켓이 공유하느냐"가
아니라 **"레벨(절대 수준) 또는 레벨끼리의 비교값이냐"**다. 기존에 유지되고
있는 피처들이 우연히 전부 안전했던 이유는 "자기참조적이라서"가 아니라, 전부
비율/모멘텀/백분위 등 **레벨을 이미 제거한 형태**였기 때문이다.

**결론 2 — 실제 원인은 "2024~2026 표본 구간이 매크로 사이클 한 방향만 담고
있다"는 데이터 자체의 한계**: FRED 원자료를 직접 fold별로 까보면, 미국
기준금리(5.33→4.79→4.33→3.87→3.63), 한국 콜금리(3.52→3.29→2.68→2.53→2.53),
미 장단기금리차(-0.35→0.08→0.43→0.60→0.46), 원/달러(1353→1385→1413→1435→1480)
**전부 fold 경계와 거의 겹치지 않는 범위로 단조에 가깝게 한 방향 이동**했다
(2024~2026이 마침 금리인하 사이클+달러 약세 되돌림 구간과 겹침). 이 구간
안에서는 "금리가 낮다"와 "최근 fold다"가 통계적으로 거의 동의어라, 트리모델이
피처를 진짜 장세 신호가 아니라 **암묵적 캘린더로 악용**한다. `YIELD_CURVE_SPREAD`를
원시 그대로 넣은 것(pass-through, 실제로는 시간축 변환을 전혀 안 한 것과
같음)이 eta² 0.91로 최악이었던 것도 이 때문이다.

**결론 3 — "레벨→변화율/스프레드 변환"만으로는 부족하다, 어떤 변환인지가
중요하다**:
- **진짜 효과 있었던 변환**: `USDKRW_RETURN`(t 대 t-1 순수 수익률, eta²≈0) —
  차분(differencing) 자체는 통한다.
- **효과 없었던 "변환"**: `US_KR_RATE_SPREAD`/`YIELD_CURVE_SPREAD`는 시간축
  차분이 아니라 **같은 시점 두 레벨의 횡단면 차이**다 — 양쪽 다 같은 방향으로
  추세를 타면 차이(스프레드)도 그대로 추세를 물려받는다(실측: US_KR 스프레드도
  eta²=0.68로 원시레벨 못지않게 높음). "스프레드"라는 이름과 별개로, 시간축
  차분이 아니면 leakage 방지 효과가 없다.
- **추가 검증한 가설(백분위 정규화)도 절반만 통함**: 매크로 시계열을
  `VOLATILITY_PERCENTILE`처럼 "자기 과거 1년 대비 백분위"로 정규화해봤다.
  `usdkrw_rate_value`는 eta² 0.576→0.049로 크게 개선됐지만, `fed_funds_rate_value`는
  0.905→0.669로 거의 개선이 없었다. 이유: 백분위 정규화는 창(window) 안에서
  값이 오르내려야 효과가 있는데, 기준금리는 이 구간에서 계단식으로 한 방향만
  움직여서(국지적 되돌림이 사실상 없음) "최근 1년 대비 백분위"조차 fold와
  강하게 얽힌다 — **되돌림이 없는 순수 단조 추세는 어떤 정규화로도 못 없앤다.**
  원/달러는 추세는 있어도 국지적 등락(노이즈)이 더 커서 백분위 정규화가 통했다.

**액션 아이템**:
1. ~~캘린더 그룹 재검토~~ — **완료(2026-08-31, 같은 세션)**: `docs/superpowers/plans/2026-08-31-regime-ml-safe-signal-retry.md`로
   즉시 재시도해 성공. HOUR/DOW/DAY_OF_MONTH_SIN·COS(MONTH 제외) 채택
   +USDKRW_RETURN 채택(중립) — pooled weighted kappa **0.096→0.106**. 위
   "안전 신호 재시도" 절 참고.
2. ~~코스피/코스닥/S&P500/다우존스/나스닥 지수 피처(사용자 제안)~~ —
   **부분 완료(2026-08-31, 같은 세션)**: `docs/superpowers/plans/2026-08-31-regime-ml-stock-index-features.md`로
   S&P500/다우존스/나스닥종합 3개 수익률(`SP500_RETURN`/`DJIA_RETURN`/
   `NASDAQ_RETURN`) 즉시 재시도해 성공. 사전 예상("환율/금리 중간 위험도")과
   달리 eta²=0.0002로 완전 안전 판정, pooled weighted kappa **0.106→0.108**.
   위 "주가지수 수익률 피처 라운드" 절 참고. 코스피/코스닥은 FRED에 없어
   미도입 상태로 남음 — 필요해지면 yfinance 등 신규 데이터소스 재조사부터
   별도 세션.
3. **근본적 한계 인정**: 학습 구간이 최소 하나의 완전한 사이클(금리 인상+인하,
   환율 상승+하락 등)을 담을 만큼 길어지기 전까지는, 어떤 형태로 가공해도
   "단조 추세" 매크로 지표는 근본적으로 위험하다는 걸 인지하고 있을 것 —
   학습 구간(`TRAIN_START`)을 과거로 더 늘리는 것도 장기적 후보지만 별도
   브레인스토밍 필요(업비트 데이터 커버리지, 라벨링 방식 재검증 등 파급 큼).
4. PBO(Probability of Backtest Overfitting) 같은 정식 검증 프레임워크는 여전히
   다음 후보로 유효(`docs/ML_Regime_Switching_Additional_Improvements.md`
   1-3절, 별도 세션 리서치로도 재확인됨) — 이번 eta² 측정은 그 정식 버전 대신
   빠르게 쓴 근사적 진단이다.

### 즉시 결정 필요 — AWS 라이브 배포 여부

로컬 재학습 모델(pooled weighted kappa 0.108, 캘린더3+환율1+주가지수3 신호
추가)이 현재 라이브 배포 모델(kappa 0.097, 20마켓/이진분류)보다 낫다. 배포하려면
`[[upbit-v1-deploy-check-open-positions-first]]` 원칙대로 **배포 전 AWS
서버 `trading.db`의 오픈 포지션부터 확인**할 것 — `deploy/update.sh`는
daemon도 재시작하므로 실거래 감시가 몇 초 끊긴다.

**코드/모델 원자성 경고(2026-08-31 최종 리뷰 추가)**: `engine/regime_ml_features.py`의
`build_feature_matrix`는 오프라인 학습기(`scripts/train_regime_ml.py`)와 라이브
서버(`backend/regime_ml_service.py`) 양쪽이 공유한다. 이 branch의 **코드**만
`deploy/update.sh`로 AWS에 배포하고, 새 피처(캘린더/환율/주가지수)로 **학습된
모델**을 `scripts/push_regime_ml_model.sh`로 함께 올리지 않으면, 코드가 만들어내는
피처 개수와 라이브에 이미 떠 있는 구모델이 기대하는 피처 개수가 어긋나
`/api/v1/regime/ml-current-prediction`가 전 마켓에서 HTTP 500을 반환하기
시작한다. 두 스크립트는 별개다 — **`deploy/update.sh`는 코드만, `scripts/
push_regime_ml_model.sh`는 모델만** 배포한다. 코드와 모델은 반드시 같은
유지보수 창(maintenance window) 안에서 함께 배포할 것 — 순서를 둘 중 하나로
고정하기보다(과거 "모델을 코드보다 먼저 배포하지 말 것"[[upbit-v1-regime-ml-backlog-cleanup]]
교훈도 있었으니 임의로 순서를 바꾸지 말고), 코드 배포와 모델 배포 사이 간격을
최대한 좁혀 그 사이에 불일치 상태로 트래픽을 받는 시간을 없앨 것.
지금 `main`에는 캘린더/환율 라운드+이번 주가지수 라운드가 전부 아직 미배포
상태로 쌓여 있어, 이후 다른 이유로 `update.sh`를 돌리는 어떤 세션이든 실수로
코드만 배포하고 모델을 빠뜨릴 위험이 있다 — **어떤 배포든 직후에 `/regime`
탭에서 최소 1개 마켓의 ML 예측이 500이 아니라 200으로 정상 응답하는지
확인**할 것.

### 우선순위 1(다음 착수 후보) — c-2 로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝

`docs/regime-ml-backlog.md`의 잔여 후보 (c) 중 미착수 항목. 우선순위 0의
남은 항목(코스피/코스닥/S&P500 등 지수 피처, PBO 프레임워크)과 반드시
순서를 지킬 필요는 없음(서로 다른 축) — 사용자 판단에 따라 먼저 진행해도 무방.

### 우선순위 2 — ③ 실시간 자동 장세 대응 개발

**전제 조건 재검토 필요**: 원래 "①②가 검증된 뒤에만 착수" 조건이었는데, ②
결과가 "구조는 더 올바르게 개선됐지만 kappa 자체(0.097→0.096)는 사실상 정체"라
애매하다. ①(fact 라벨 백테스트)의 "장세 조건부 전략 전환이 유의미하다"는 결론은
여전히 유효하지만, "지금 모델의 예측력으로 실시간 자동전환까지 해도 되는가"는
별개 질문 — 다음 세션에서 ③ 착수 전에 이 판단부터 다시 짚어야 함(사용자 논의
필요, 이 백로그가 임의로 정하지 않음). 위 "남은 후보(c-1~c-4)"로 kappa를 더
끌어올린 뒤 착수하는 안도 고려 가능.

**왜 원래 마지막이었나**: 이건 **실계좌 자동매매에 새 자동화 레이어를 얹는 것**이라
검증 안 된 신호로 실거래를 자동으로 바꿔치기하는 위험한 구조가 될 수 있다. 또한
엔지니어링 난이도도 가장 높음 — daemon이 ML 예측을 주기적으로 폴링하면서 살아있는
라이브 전략을 무중단으로 다뤄야 하고, 교체 시점에 열린 포지션이 있으면 어떻게
처리할지 등 별도 설계가 필요함. 브레인스토밍부터 새로 시작해야 하는 규모의 작업.

**사용자 결정사항(2026-08-30)**: 하락 국면에서 "다른 전략으로 전환"할지 "아예
거래를 일시정지"할지는 이 백로그가 미리 정할 문제가 아니라, 실제 라이브 운영
시점에 사용자가 직접 고르는 선택지로 둔다 — ③ 설계 시 두 옵션을 모두 지원하는
쪽으로 브레인스토밍(단일 방식으로 미리 못박지 않음).

### 별도 — ④ 세그먼트(추세기반) 탭 제거 여부

①②③과 독립적인 스코프 판단 질문이라 우선순위 매기기보다 **먼저 결정만
내리면 되는 항목**. 판단에 필요한 정보: `/analysis`(세그먼트) 탭은 ZigZag
스윙 기반 추세 구간 분류(`engine/trend_segments.py`)로, 장세 판별 ML과는
완전히 다른 알고리즘(둘 다 "상승/하락/횡보"를 다루지만 방법론 무관)이고,
Grid Search 탭에 "세그먼트 패턴 셀 클릭 시 프리필 복사" 연동 기능이 있음
(`[[upbit-v1-trend-segment-grid-search-copy]]`, 2026-08-17 SHIPPED). 제거하면
이 프리필 연동도 같이 없어짐 — 세그먼트 자체를 안 쓴다면 문제없지만, Grid
Search에서 그 프리필 기능을 계속 쓰고 있다면 대체 경로가 필요함. 다음
세션에서 이 프리필 기능을 실제로 쓰고 있는지부터 확인하고 결정.

## 잔여 기술부채 (C, 급하지 않음 — ①②③ 작업 중 같은 파일 만질 때 묶어서 처리 권장)

- `backend/regime_ml_service.py:find_latest_model()`이 사이드카 JSON 파싱 실패
  (손상된 파일)에 대한 예외처리 없음 — 여전히 미해결(2026-08-30 코드 확인)
- "모델 디렉터리는 있는데 파일 0개"인 케이스 테스트 없음 — 여전히 없음
- `ml-current-prediction`의 `RuntimeError`→500 응답 경로 테스트 없음
- 워크포워드 fold의 embargo가 이론상 `n_bars`가 아니라 `n_bars+1`이어야 정확함
  (`engine/regime_ml_splits.py`) — 실측상 무해함 확인됐으나 미수정
- ~~Triple Barrier의 `vol_t`가 자기 자신의 수익률을 포함하는 문제~~ — 2026-08-31
  ②(모델 성능 개선)에서 `.shift(1)`로 수정·채택 완료(`engine/regime_ml_labels.py`).
  실측 kappa는 변화 없었지만(0.093→0.093) 라벨링 정합성 버그라 유지.
- FRED 관련 fetch가 재시도 소진 후 실패하면 `RuntimeError`가 그대로 전파돼(NaN
  폴백 없음) `/regime` ML 예측 전체가 죽는다 — 이번 라운드(주가지수 3개 추가)로
  이 의존성이 4개→7개로 늘었다. 스테일 캐시 폴백 추가는 향후 후보.

## 계속 범위 밖 (재논의 필요 시에만)

- ML 과거 임의 시점 정확도 리포트/confusion matrix 조회(현재는 "현재 시점
  성능"만 A2로 제공됨, 과거 임의 구간 조회는 별개 기능)
- 1시간봉 외 타임프레임 미지원(타임프레임별 재학습 필요)
- 신경망(PyTorch 등) 전환 — 검토 후 보류 확정(GPU는 학습속도만 개선, 신호
  자체가 약한 문제라 모델 교체로 해결 안 될 가능성 높음)
