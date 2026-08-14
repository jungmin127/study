# 4단계 — 상시 서버 배포 — Design Spec

## 배경 및 목표

[[upbit-v1-live-trading-roadmap-sequencing]] 1단계(트레이딩 엔진 + 핵심 안전장치)가
완결됐다([[upbit-v1-live-trading-qa-audit-fixes]]). 지금은 `trading/daemon.py`가 사용자의
개인 PC에서만 돌아가, PC를 끄거나 잠들면 자동매매(손절/익절 감시 포함)도 같이 멈춘다.
4단계의 목표는 daemon을 24/7 켜져 있는 별도 호스트로 옮겨서, PC 전원과 무관하게 계속
동작하게 만드는 것이다. 원본 로드맵 스펙(`2026-08-04-live-trading-foundation-design.md`)
표에서 "4. 운영 하드닝"으로 정의된 항목이며, 이번 스펙에서 세부 구현을 확정한다.

사용자는 서버/클라우드 경험이 거의 없어, 이번 브레인스토밍은 개념 설명과 함께 단계적으로
결정했다. 최종 결정: **Oracle Cloud 영구무료 티어(Ampere A1, Ubuntu) + systemd(Docker
아님) + Tailscale 사설망**.

## 범위

**이 스펙에서 다루는 것:**
- Oracle Cloud VM 프로비저닝 방식 결정(인스턴스 종류)
- daemon/backend/frontend를 systemd 서비스로 상시 구동하는 방법
- Tailscale을 통한 원격(핸드폰/노트북) 접속 보안
- 배포/업데이트를 재사용 가능한 스크립트로 만들어 다른 클라우드(AWS 등)로 이식하기
  쉽게 하는 것
- 서버 이전 시 필요한 코드 설정값 변경(CORS origin)

**이 스펙에서 다루지 않는 것:**
- 2단계(텔레그램 알림/제어), 3단계(분석 대시보드) — 4단계 이후로 후순위, 로드맵 순서
  그대로
- HTTPS 인증서/공개 도메인 — Tailscale 사설망으로만 접속하므로 불필요
- 다중 서버/로드밸런싱/오토스케일링 — 단일 사용자, 단일 VM으로 충분
- 업타임 모니터링·장애 알림(예: UptimeRobot, 텔레그램 알림) — 2단계 범위
- 기존 로컬 `data/trading.db`의 서버 이전 — 서버는 새 빈 DB로 시작(아래 결정6)
- `trading/*.py`의 트레이딩 로직 변경 — 이번 스펙은 순수 배포/인프라 작업

## 핵심 결정

### 결정 1 — Oracle Cloud 영구무료 티어, Ampere A1(ARM) 인스턴스

AWS 프리티어(12개월 한정, 이후 자동 과금 전환)나 저가 유료 VPS 대신 Oracle Cloud의
"Always Free" 티어를 쓴다 — 기간 제한 없이 영구 무료이고, Ampere A1(ARM) 인스턴스는
최대 4 OCPU/24GB RAM까지 무료 범위 안에서 쓸 수 있어 이 프로젝트(SQLite + FastAPI +
Next.js) 규모에 여유롭다. ARM 아키텍처는 Python/Node 생태계가 이미 폭넓게 지원해
호환성 문제가 거의 없다. OS는 Ubuntu(LTS)를 쓴다 — 문서/커뮤니티 자료가 가장 많다.

가입 절차가 다소 까다로울 수 있다는 점은 사용자와 확인했고, 실패 시 AWS 등 다른 우분투
VPS로 전환하는 비용은 결정5(이식성)로 낮춰둔다.

**알려진 위험:** Oracle Always Free 인스턴스는 장기간 유휴 상태로 감지되면 회수될 수
있다는 정책이 있다. daemon이 계속 API를 호출하며 동작하므로 해당 가능성은 낮지만,
결정5의 이식성이 이 위험에 대한 사실상의 보험 역할을 한다.

### 결정 2 — systemd로 3개 프로세스 관리 (Docker/PM2 아님)

daemon/backend/frontend를 systemd 서비스로 등록한다 — 리눅스 표준 내장 기능이라
별도 도구 설치가 필요 없고, 사용자가 처음 접하는 개념 수를 최소화한다. 부팅 시 자동
시작(`enable`), 크래시 시 자동 재시작(`Restart=always`)을 기본 제공한다. Docker는
환경 재현성/이식성 이점이 있지만 이 프로젝트 규모(단일 VM, 단일 사용자, SQLite)에서는
득보다 학습비용이 크다고 판단했다(사용자 확정). PM2는 Python 프로세스 관리 용도로는
부자연스러워 제외했다.

