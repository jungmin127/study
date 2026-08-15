# 로컬 → AWS 백테스트 결과 이전 스크립트 — Design Spec

## 배경 및 목표

사용자는 무거운 grid search(9-오실레이터 전 교차, 20,700개 조합)를 AWS
`t4g.small`(2 vCPU, 버스터블 인스턴스) 위에서 돌리면 CPU 크레딧을 소모해 소액의
추가 과금이 생길 수 있다는 점을 확인했다([[upbit-v1-aws-server-live-and-budget-followup]]
참고). 이를 피하려면 grid search 자체는 로컬 PC에서 돌리고, 그중 실거래로 연결할
결과만 AWS 서버로 옮기는 편이 낫다.

로컬과 AWS는 완전히 분리된 SQLite 파일(`data/backtest_results.db`)을 각자 갖고
있다(`engine/cache.py`의 스펙 결정4). 로컬에서 만든 백테스트 결과가 AWS 프론트엔드의
백테스트 목록/상세 페이지에 나타나야, 거기 있는 "실거래 전환"(`GoLiveButton` →
`POST /api/v1/live-strategies`)으로 실제 라이브 전략을 만들 수 있다. 이 스펙은 그
전 단계, 즉 **로컬의 백테스트 결과 DB를 AWS 서버의 DB로 안전하게 병합**하는
스크립트를 다룬다.

브레인스토밍에서 확정한 조건:
- 옮기는 범위: 로컬 `data/backtest_results.db` 전체(선택적 run_id 지정 없음).
- 반복 사용: 앞으로도 로컬에서 grid search를 새로 돌릴 때마다 재실행한다 —
  이미 서버에 있는 결과를 다시 보내도 안전해야 한다(멱등성).
- 실행 방식: 로컬 PC에서 명령어 하나만 실행하면 scp 전송부터 서버 쪽 병합까지
  전부 끝나야 한다(중간에 서버 SSH 세션으로 갈아타지 않음).

## 범위

**이 스펙에서 다루는 것:**
- 로컬 → AWS 방향의 단방향 DB 병합 스크립트 2개(로컬용 셸 스크립트 + 서버용
  파이썬 스크립트)
- 접속 정보(SSH 키 경로/서버 주소)를 로컬 `.env`에 한 번만 설정해두는 방식
- `run_id` 중복을 건너뛰는 멱등적 병합 로직과 그 pytest 테스트

**이 스펙에서 다루지 않는 것:**
- AWS → 로컬 방향(역방향) 동기화 — 필요해지면 별도 스펙으로 다룬다.
- `grid_search_jobs`(그리드서치 탭의 "요청 이력" 테이블) 이전 — 실거래 전환에
  필요한 건 `backtest_runs`/`backtest_results`뿐이라 범위에서 제외한다. AWS
  프론트엔드의 그리드서치 탭 "요청 이력"에는 로컬에서 실행한 job이 나타나지
  않는다(의도된 제약).
- "실거래 전환" 기능 자체 — 이미 구현되어 있다(`GoLiveButton`,
  `POST /api/v1/live-strategies`, [[upbit-v1-live-trading-approval-control-ux]]).
  이 스펙은 그 기능이 참조할 백테스트 결과를 AWS 쪽에 보이게 만드는 것까지만
  다룬다.
- 캔들 데이터 캐시(`data/cache/ohlcv/*.parquet`) 이전 — 백테스트 결과(equity
  curve, trades)는 이미 DB에 JSON으로 저장돼 있어 조회 시 캔들을 다시 조회할
  필요가 없다.

## 핵심 결정

### 결정 1 — 병합 단위는 `backtest_runs`/`backtest_results` 두 테이블, 키는 `run_id`

`engine/cache.py`의 `run_id`는 `compute_cache_key()`가 만드는 내용 기반 해시(전략
소스+파라미터+마켓+타임프레임+기간+리스크설정을 sha256)다. 즉 같은 조건으로 로컬에서
grid search를 다시 돌려도 항상 같은 `run_id`가 나온다. 이 성질을 그대로 활용해
`INSERT OR IGNORE`로 병합하면, 이미 서버에 있는 `run_id`는 자연스럽게 건너뛰고
새 `run_id`만 추가된다 — 별도의 dedup 로직 없이 "반복 실행해도 안전"을 만족한다.

`backtest_results.run_id`가 `backtest_runs.id`를 참조하는 1:1 관계이므로, 두 테이블
모두 같은 방식(`INSERT OR IGNORE ... SELECT * FROM 원본테이블`)으로 병합한다.

### 결정 2 — 로컬 스크립트가 scp + ssh를 한 번에 실행(2단계 수동 대신)

