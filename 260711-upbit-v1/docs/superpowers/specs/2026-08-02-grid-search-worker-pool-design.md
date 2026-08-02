# Grid Search 워커 풀 설계 — 메모리 누적 크래시 방지 + 병렬화

## 배경 및 목적

`scripts/grid_search.py`의 `compute_grid_results()`는 조합마다 `engine/runner.py::run_backtest()`를
순차 호출하며, 매 호출마다 `backtrader.Cerebro()`를 새로 인스턴스화한다. backtrader가 반복
인스턴스화 시 내부 참조를 계속 쌓아 메모리를 선형으로 누적하는 것으로 확인됐다
([[upbit-v1-runner-memory-leak]] 참고 — 9,000회 호출당 RSS +183MB, 1시간봉/캔들 ~1,460개
기준 실측). 9-오실레이터 확장으로 전체 조합 수가 20,700개까지 늘면서, 이전 세션에서 매번
전체의 36~40% 지점(약 7,500~8,300회 호출)에서 트레이스백 없이 프로세스가 죽는 문제를
3회 재현했다.

이 설계는 다음 두 가지를 달성한다:
1. **크래시 방지**: 워커 프로세스를 주기적으로 재시작해 누적 메모리를 OS에 반환, 전체
   20,700개 조합을 끝까지 완주할 수 있게 한다.
2. **속도 개선(부가)**: 워커를 병렬로 여러 개 띄워 전체 소요 시간을 단축한다.

대상 사용자 PC 스펙: Intel i5-12400F (물리 코어 6 / 스레드 12), RAM 16GB(백엔드+프론트엔드
개발서버가 이미 상시 구동 중인 상태를 전제로 함).

## 스코프

- **포함**: `scripts/grid_search.py`(`compute_grid_results()` 재작성, 워밍업 사전 체크 추가),
  `.claude/skills/grid-search/SKILL.md` 문구 업데이트, `tests/test_grid_search.py` 테스트 추가.
- **제외**: `engine/runner.py`(수정 없음, 순수 재사용), `engine/condition_tree.py`(수정 없음,
  `max_required_period` 재사용), 다른 `run_backtest` 호출부(`backend/main.py`는 요청당 1회만
  호출하므로 이 버그의 실질적 영향권 밖 — [[upbit-v1-runner-memory-leak]] 참고).
  `scripts/run_eda_sweep.py`는 애초에 `run_backtest`를 호출하지 않아 대상이 아니다.

## 아키텍처

### 워커 풀

`compute_grid_results()`의 순차 `for` 루프를 다음으로 교체한다:

```python
pool = multiprocessing.Pool(
    processes=4,
    maxtasksperchild=K,  # 캘리브레이션으로 확정, 아래 참고
    initializer=_init_worker,
    initargs=(df, risk_config),
)
```

- `df`/`risk_config`는 워커 시작 시 `initializer`로 한 번만 전달해 워커 전역 변수에 저장한다
  (태스크마다 재직렬화하지 않음).
- 새 모듈 최상위 함수 `_run_one_combo(buy_block, sell_block)`를 추가한다. Windows는
  `multiprocessing`이 `spawn` 방식이라, 워커에 전달되는 함수/클래스는 반드시 모듈 최상위에
  있어야 pickle이 가능하다(중첩 함수/람다 불가) — 기존 `run_backtest`/`ConditionTreeStrategy`는
  이미 모듈 최상위이므로 문제없음.
- `main()`은 이미 `if __name__ == "__main__":` 가드 안에서 호출되므로 `spawn` 방식과 호환된다.

### 태스크 제출 방식: `apply_async` + 폴링 (워치독을 위해 `imap_unordered` 대신 채택)

`imap_unordered`는 특정 워커가 죽어 결과가 영영 안 돌아오는 경우를 감지할 방법이 없어
무한 대기로 이어질 수 있다. 대신:

```python
async_results = [pool.apply_async(_run_one_combo, (b, s)) for b, s in combos]
```

으로 전체(20,700개, 작은 dict라 메모리 부담 없음)를 한 번에 제출하고, 폴링 루프로 소비한다.

### 진행률 로그

병렬 실행이라 완료 순서가 매수조건 인덱스 순서와 무관해지므로, 로그를 "완료 개수 기준"으로
바꾼다:

```
완료 1,000/20,700건 (4.8%)
완료 2,000/20,700건 (9.7%)
...
```

`RESULT_JSON`의 구조(`total_combos`/`elapsed_sec`/`saved`)는 그대로 유지 — 스킬의 최종 보고
로직은 변경 없음.

### 워밍업 사전 체크 (스코프에 포함, 별개 버그)

그리드에 등장하는 파라미터 조합이 필요로 하는 최대 워밍업 봉 수(예: `MACD_PPO`는
fast/slow/signal 합산 최대 ~44봉)보다 캔들 수가 적으면 backtrader 내부에서 `IndexError`로
불명확하게 죽는 별개 문제도 이번에 같이 고친다. 새 함수를 만들지 않고 기존
`engine/condition_tree.py::max_required_period(group)`를 재사용한다
(`backend/main.py`가 실제 백테스트 실행 시 이미 같은 패턴을 씀):

```python
all_buy_group = {"type": "AND", "conditions": buy_conditions}
all_sell_group = {"type": "AND", "conditions": sell_conditions}
required_bars = max(max_required_period(all_buy_group), max_required_period(all_sell_group))
if len(df) < required_bars:
    raise SystemExit(
        f"선택된 그리드가 최소 {required_bars}개의 봉을 필요로 하지만, "
        f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리세요."
    )
```

`build_condition_grid()` 직후, `compute_grid_results()` 호출 전에 실행한다.

## K(재시작 주기) 캘리브레이션