**frontend는 프로덕션 빌드로 구동한다** — `npm run dev`(현재 로컬 개발용)가 아니라
`npm run build && npm run start`(`next start`)를 systemd가 실행한다. 개발 서버는 상시
운영에 부적합(리소스 낭비, 핫리로드 오버헤드)하기 때문이다.

세 서비스 정의(요지, 실제 파일은 `deploy/systemd/`에 작성):

```ini
# deploy/systemd/daemon.service
[Unit]
Description=Upbit Live Trading Daemon
After=network-online.target

[Service]
WorkingDirectory=/opt/upbit-v1
ExecStart=/opt/upbit-v1/.venv/bin/python -m trading.daemon
EnvironmentFile=/opt/upbit-v1/.env
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=10

[Install]
WantedBy=multi-user.target
```

backend/frontend도 동일한 구조(`ExecStart`만 각각
`uvicorn backend.main:app --host 127.0.0.1 --port 8000`,
`npm run start`로 교체). `StartLimitBurst`로 짧은 시간 내 무한 재시작 루프를 방지한다
(60초 내 10회 초과 실패 시 systemd가 재시도를 멈추고 상태를 `failed`로 남겨, 조용히
크래시-재시작을 반복하는 대신 사람이 알아챌 수 있게 한다).

### 결정 3 — Tailscale로 원격 접속, 공용 인터넷엔 포트를 열지 않는다

라이브 전략 승인/일시정지/중지 API가 실거래(자금 이동에 준하는 조작)와 직결되므로,
포트를 인터넷에 그대로 여는 건 허용하지 않는다(사용자 확정). 대신 Tailscale(가상
사설망)을 서버와 사용자의 모든 기기(핸드폰/노트북)에 설치한다 — 로그인만 하면 물리적
위치(집/카페/LTE)와 무관하게 서버에 사설 주소로 접속된다.

- Oracle 클라우드 방화벽(Security List)과 서버 OS 방화벽(`ufw`) 양쪽에서 **SSH(22)만
  인터넷에 허용**, backend(8000)/frontend(3000)는 아예 열지 않는다.
- `backend`는 `127.0.0.1`(localhost)에만 바인딩한다(`--host 127.0.0.1`) — 방화벽이
  뚫리는 실수가 있어도 로컬에서만 접근 가능하도록 이중 방어.
- `frontend`(`next start`)는 기본적으로 모든 인터페이스에 바인딩되므로, `ufw`가
  3000번 포트의 외부 인바운드를 막아 Tailscale 인터페이스로 들어온 트래픽만 도달하게
  한다.
- Tailscale MagicDNS로 서버에 사람이 읽기 쉬운 주소(예: `oracle-server.tailXXXX.ts.net`)
  를 부여한다 — IP보다 안정적이라 이 주소를 결정4의 설정값에 쓴다.
- SSH는 비밀번호 로그인을 끄고 키 인증만 허용한다(Oracle Ubuntu 이미지 기본값을 따름,
  추가 작업 없음).

### 결정 4 — CORS origin을 환경변수화한다 (결정5 이식성과 직결)

현재 `backend/main.py`는 CORS 허용 origin이 `"http://localhost:3000"`으로 하드코딩돼
있다(`dde670a`, "이 규모에서 과설계"라는 이유로 환경변수화를 보류했던 결정). 그러나
이번 배포로 origin이 배포 환경마다 달라지는(로컬=`localhost:3000`, 서버=Tailscale
MagicDNS 주소) 실제 필요가 생겼으므로, 이 결정을 뒤집는다:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("ALLOWED_ORIGIN", "http://localhost:3000")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`.env`에 `ALLOWED_ORIGIN=http://oracle-server.tailXXXX.ts.net:3000`을 추가한다. 로컬
개발 환경은 `.env`에 이 값이 없으면 기존 기본값(`localhost:3000`)으로 그대로 동작해
회귀가 없다.

프론트엔드 쪽은 코드 변경이 필요 없다 — `frontend/lib/api/client.ts`가 이미
`NEXT_PUBLIC_API_URL` 환경변수를 지원한다. 서버에서 빌드할 때
`NEXT_PUBLIC_API_URL=http://oracle-server.tailXXXX.ts.net:8000`을 `.env.production`에
설정하고 `npm run build`한다(Next.js는 `NEXT_PUBLIC_*` 값을 빌드 시점에 클라이언트
번들에 굳혀 넣으므로, 이 값을 바꾸면 반드시 재빌드해야 한다 — 결정5의 업데이트
스크립트에 반영).