`scripts/push_backtest_results.sh`(로컬, Git Bash에서 실행) 하나가 전 과정을
담당한다:

1. 로컬 `data/backtest_results.db`가 존재하는지 확인(없으면 "옮길 결과가 없습니다"
   류의 에러로 중단).
2. `scp`로 이 파일을 서버의 `data/_incoming_backtest_results.db`에 올린다(원래
   파일명과 겹치지 않는 접두어 `_incoming_`을 붙여, 실수로 서버 쪽 실제 DB를
   덮어쓰는 사고를 원천적으로 피한다).
3. `ssh`로 서버에 접속해 `.venv/bin/python scripts/import_backtest_results.py
   data/_incoming_backtest_results.db`를 실행시킨다.
4. 병합이 성공적으로 끝나면 서버 쪽 임시 파일을 정리한다(실패 시에는 지우지 않는다 —
   자세한 근거는 아래 "에러 처리" 절 참고).

`deploy/update.sh`와 동일하게 `set -euo pipefail`로 중간 실패 시 즉시 중단한다.

### 결정 3 — 접속 정보는 로컬 `.env`에 저장(반복 실행 편의성)

`.env`는 이미 `.gitignore`에 등록돼 있고 `UPBIT_ACCESS_KEY` 등 로컬 전용 설정을
두는 관례가 있다. 여기에 두 값을 추가한다:

```
DEPLOY_SSH_KEY_PATH=/c/Users/jungm/Downloads/upbit-server-key.pem
DEPLOY_SERVER_HOST=ubuntu@upbit-server.tailXXXX.ts.net
```

- `DEPLOY_SERVER_HOST`는 Tailscale MagicDNS 주소를 권장한다(탄력적 IP를 직접 써도
  되지만, MagicDNS 쪽이 이 프로젝트의 기존 원칙 — 8000/3000 포트는 Tailscale로만
  접근 — 과 결이 맞고, SSH 자체는 보안 그룹에서 이미 전체 공개라 22번 포트는 둘 다
  동일하게 동작한다).
- 두 값이 없으면 `push_backtest_results.sh`가 `deploy/UPDATE.md`를 가리키는
  안내와 함께 중단한다(어디서 값을 구하는지 이미 그 문서에 설명돼 있음).
- 원격 앱 경로(`/opt/study/260711-upbit-v1`)는 `deploy/update.sh`처럼 스크립트에
  상수로 고정한다 — 배포 경로가 바뀔 일이 없어 설정으로 뺄 필요가 없다(YAGNI).

`.env.example`에는 두 키를 주석 처리된 예시로 추가해 존재를 알린다(실제 값은
비워둔 채로, 다른 시크릿과 동일한 패턴).

### 결정 4 — 서버 쪽 병합은 파이썬(`engine.cache._connect()` 재사용), sqlite3 CLI 의존 없음

서버(Ubuntu)에는 `sqlite3` CLI가 기본 설치돼 있지 않다(`deploy/setup.sh`의 apt
설치 목록에 없음). 반면 프로젝트 venv에는 이미 `engine/cache.py`가 있고, 그 안의
`_connect()`가 스키마 생성/마이그레이션까지 처리한다. 새 CLI 의존성을 추가하는 대신
`scripts/import_backtest_results.py`가 `engine.cache._connect()`로 대상 DB에 연결하고,
`ATTACH DATABASE`로 넘어온 파일을 붙여 두 테이블을 병합한다.

```python
# 개략적 로직 (실제 구현 시 세부 조정 가능)
conn = engine.cache._connect()          # data/backtest_results.db, 스키마 보장됨
conn.execute("ATTACH DATABASE ? AS incoming", (incoming_path,))
conn.execute("INSERT OR IGNORE INTO backtest_runs SELECT * FROM incoming.backtest_runs")
conn.execute("INSERT OR IGNORE INTO backtest_results SELECT * FROM incoming.backtest_results")
conn.commit()
```

`sqlite3.connect(..., timeout=10)`(또는 `_connect()`에 이미 있다면 그대로 재사용)로
짧은 busy timeout을 둬서, 마침 backend/daemon이 같은 DB에 쓰기 작업 중인 순간과
겹쳐도 즉시 실패하지 않고 잠깐 대기 후 재시도되게 한다.

