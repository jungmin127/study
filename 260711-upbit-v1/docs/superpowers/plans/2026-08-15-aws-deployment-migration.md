# AWS 배포 전환 (deploy/README.md 재작성) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `deploy/README.md`를 Oracle Cloud 콘솔 기준에서 AWS EC2 launch wizard
기준으로 재작성하고, Elastic IP·보안 그룹·Budgets Actions(수동 승인) 단계를 추가한다.

**Architecture:** 코드/스크립트 변경 없음 — `deploy/setup.sh`/`update.sh`/systemd 유닛/
`backend/main.py`의 `ALLOWED_ORIGIN` 처리는 이미 클라우드 무관하게 동작한다. 유일한
변경 대상은 `deploy/README.md` 문서 하나이며, 섹션별로 순차 교체한다.

**Tech Stack:** Markdown 문서 편집. 검증은 `grep`으로 잔존 Oracle 문구/신규 섹션
존재 여부를 확인하는 방식(자동화 테스트 대상 없음 — 스펙의 "테스트 전략" 참고).

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-08-15-aws-deployment-migration-design.md`
- 리전: 아시아 태평양(서울) `ap-northeast-2`.
- 인스턴스 타입: `t4g.small`(ARM Graviton, 2 vCPU/2GB).
- AMI: Ubuntu 22.04 또는 24.04 LTS, **아키텍처는 반드시 Arm(64비트)**.
- 스토리지: gp3 20GB(기본 8GB에서 변경).
- 퍼블릭 IP는 Elastic IP로 고정한다(재부팅 시 IP 불변).
- 보안 그룹 인바운드는 SSH(22)만 `0.0.0.0/0` 허용, 그 외 포트는 열지 않는다.
- 키 페어는 ED25519, `.pem` 형식.
- 예산 액션은 **자동 실행이 아니라 수동 승인**으로 설정한다(열린 포지션의 손절/익절
  감시가 함께 끊길 위험 때문 — 스펙 결정5 참고).
- `deploy/setup.sh`, `deploy/update.sh`, `deploy/systemd/*.service`, `backend/main.py`는
  이 플랜에서 변경하지 않는다.

---

### Task 1: 헤더 + "1. AWS EC2 인스턴스 생성" + "1-1. Elastic IP" 섹션 재작성

**Files:**
- Modify: `deploy/README.md` (파일 최상단 ~ 옛 "1. Oracle Cloud VM 생성" 섹션 전체)

**Interfaces:** 없음(문서 전용 작업).

- [ ] **Step 1: 현재 내용을 정확히 교체**

`deploy/README.md`에서 다음 블록을 찾는다(파일 맨 위부터 옛 섹션 1 끝까지):

```markdown
# 상시 서버 배포 런북

이 문서는 Oracle Cloud VM 하나를 만들어 daemon/backend/frontend를 24/7 가동시키는
전체 순서다. 설계 배경은
`docs/superpowers/specs/2026-08-14-live-trading-server-deployment-design.md` 참고.

## 1. Oracle Cloud VM 생성

1. https://cloud.oracle.com 에서 계정을 만든다(신용카드 등록 필요, 무료 티어라도
   본인확인 목적).
2. 콘솔에서 Compute > Instances > Create Instance.
3. Image: **Ubuntu 22.04** (또는 최신 LTS) 선택.
4. Shape: **VM.Standard.A1.Flex**(Ampere, ARM) 선택, OCPU 2 / RAM 12GB 정도로 시작
   (Always Free 한도 내에서 조절 가능, 최대 4 OCPU/24GB까지 무료).
5. SSH 키를 생성/업로드한다(콘솔이 안내하는 대로 — 다운로드한 개인키 파일을 잘
   보관한다).
6. 생성 후 인스턴스의 **공인 IP**를 기록해둔다.
```

다음 내용으로 통째로 교체한다:

```markdown
# 상시 서버 배포 런북

이 문서는 AWS EC2 인스턴스 하나를 만들어 daemon/backend/frontend를 24/7 가동시키는
전체 순서다. 설계 배경은
`docs/superpowers/specs/2026-08-14-live-trading-server-deployment-design.md`(systemd/
Tailscale 등 클라우드 무관 결정)와
`docs/superpowers/specs/2026-08-15-aws-deployment-migration-design.md`(AWS 인스턴스
프로비저닝 결정) 참고.

## 1. AWS EC2 인스턴스 생성

1. https://aws.amazon.com/ko/ 에서 회원가입 후 콘솔(https://console.aws.amazon.com/)에
   로그인한다.
2. 콘솔 우측 상단 리전을 **아시아 태평양(서울) ap-northeast-2**로 맞춘다 — 업비트는
   한국 거래소라 서울 리전이 API 응답 지연이 가장 적다.
3. 콘솔 검색창에 "EC2"를 검색해 EC2 대시보드로 이동, **"인스턴스 시작"** 버튼을
   클릭한다.
4. **이름**: 원하는 대로 입력(예: `upbit-live-trading`).
5. **애플리케이션 및 OS 이미지(AMI)**: Ubuntu 선택 → **Ubuntu Server 22.04 LTS**
   (또는 24.04 LTS), 아키텍처를 **64비트(Arm)**로 변경한다(기본값은 x86이므로 반드시
   Arm으로 바꿔야 한다 — 인스턴스 타입과 아키텍처가 맞지 않으면 시작 시 오류가 난다).
6. **인스턴스 유형**: `t4g.small` 검색해서 선택(2 vCPU / 2GiB RAM, ARM Graviton).
7. **키 페어 생성**: "새 키 페어 생성" 클릭 → 이름 입력(예: `upbit-server-key`) →
   **키 페어 유형: ED25519**, **프라이빗 키 파일 형식: .pem** 선택 → 생성 → `.pem`
   파일이 자동 다운로드된다. **이 파일은 재발급이 안 되니 잘 보관한다.**
8. **네트워크 설정**: "편집" 클릭
   - 보안 그룹 이름: 원하는 대로(예: `upbit-server-sg`).
   - 인바운드 보안 그룹 규칙: 기본으로 잡히는 **SSH, 포트 22, 소스 0.0.0.0/0** 규칙
     하나만 남긴다. 그 외 규칙(HTTP/HTTPS 등)은 추가하지 않는다 — backend(8000)/
     frontend(3000)는 Tailscale로만 접속하므로 인터넷에 열지 않는다.
9. **스토리지 구성**: 루트 볼륨 크기를 기본 8GiB에서 **20GiB**로 변경한다(볼륨
   유형은 기본값 gp3 유지) — venv/`node_modules`/빌드 산출물을 합치면 8GiB로는
   빠듯하다.
10. 우측 요약을 확인하고 **"인스턴스 시작"**을 클릭한다.
11. 인스턴스가 "실행 중" 상태가 되면(1~2분) 다음 단계로 넘어간다.

### 1-1. Elastic IP(고정 퍼블릭 IP) 할당

인스턴스를 재부팅하거나 잠깐 멈췄다 켜도 IP가 바뀌지 않도록 고정 IP를 붙인다 —
업비트 API 키의 IP 화이트리스트를 한 번만 등록하면 되게 하기 위함이다.

1. EC2 콘솔 좌측 메뉴에서 **"탄력적 IP"** 클릭 → **"탄력적 IP 주소 할당"** → 그대로
   할당한다.
2. 방금 할당된 주소를 선택 → **작업 → 탄력적 IP 주소 연결** → 방금 만든 인스턴스를
   선택 → 연결한다.
3. EC2 인스턴스 목록에서 이 인스턴스의 "퍼블릭 IPv4 주소"가 방금 할당한 탄력적 IP로
   고정된 걸 확인한다. **이후 모든 단계에서 "서버 공인 IP"는 이 탄력적 IP를
   가리킨다.**

**주의**: 탄력적 IP가 실행 중인 인스턴스에 연결되어 있는 동안은 시간당 소액만
청구되지만(24/7 가동 전제라 어차피 계속 붙어있으므로 실질적 추가 비용 없음),
인스턴스를 오래 정지시켜 둘 계획이라면 안 쓰는 탄력적 IP는 릴리스(해제)하는 것이
좋다(이 프로젝트는 상시 가동이 목적이라 해당 사항 없음).
```

- [ ] **Step 2: 결과 확인**

Run: `grep -n "^## \|^### " deploy/README.md`
Expected: 출력 첫 두 줄이 각각 `## 1. AWS EC2 인스턴스 생성`,
`### 1-1. Elastic IP(고정 퍼블릭 IP) 할당`이어야 한다(이후 섹션은 아직 옛 번호 그대로
남아있는 게 정상 — Task 2/3에서 이어서 정리).

Run: `grep -n "Oracle\|cloud.oracle.com\|VM.Standard" deploy/README.md`
Expected: 이 Task에서 손댄 범위(파일 맨 위 ~ 섹션 1-1)에는 더 이상 안 나와야 한다.
아직 옛 "7. Oracle이 안 되면" 섹션이 남아있어 최소 1건은 매칭될 수 있음(Task 3에서
정리 예정) — 그 외 위치에서 나오면 안 됨.

- [ ] **Step 3: 커밋**

```bash
git add deploy/README.md
git commit -m "docs: 배포 런북 VM 생성 섹션을 AWS EC2 기준으로 재작성"
```

---

### Task 2: SSH 접속 섹션에 Windows pem 권한 안내 추가 + IP 표현 통일

**Files:**
- Modify: `deploy/README.md` (옛 "2. SSH 접속 및 배포 스크립트 실행" 섹션의 코드블록,
  "4. 업비트 API IP 화이트리스트 등록" 섹션)

**Interfaces:** 없음(문서 전용 작업). Task 1이 도입한 "탄력적 IP" 용어를 이어서 쓴다.

- [ ] **Step 1: SSH 코드블록 교체**

다음 블록을 찾는다:

```markdown
## 2. SSH 접속 및 배포 스크립트 실행

```bash
ssh -i <다운로드한-키파일> ubuntu@<공인IP>
```

접속 후:
```

다음으로 교체한다(첫 줄 `## 2. ...` 헤딩은 그대로 유지):

```markdown
## 2. SSH 접속 및 배포 스크립트 실행

Windows(Git Bash)에서:

```bash
chmod 400 <다운로드한-키파일>.pem
ssh -i <다운로드한-키파일>.pem ubuntu@<탄력적 IP>
```

`chmod 400`이 적용되지 않고("Permissions ... are too open" 에러) 접속이 거부되면
PowerShell에서 다음을 실행해 키 파일 권한을 좁힌다:

```powershell
icacls "<다운로드한-키파일>.pem" /inheritance:r
icacls "<다운로드한-키파일>.pem" /grant:r "$($env:USERNAME):(R)"
```

접속 후:
```

- [ ] **Step 2: 업비트 화이트리스트 섹션 문구 수정**

다음 블록을 찾는다:

```markdown
## 4. 업비트 API IP 화이트리스트 등록

`deploy/setup.sh` 마지막에 출력되는 서버 공인 IP를, 업비트 웹사이트의 API 키 관리
페이지에서 해당 키의 IP 화이트리스트에 추가한다.
```

다음으로 교체한다:

```markdown
## 4. 업비트 API IP 화이트리스트 등록

`deploy/setup.sh` 마지막에 출력되는 서버 공인 IP(1-1에서 연결한 탄력적 IP와 같은
값이어야 한다)를, 업비트 웹사이트의 API 키 관리 페이지에서 해당 키의 IP
화이트리스트에 추가한다.
```

- [ ] **Step 3: 결과 확인**

Run: `grep -n "공인IP>\|<공인 IP>" deploy/README.md`
Expected: 매칭 없음(전부 `<탄력적 IP>` 또는 "서버 공인 IP(탄력적 IP...)" 형태로
바뀌어야 함).

Run: `grep -n "chmod 400\|icacls" deploy/README.md`
Expected: 두 명령 모두 최소 1회씩 나와야 함.

- [ ] **Step 4: 커밋**

```bash
git add deploy/README.md
git commit -m "docs: SSH 접속 섹션에 Windows pem 권한 안내 추가"
```

---

### Task 3: 예산 대응 섹션 신규 추가 + "다른 클라우드로" 섹션 갱신

**Files:**
- Modify: `deploy/README.md` (옛 "7. Oracle이 안 되면: 다른 클라우드로" 섹션)

**Interfaces:** 없음(문서 전용 작업).

- [ ] **Step 1: 옛 섹션 7을 새 섹션 7(예산)+섹션 8(다른 클라우드로)로 교체**

다음 블록을 찾는다(파일 맨 끝):

```markdown
## 7. Oracle이 안 되면: 다른 클라우드로

이 저장소는 Docker를 쓰지 않아 특정 클라우드에 종속되지 않는다. 우분투 VM을 아무
데서나(AWS, 다른 VPS 등) 새로 만들고 위 2~5단계를 그대로 반복하면 된다. 달라지는 건
`.env`/`frontend/.env.production`을 새로 채우는 것과, 업비트 IP 화이트리스트를 새
IP로 바꾸는 것뿐이다.
```

다음으로 교체한다:

```markdown
## 7. 예산 초과 대비 — AWS Budgets + Budgets Actions 설정

$200 크레딧(6개월) 소진을 조기에 알아채고, 필요하면 서버를 직접 멈출 수 있게 다음을
한 번 설정해둔다.

### 7-1. IAM 역할 생성 (Budgets가 인스턴스를 멈출 수 있는 최소 권한)

1. 콘솔에서 "IAM" 검색 → 좌측 **"역할"** → **"역할 생성"**.
2. 신뢰할 수 있는 엔터티 유형: **"사용자 지정 신뢰 정책"** 선택 후 다음 JSON을
   붙여넣는다:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "budgets.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

3. 다음 화면에서 **"정책 생성"**으로 새 창을 열어 아래 JSON으로 인라인 정책을
   만든다(`<인스턴스ID>`는 EC2 콘솔에서 확인한 `i-`로 시작하는 ID로 바꾼다):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ec2:StopInstances",
      "Resource": "arn:aws:ec2:ap-northeast-2:*:instance/<인스턴스ID>"
    }
  ]
}
```

4. 역할 이름을 입력(예: `budgets-stop-upbit-server`)하고 생성한다.

### 7-2. 예산 + 예산 액션 생성

1. 콘솔에서 "Budgets" 검색 → **"예산 생성"**.
2. 예산 유형: **비용 예산**, 예산 금액: **월 $20**.
3. 예산 알림: 실제 비용이 예산의 100% 도달 시 이메일로 알림받도록 이메일 주소를
   입력한다.
4. **예산 액션 추가**:
   - 작업 유형: **"Amazon EC2 인스턴스 중지"**
   - 대상 인스턴스: 방금 만든 인스턴스 선택
   - 실행 방식: **"승인 후 실행"**(자동 실행이 아니다 — 이메일로 온 실행 링크를
     직접 눌러야 실제로 인스턴스가 멈춘다)
   - IAM 역할: 7-1에서 만든 역할 선택
5. 저장한다.

**중요**: 이 액션은 "자동 정지"가 아니라 "알림 + 수동 실행"이다. 열린 포지션이 있는
상태에서 서버를 멈추면 손절/익절 감시도 함께 멈추므로, 알림을 받으면 먼저
`/live-strategies` 페이지에서 열린 포지션이 있는지 확인한 뒤 정지 여부를 판단한다.

**한계**: 인스턴스를 정지해도 탄력적 IP(월 ~$3.6)와 EBS 스토리지(월 ~$1.6) 요금은
계속 청구된다 — 가장 큰 비중(인스턴스 시간당 요금)만 막을 뿐이다.

## 8. 다른 클라우드로 이전하고 싶다면

이 저장소는 Docker를 쓰지 않아 특정 클라우드에 종속되지 않는다. 우분투 VM을 아무
데서나(다른 리전, 다른 클라우드 제공자 등) 새로 만들고 위 2~5단계를 그대로
반복하면 된다. 달라지는 건 `.env`/`frontend/.env.production`을 새로 채우는 것과,
업비트 IP 화이트리스트를 새 IP로 바꾸는 것뿐이다.
```

- [ ] **Step 2: 전체 파일 최종 검증**

Run: `grep -n "Oracle\|cloud.oracle.com\|VM.Standard\|OCPU" deploy/README.md`
Expected: 매칭 없음(파일 전체에서 Oracle 관련 문구가 완전히 사라져야 함).

Run: `grep -n "^## " deploy/README.md`
Expected: 정확히 다음 8줄이 이 순서로 출력됨:
```
## 1. AWS EC2 인스턴스 생성
## 2. SSH 접속 및 배포 스크립트 실행
## 3. Tailscale 연결
## 4. 업비트 API IP 화이트리스트 등록
## 5. 확인
## 6. 이후 코드 업데이트할 때
## 7. 예산 초과 대비 — AWS Budgets + Budgets Actions 설정
## 8. 다른 클라우드로 이전하고 싶다면
```

Run: `grep -n "탄력적 IP\|Elastic IP" deploy/README.md`
Expected: 최소 5회 이상 매칭(섹션 1-1, 2, 4, 7-1 설명 등에서 일관되게 사용).

- [ ] **Step 3: 커밋**

```bash
git add deploy/README.md
git commit -m "docs: 예산 초과 대비 섹션 추가 및 클라우드 이전 섹션 정리"
```

---

### Task 4: 프로젝트 메모리 갱신 (저장소 밖, 커밋 없음)

**Files:** 없음 — 이 Task는 코드/문서 변경이 아니라 다음 세션 연속성을 위한 메모리
갱신이다. `superpowers:executing-plans`/`subagent-driven-development`를 실행하는
에이전트가 사람일 경우 스킵해도 무방하지만, Claude가 이 플랜을 실행 중이라면 자신의
메모리 시스템(`~/.claude/projects/.../memory/`)의
`upbit-v1-server-deployment-cloud-provider-decision.md`를 다음 내용으로 갱신한다:

- [ ] **Step 1**: `upbit-v1-server-deployment-cloud-provider-decision.md`의 "다음
  세션에서 할 일" 섹션을, "`deploy/README.md`가 AWS EC2 기준으로 재작성 완료됨
  (SHIPPED&PUSHED, 이 플랜 참고). 남은 건 사용자가 실제로 콘솔에서 인스턴스를
  만들고 `deploy/README.md`를 따라가는 것뿐"으로 갱신한다.
- [ ] **Step 2**: `MEMORY.md` 인덱스에서 해당 항목의 한 줄 설명도 같이 갱신한다.