### 결정 5 — 배포를 재사용 가능한 스크립트로 작성한다 (이식성 확보)

Oracle 전용 콘솔 클릭이나 사람이 손으로 따라 치는 가이드 대신, `deploy/setup.sh`(최초
설치)와 `deploy/update.sh`(코드 업데이트) 두 스크립트로 만든다. Docker를 안 쓰기로
했으므로(결정2) 이 두 스크립트 자체가 "우분투 서버라면 어디든 동일하게 동작"하는
이식성의 핵심 장치가 된다 — Oracle 가입이 막히거나 서버를 다른 클라우드(AWS 등)로
옮겨야 할 때, 새 우분투 VM에서 같은 스크립트를 그대로 실행하면 된다.

`deploy/setup.sh`가 하는 일(요지):
1. 시스템 패키지 설치(Python 3.11+, Node 20+, git, ufw)
2. 저장소 clone(또는 이미 clone돼 있으면 스킵), `/opt/upbit-v1`에 배치
3. 파이썬 가상환경 생성 + `pip install -r requirements.txt`
4. `npm install && npm run build`(frontend, `NEXT_PUBLIC_API_URL` 필요 — 스크립트가
   프롬프트로 물어보거나 미리 준비된 `.env.production`을 읽음)
5. `.env` 존재 확인(없으면 `.env.example`을 복사만 하고 사용자가 직접 채우도록 중단 —
   API 키를 스크립트 인자로 받지 않는다, 보안)
6. Tailscale 설치 안내 출력(로그인은 대화형이라 스크립트가 대신 할 수 없음 — 사용자가
   `tailscale up` 실행 + 인증 링크 클릭)
7. `deploy/systemd/*.service`를 `/etc/systemd/system/`에 복사, `daemon-reload`,
   `enable`, `start`
8. `ufw` 규칙 적용(22만 허용) + 상태 출력
9. 마지막에 "업비트 API 키의 IP 화이트리스트에 이 서버의 공인 IP(`curl ifconfig.me`
   결과)를 등록하세요"라는 안내 메시지 출력(자동화 불가 — 업비트 콘솔 수동 조작)

`deploy/update.sh`가 하는 일: `git pull` → 의존성 변경 시만 재설치 → 프론트 변경 시
`npm run build` → 변경된 서비스만 `systemctl restart`.

두 스크립트와 systemd 유닛 파일, 그리고 순서를 설명하는 `deploy/README.md`(런북)를
저장소에 커밋한다.

### 결정 6 — 서버는 새 빈 DB로 시작한다

기존 로컬 `data/trading.db`(소액 실전 테스트 기록이 남아있음)를 서버로 옮기지 않는다
— 서버는 독립된 새 배포로 취급하고, `trading/db.py`의 기존 자동 스키마 생성으로 빈
DB에서 시작한다. 로컬 PC의 과거 기록은 그대로 로컬에 남는다. (사용자가 나중에 특정
시점 상태를 이어가고 싶다면 `data/trading.db` 파일을 `scp`로 복사하는 것만으로
가능하다 — 이번 스펙 범위에서 자동화하지 않을 뿐, 수동으로는 항상 가능하다.)

### 결정 7 — 장애 대응은 systemd 기본 기능에 위임한다

결정2의 `Restart=always` + `RestartSec=5` + `StartLimitBurst=10`(60초 내)이 크래시
자동 재시작과 무한 재시작 방지를 모두 담당한다. 이 이상의 알림(예: 재시작이 반복될 때
텔레그램/이메일 통지)은 2단계 범위로 미룬다 — 4단계는 "상시 가동"만 보장하고, "장애를
사람에게 알리는 것"은 별도 단계다.

## 변경 파일

- `backend/main.py` — CORS origin을 `ALLOWED_ORIGIN` 환경변수로 변경(결정4), 로컬
  기본값 유지로 회귀 없음.
- `.env.example` — `ALLOWED_ORIGIN` 항목 추가(주석으로 로컬/서버 예시 값 안내).
- `deploy/setup.sh`(신규), `deploy/update.sh`(신규) — 배포/업데이트 스크립트(결정5).
- `deploy/systemd/daemon.service`, `deploy/systemd/backend.service`,
  `deploy/systemd/frontend.service`(신규) — systemd 유닛 정의(결정2).