병합 후 삽입된 행 수/건너뛴 행 수를 표준출력에 한글로 요약 출력한다(예: "신규
12건 추가, 기존 8건 건너뜀") — `push_backtest_results.sh`가 이 출력을 그대로
사용자에게 보여준다. 병합이 끝나면 스크립트가 자신에게 넘어온 임시 입력 파일
(`data/_incoming_backtest_results.db`)을 스스로 삭제한다.

## 변경 파일

- `scripts/push_backtest_results.sh`(신규) — 로컬 실행 진입점.
- `scripts/import_backtest_results.py`(신규) — 서버에서 실행되는 병합 로직.
- `tests/test_import_backtest_results.py`(신규) — 병합 로직 단위 테스트.
- `.env.example`(수정) — `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST` 예시 추가.
- `deploy/UPDATE.md`(수정) — "코드 업데이트" 절차 옆에 "로컬 백테스트 결과
  가져오기" 절차를 짧은 절로 추가(같은 SSH 설정을 재사용하므로 문서를 나눌
  필요 없이 이어 붙인다).

## 에러 처리

- 로컬에 `data/backtest_results.db`가 없으면 즉시 중단, 명확한 한글 에러.
- `.env`에 `DEPLOY_SSH_KEY_PATH`/`DEPLOY_SERVER_HOST`가 없으면 즉시 중단, 어디서
  값을 채우는지 안내(`deploy/UPDATE.md` 참고 문구 포함).
- `scp`/`ssh` 실패(네트워크 끊김, 서버 미기동, 키 경로 오타 등)는 `set -euo
  pipefail`이 그대로 중단시키고, bash/ssh의 원래 에러 메시지가 사용자에게
  보인다 — 별도 래핑 없이 그대로 노출하는 편이 실제 원인(권한/네트워크/호스트명)
  파악에 더 유리하다.
- 서버 쪽 병합 스크립트가 예외로 죽으면(예: 손상된 DB 파일) 0이 아닌 종료 코드로
  끝나고, `ssh`가 그 종료 코드를 그대로 로컬로 전달해 `push_backtest_results.sh`도
  실패로 끝난다 — 이 경우 임시 파일은 정리되지 않고 남아 다음 시도 시 참고/재사용
  가능하게 둔다(디버깅 편의, 실패 케이스에서는 자동 삭제하지 않음).

## 테스트 전략

- `tests/test_import_backtest_results.py`: `tmp_path`에 "서버 DB"와 "들어오는 DB"
  두 개의 SQLite 파일을 만들어 병합 함수를 직접 호출하고 다음을 검증한다.
  - 신규 `run_id`는 두 테이블 모두에 삽입된다.
  - 이미 존재하는 `run_id`는 건너뛴다(행 수 변화 없음, 기존 값이 덮어써지지
    않음).
  - `backtest_runs`에는 있지만 `backtest_results`가 없는(비정상) 행도 에러 없이
    처리된다.
  - 삽입/건너뜀 건수 카운트가 정확히 반환된다.
- `scripts/push_backtest_results.sh`의 scp/ssh 부분은 실제 네트워크와 실제 서버가
  필요해 자동화 테스트 대상에서 제외한다 — 구현 후 실제 AWS 서버로 1회 수동
  검증(로컬에서 실행 → 서버 `data/backtest_results.db`에 새 행이 늘었는지,
  AWS 프론트엔드 백테스트 목록에 해당 결과가 보이는지 확인)한다.

## 자기 검토(스펙 완성도)

- **플레이스홀더 없음** — SSH 키/서버 주소는 사용자별로 다를 수밖에 없어
  `.env` 값으로 명시했고, 나머지(원격 경로, 임시 파일명, 병합 SQL)는 전부
  구체적으로 정했다.
- **내부 정합성**: 결정1(내용 기반 `run_id`로 멱등성 확보)과 결정4(`INSERT OR
  IGNORE` 병합)가 서로를 전제로 한다 — `run_id`가 내용 기반이 아니었다면 이
  단순한 병합 방식은 성립하지 않았을 것이므로, 이 전제를 결정1에 명시했다.
- **범위 경계 재확인**: `grid_search_jobs`/역방향 동기화/캔들 캐시 이전을
  명시적으로 범위 밖에 두어, 구현 중 범위가 슬금슬금 넓어지는 것을 막았다.
- **하위호환 확인**: 기존 `engine/cache.py`의 스키마/함수는 전혀 수정하지 않고
  `_connect()`만 재사용하므로, 기존 백테스트/그리드서치 흐름에 영향이 없다.
- **가장 리스크가 큰 항목 식별**: 결정2의 3단계(서버 SSH 실행)가 실패하는
  시나리오(서버가 꺼져있거나 daemon이 DB를 길게 잠그고 있는 경우)가 가장 흔한
  실패 지점이다 — 에러 처리 절에서 이 경우 임시 파일을 남겨 재시도 시 참고할 수
  있게 한 것이 그 대비다.
