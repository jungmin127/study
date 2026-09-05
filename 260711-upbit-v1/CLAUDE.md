# 프로젝트 워크플로우 규칙

## 스펙/플랜 저장 위치 (2026-09-05부터)

`superpowers:writing-plans`/`superpowers:brainstorming` 등이 스펙·플랜 파일을 저장할
때, 기본 경로(`docs/superpowers/specs/`, `docs/superpowers/plans/`) 대신 다음을 쓴다:

- 새 스펙 → `docs/superpowers/specs_v2/`
- 새 플랜 → `docs/superpowers/plans_v2/`

2026-09-05 장세 판별 레거시 삭제까지의 기존 문서는 전부 `specs_v1/`, `plans_v1/`로
옮겨져 있다(파일 개수가 너무 많아져 v1/v2로 분리). `specs_v1/`, `plans_v1/`에는 더 이상
새 파일을 추가하지 않는다.

## 이해를 돕는 참고 문서

특정 기능 구현을 위한 설계가 아니라 배경지식/가이드 성격의 md 문서는
`docs/superpowers/references/`에 모은다(`docs/` 최상위에 흩어두지 않는다).

## 작업 방식

- 워크트리를 쓰지 않고 항상 `main`에서 직접 작업한다. 완료 후 병합 방식을 묻지 않고
  커밋 + `git push`까지 진행한다.
- `deploy/update.sh` 실행 전에는 항상 AWS 서버 `trading.db`에 오픈 포지션이 있는지
  먼저 확인한다(daemon도 함께 재시작되어 실시간 손절/익절 감시가 잠시 끊기기 때문).