구현 단계에서 이 PC로 실측용 스크립트를 한 번 돌려 확정하고, 결과값을 상수로 스크립트에
박아넣는다(매 실행마다 재측정하지 않음). 캘리브레이션 스크립트 자체는 저장소에 커밋하지 않고
세션 scratchpad에만 둔다(기존 그리드서치 설계 때 실험 스크립트들과 동일한 관례).

- **측정 시나리오**: 가장 무거운 현실적 케이스(1시간봉, 다개월치 — 지난 크래시 재현 조건과
  유사한 캔들 수)로 측정한다. 오늘 사용한 일봉처럼 캔들이 적은 요청에도 이 값은 항상 안전한
  방향으로 작동한다(재시작이 필요 이상으로 잦아질 뿐, 위험한 방향으로 어긋나지 않음).
- **역산 공식**: `K ≤ (워커당 허용 메모리 여유분) ÷ (호출당 RSS 증가량, 실측치)`. 워커 4개가
  16GB RAM을 나눠 쓰는 것과 이미 상시 구동 중인 백엔드/프론트엔드 개발서버, OS 오버헤드를
  감안해 워커당 예산을 정하고 안전마진을 곱한다.
- K를 지나치게 작게 잡으면 재시작 오버헤드가 늘어 병렬화로 얻는 속도 이득이 줄어드는
  트레이드오프가 있다 — 캘리브레이션 시 함께 고려한다.

## 에러 처리: 워치독

`apply_async` 폴링 루프에서 함께 구현한다.

- 1~2초 간격으로 각 `AsyncResult.ready()`를 확인한다. 새로 완료된 게 있으면 진행률을 갱신하고
  "마지막 진행 시각"을 기록한다.
- **마지막 진행 이후 5분간 아무 결과도 안 들어오면** 워커가 죽어서 멈춘 것으로 판단해
  `pool.terminate()`를 호출하고 명확한 에러 메시지로 중단한다:
  `"워커 응답 없음 — 5분간 진행 없어 중단합니다. 일부 워커가 예기치 않게 종료됐을 수 있습니다."`
  타임아웃 판정 자체(`마지막 진행 시각, 현재 시각, 타임아웃` → bool)는 순수 함수로 분리해
  유닛 테스트 가능하게 한다.
- 5분은 넉넉한 기본값이다 — 조합 하나 계산은 보통 1초 미만~몇 초이므로, 워커 4개 중 하나라도
  살아있으면 5분 안에 뭔가는 끝나는 게 정상이다.
- 워치독 발동 시 그 시점까지 완료된 결과는 버리고 **전체 실패로 처리**한다(부분 결과 저장은
  스코프 밖 — 지금 크래시 나도 아무것도 저장 안 되는 것과 동일한 실패 시맨틱을 유지).
- 워커 내부에서 `run_backtest()`가 즉시 예외를 던지는 경우(hang이 아닌 일반 에러)는
  `AsyncResult.get()`이 그 예외를 그대로 재발생시켜 기존과 동일하게 fail-fast로 죽는다 —
  별도 처리 불필요.

**잔여 리스크**: 이 설계는 크래시를 "매우 높은 확률로" 방지하지만 수학적으로 100% 보장하지는
않는다. K는 캘리브레이션 시점의 여유 RAM을 전제로 한 안전마진이라, 그 전제가 깨지면(그리드
서치 도중 다른 무거운 프로그램 구동, 훗날 지금보다 훨씬 큰 그리드 실행 등) 마진이 잠식될 수
있다. 워치독은 이 경우에도 "응답 없이 무한 대기"를 "5분 내 명확한 에러"로 바꿔주지만, 크래시
자체를 막지는 못한다.

## 테스트

- `build_condition_grid`/`dedup_top_results`: 순수 함수, 변경 없음 — 기존 테스트 유효.
- `_run_one_combo(buy_block, sell_block)`: Pool 없이 직접 호출해 반환 dict 모양
  (`return_pct`/`buy_block`/`sell_block`/`trades`/`final_value`)을 검증하는 유닛 테스트 추가.
- 워밍업 사전 체크: 캔들 부족 시 명확한 에러로 중단 / 충분하면 통과 — 유닛 테스트 추가.
- 워치독 타임아웃 판정 함수: 순수 함수로 유닛 테스트.
- **실제 Pool 병렬 실행·워커 재시작·워치독 트리거는 자동 테스트 대신 수동 스모크 테스트로
  검증**한다(멀티프로세싱 내부를 mock하는 건 깨지기 쉬움 — 기존 grid search 구현 때도 동일한
  관례를 따름).

## 문서 업데이트 (`SKILL.md`)

- "예상 소요 시간" 문구를 병렬화 반영해 업데이트(정확한 배수는 구현 후 실측치로 채움 — 이론상
  최대 4배지만 직렬화/재시작 오버헤드로 실제로는 3배 안팎 예상).
- 실행 절차에 "워커 4개로 병렬 실행되며, 5분간 진행이 없으면 자동 중단됩니다" 안내를 추가하고,
  실패 시 에러 메시지를 사용자에게 그대로 전달하도록 안내한다.

## 알려진 트레이드오프 (사용자 승인됨)

- 소규모 그리드(캔들 수가 적어 원래도 빨리 끝나는 경우, 예: 이번 세션의 일봉 그리드서치)에서는
  워커 4개를 띄우는 고정 비용(인터프리터 기동 + 모듈 재임포트 4회) 때문에 병렬화 이득이
  미미하거나 거의 없을 수 있다. 느려지지는 않지만 크게 빨라지지도 않는 구간이 존재한다.
- 워치독 타임아웃(5분) 발동 시 부분 결과는 저장하지 않고 전체 실패로 처리한다.