- `deploy/README.md`(신규) — Oracle VM 생성부터 Tailscale/업비트 IP 화이트리스트까지
  전체 런북(결정1·3·5의 수동 단계 안내).
- `tests/test_backend.py` — `ALLOWED_ORIGIN` 미설정 시 기존 기본값(`localhost:3000`)
  유지, 설정 시 그 값을 쓰는지 검증하는 테스트 추가.

## 에러 처리

- `deploy/setup.sh`는 각 단계 실패 시 즉시 중단한다(`set -euo pipefail`) — 부분 설치
  상태로 다음 단계가 잘못된 전제 위에서 실행되는 것을 막는다.
- `.env` 파일이 없으면 스크립트는 API 키 입력을 대신하지 않고 `.env.example` 복사만
  한 뒤 사람이 직접 채우도록 명확히 안내하고 종료한다(보안 — 키를 스크립트 인자/로그에
  남기지 않는다).
- Tailscale 로그인은 대화형 인증이 필요해 자동화하지 않는다 — 스크립트가 실행할
  명령과 다음 할 일을 화면에 출력하고 사람이 이어받는다.
- systemd 서비스가 `StartLimitBurst`를 초과해 `failed` 상태가 되면, `systemctl status
  <service>`로 원인을 확인하는 방법을 `deploy/README.md`에 문서화한다(자동 알림은
  결정7에 따라 범위 밖).

## 테스트 전략

인프라/배포 작업이라 단위테스트 대상은 결정4(CORS 환경변수화) 하나뿐이다. 나머지는
배포 후 수동 스모크 체크리스트로 검증한다(`deploy/README.md`에 포함):

- **결정4**: `ALLOWED_ORIGIN` 미설정 시 `http://localhost:3000` 허용(기존 테스트
  회귀), 설정 시 그 값만 허용하는지 `TestClient`로 검증.
- **배포 후 체크리스트(수동)**:
  1. `systemctl status daemon backend frontend` 세 개 모두 `active (running)`
  2. daemon 로그(`journalctl -u daemon -f`)에 정상 폴링 기록(에러 없이 캔들/계좌
     조회 성공)
  3. 서버에서 `curl http://127.0.0.1:8000/health` → `{"status": "ok"}`
  4. 핸드폰에 Tailscale 앱 로그인 후 `http://oracle-server.tailXXXX.ts.net:3000`
     접속 → 라이브 전략 목록 페이지 정상 표시
  5. `curl ifconfig.me`로 확인한 공인 IP가 업비트 API 키 화이트리스트에 등록됐는지
     확인
  6. 외부(예: 핸드폰 LTE로 Tailscale 끄고)에서 `http://<공인IP>:8000`,
     `:3000` 접속 시도 → 응답 없음(타임아웃) 확인 — 방화벽이 실제로 막고 있는지 검증

## 자기 검토(스펙 완성도)

- **플레이스홀더 없음** — 7개 결정 각각 이유/구체적 값/파일 경로를 명시했다.
- **내부 정합성**: 결정2(systemd, Docker 아님)와 결정5(스크립트 기반 이식성)는 서로
  보강 관계다 — Docker 없이도 스크립트가 동일한 이식성을 제공하는 근거를 결정5에서
  명시했다. 결정3(Tailscale, 포트 미개방)과 결정4(CORS origin이 Tailscale 주소)는
  일관된 전제(접속은 항상 Tailscale 경유) 위에 있다.
- **범위 경계 재확인**: `trading/daemon.py`/`order_executor.py` 등 트레이딩 로직
  파일은 변경 파일 목록에 없다 — 실제로 이번 스펙의 모든 변경은 `backend/main.py`
  CORS 설정 한 줄 + 신규 `deploy/` 디렉토리뿐이다.
- **하위호환 확인**: `ALLOWED_ORIGIN` 미설정 시 기존 로컬 개발 흐름(`localhost:3000`)
  이 그대로 동작 — 로컬 개발자 경험에 회귀 없음.
- **가장 리스크가 큰 항목 식별**: 결정3(Tailscale 없이 실수로 포트가 열리는 상황)이
  가장 위험도가 높다 — 그래서 배포 후 체크리스트 6번(외부에서 직접 접속 시도해 막히는지
  확인)을 필수 항목으로 넣었다.
