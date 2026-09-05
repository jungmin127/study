# 상시 서버 배포 런북

이 문서는 AWS EC2 인스턴스 하나를 만들어 daemon/backend/frontend를 24/7 가동시키는
전체 순서다. 설계 배경은
`docs/superpowers/specs_v1/2026-08-14-live-trading-server-deployment-design.md`(systemd/
Tailscale 등 클라우드 무관 결정)와
`docs/superpowers/specs_v1/2026-08-15-aws-deployment-migration-design.md`(AWS 인스턴스
프로비저닝 결정) 참고.

## 1. AWS EC2 인스턴스 생성

1. https://aws.amazon.com/ko/ 에서 회원가입 후 콘솔(https://console.aws.amazon.com/)에
   로그인한다.
2. 콘솔 우측 상단 리전을 **아시아 태평양(서울) ap-northeast-2**로 맞춘다 — 업비트는
   한국 거래소라 서울 리전이 API 응답 지연이 가장 적다.
3. 콘솔 검색창에 "EC2"를 검색해 EC2 대시보드로 이동, **"인스턴스 시작"** 버튼을
   클릭한다.
4. **이름**: 원하는 대로 입력(예: `upbit-live-trading`).
5. **애플리케이션 및 OS 이미지(AMI)**: Ubuntu 선택 → **Ubuntu Server 22.04 LTS**를
   선택한다(24.04 LTS는 선택하지 않는다 — 24.04는 기본 Python이 3.12이고
   `python3.11` 패키지가 없어 `deploy/setup.sh`가 실패한다). 아키텍처를
   **64비트(Arm)**로 변경한다(기본값은 x86이므로 반드시
   Arm으로 바꿔야 한다 — 인스턴스 타입과 아키텍처가 맞지 않으면 시작 시 오류가 난다).
6. **인스턴스 유형**: `t4g.small` 검색해서 선택(2 vCPU / 2GiB RAM, ARM Graviton).
7. **키 페어 생성**: "새 키 페어 생성" 클릭 → 이름 입력(예: `upbit-server-key`) →
   **키 페어 유형: ED25519**, **프라이빗 키 파일 형식: .pem** 선택 → 생성 → `.pem`
   파일이 자동 다운로드된다. **이 파일은 재발급이 안 되니 잘 보관한다.**
8. **네트워크 설정**: "편집" 클릭
   - 보안 그룹 이름: 원하는 대로(예: `upbit-server-sg`).
   - 인바운드 보안 그룹 규칙: 기본으로 잡히는 **SSH, 포트 22, 소스 0.0.0.0/0** 규칙
     하나만 남긴다. 그 외 규칙(HTTP/HTTPS 등)은 추가하지 않는다 — backend(8000)/
     frontend(3000)는 Tailscale로만 접속하므로 인터넷에 열지 않는다. 단, 이 SSH
     규칙 자체는 나중에 지우지 않는다 — 지우면 콘솔에서 별도 복구 절차 없이는
     인스턴스에 접속할 방법이 없어져 완전히 잠길 수 있다.
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

비용 경보(7절)는 인스턴스를 만든 직후 바로 설정해두는 걸 권장한다.

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

접속 후, 먼저 최신 Node.js를 설치한다(Ubuntu 22.04의 기본 `apt` nodejs는 v12라
Next.js 14 빌드가 실패하므로, `deploy/setup.sh` 실행 전에 NodeSource 저장소를
추가해 apt가 최신 LTS(20.x)를 설치하게 만든다):

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
```

이어서 저장소를 내려받고 배포 스크립트를 실행한다:

```bash
sudo apt-get update && sudo apt-get install -y git
sudo mkdir -p /opt/study && sudo chown -R $USER:$USER /opt/study
git clone https://github.com/jungmin127/study.git /opt/study
cd /opt/study/260711-upbit-v1
bash deploy/setup.sh
```

`.env`와 `frontend/.env.production`이 없으면 스크립트가 중간에 멈추고 만드는 법을
알려준다 — 안내대로 채운 뒤 `bash deploy/setup.sh`를 다시 실행한다. 이때 필요한
Tailscale 주소는 3절을 먼저 진행해(`sudo tailscale up` 로그인 후 Tailscale 관리
콘솔에서 MagicDNS 이름을 확인) 얻은 뒤 이 단계로 돌아온다.

## 3. Tailscale 연결

`deploy/setup.sh`가 Tailscale을 설치한다. 로그인이 아직이면, 먼저 호스트명을
읽기 쉬운 이름으로 지정해둔다(안 해두면 MagicDNS 이름이 `ip-172-31-x-x`처럼
알아보기 어렵게 잡힐 수 있다):

```bash
sudo hostnamectl set-hostname upbit-server
```

그다음 로그인한다:

```bash
sudo tailscale up
```

화면에 뜨는 링크를 눌러 브라우저에서 로그인(사용자의 Tailscale 계정으로).

핸드폰/노트북에도 각각 Tailscale 앱을 설치하고 **같은 계정으로 로그인**한다. 이후
Tailscale 관리 콘솔(https://login.tailscale.com/admin/machines)에서 서버의
MagicDNS 이름(예: `upbit-server.tailXXXX.ts.net`)을 확인할 수 있다.

## 4. 업비트 API IP 화이트리스트 등록

`deploy/setup.sh` 마지막에 출력되는 서버 공인 IP(1-1에서 연결한 탄력적 IP와 같은
값이어야 한다)를, 업비트 웹사이트의 API 키 관리 페이지에서 해당 키의 IP
화이트리스트에 추가한다.

## 5. 확인

```bash
systemctl status daemon backend frontend
journalctl -u daemon -f     # 실시간 로그, Ctrl+C로 종료
curl http://127.0.0.1:8000/health
```

핸드폰에서 Tailscale 앱 로그인 후 브라우저로
`http://upbit-server.tailXXXX.ts.net:3000` 접속 — 라이브 전략 목록이 보이면 완료.

**보안 확인(중요):** Tailscale을 끄고 핸드폰 LTE로 `http://<탄력적 IP>:8000`,
`http://<탄력적 IP>:3000`에 직접 접속을 시도해서 응답이 없는지(타임아웃) 확인한다
— 방화벽이 실제로 막고 있는지 검증하는 단계다.

## 6. 이후 코드 업데이트할 때

로컬 PC에서 평소처럼 개발 → `git push` 한 뒤, 서버에 SSH 접속해서:

```bash
cd /opt/study/260711-upbit-v1
bash deploy/update.sh
```

**주의:** daemon 재시작 중 몇 초간 실시간 손절/익절 감시가 끊긴다. 포지션이 없을 때,
또는 직접 지켜보고 있을 때 실행하는 걸 권장한다.

## 7. 예산 초과 대비 — AWS Budgets + Budgets Actions + Cost Explorer

$200 크레딧(6개월) 소진을 조기에 알아채고, 필요하면 서버를 직접 멈출 수 있게 다음을
한 번 설정해둔다.

예상 정상 운영 비용: 인스턴스 약 $10~13 + 탄력적 IP 약 $3.6 + EBS 약 $1.6 ≈ 월
$15~18(리전/시점에 따라 달라질 수 있음).

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

3. 권한 추가 화면은 아무 정책도 선택하지 않고 그대로 **"다음"**을 눌러 넘어간다
   (이 마법사의 "정책 생성" 버튼은 인라인 정책이 아니라 별도의 고객 관리형
   정책을 만드는 것이라 이 용도에 맞지 않다 — 권한은 역할을 만든 뒤 역할
   상세 페이지에서 인라인 정책으로 붙인다).
4. 역할 이름을 입력(예: `budgets-stop-upbit-server`)하고 생성한다.
5. 생성된 역할 상세 페이지로 이동 → **"권한"** 탭 → **"권한 추가"** →
   **"인라인 정책 생성"** → 아래 JSON을 붙여넣는다(`<인스턴스ID>`는 EC2
   콘솔에서 확인한 `i-`로 시작하는 ID로 바꾼다):

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

6. 정책 이름을 입력(예: `stop-upbit-server`)하고 저장한다.

### 7-2. 예산 + 예산 액션 생성

1. 콘솔에서 "Budgets" 검색 → **"예산 생성"**.
2. 예산 유형: **비용 예산**, 예산 금액: **월 $30**. 비용 추적 범위는 **"실제 비용
   기준(크레딧 제외)"**으로 설정한다 — 콘솔 기본값이 "크레딧 포함"이면 체크를
   해제한다. 이 예산은 크레딧 잔액과 무관하게 실제 리소스 사용량 이상 징후를
   잡기 위한 것이고(크레딧 잔액 확인은 7-3의 Cost Explorer가 맡는다), 크레딧이
   적용되는 동안은 실제 청구액이 0에 가까워 "크레딧 포함" 기준으로는 임계값에
   도달하지 않기 때문이다.
3. 예산 알림: 실제 비용이 예산의 **80%(=$24) 도달 시** 이메일로 알림받도록 이메일
   주소를 입력한다(100%로 하면 정상 운영 비용($15~18) 대비 여유가 거의 없어
   경보가 매달 울리거나 무의미해진다).
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

**재개**: 예산 액션으로 인스턴스가 정지된 뒤 다시 켜려면 EC2 콘솔 → 인스턴스
선택 → 인스턴스 상태 → 인스턴스 시작. 탄력적 IP가 그대로 붙어있으므로 업비트
화이트리스트 재등록은 불필요하고(1-1 참고), daemon/backend/frontend 세 서비스는
systemd `enable` 상태라 부팅과 함께 자동으로 다시 켜진다.
`systemctl status daemon backend frontend`로 세 개 모두 active인지 확인한다.

### 7-3. Cost Explorer 활성화 — 월별 실사용 비용 확인

크레딧($200, 6개월) 기간 동안 실제로 자동매매를 운용해보면서 한 달에 얼마나
쓰는지 파악해두면, 크레딧 소진 후 유료로 계속 쓸지 판단하는 데 도움이 된다.
Budgets 알림(7-2)은 "임계값을 넘었는지"만 알려줄 뿐 항목별 추이는 보여주지
않으므로, Cost Explorer를 별도로 켠다.

1. 콘솔에서 "Cost Explorer" 검색 → 처음 접속하면 **"Cost Explorer 활성화"** 버튼이
   보인다 — 클릭한다(무료 기능, 활성화 후 데이터가 반영되기까지 최대 24시간 걸릴
   수 있다).
2. 활성화 후 좌측 메뉴에서 **"비용 및 사용량"** 리포트로 이동, 기간을 "월별", 그룹화
   기준을 "서비스"로 설정하면 EC2/EBS/VPC(탄력적 IP) 등 항목별 월간 비용을 그래프로
   볼 수 있다.
3. 매달 한 번씩 확인하는 습관을 들이면, 크레딧이 6개월 뒤 소진되기 전에 "이 페이스면
   유료 전환 시 월 얼마"인지 미리 알 수 있다.

## 8. 다른 클라우드로 이전하고 싶다면

이 저장소는 Docker를 쓰지 않아 특정 클라우드에 종속되지 않는다. 우분투 VM을 아무
데서나(다른 리전, 다른 클라우드 제공자 등) 새로 만들고 위 2~5단계를 그대로
반복하면 된다. 달라지는 건 `.env`/`frontend/.env.production`을 새로 채우는 것과,
업비트 IP 화이트리스트를 새 IP로 바꾸는 것뿐이다.

## 9. HTTPS + PWA(홈 화면 앱) 설정

모바일 홈 화면에 아이콘을 추가해 브라우저 주소창 없이 앱처럼 쓰려면(PWA
standalone 모드) HTTPS가 필수다(Chrome은 HTTP 사이트에서 standalone 설치를
허용하지 않는다). 최초 1회만 하면 된다.

### 9-1. Tailscale HTTPS 인증서 기능 켜기(최초 1회, 웹 콘솔)

https://login.tailscale.com/admin/dns 접속 → "HTTPS Certificates" 섹션 →
"Enable HTTPS" 클릭. 계정당 한 번만 켜면 된다.

### 9-2. 인증서 발급 + nginx 설치

서버에 SSH 접속 후:

```bash
sudo mkdir -p /etc/nginx/tailscale-certs
sudo tailscale cert \
  --cert-file=/etc/nginx/tailscale-certs/upbit-server.crt \
  --key-file=/etc/nginx/tailscale-certs/upbit-server.key \
  upbit-server.tailXXXX.ts.net   # 본인 Tailscale MagicDNS 주소로 교체

sudo apt-get update && sudo apt-get install -y nginx
```

### 9-3. nginx 설정 배포

저장소의 `deploy/nginx/upbit.conf`를 그대로 쓴다 — `server_name`을 본인 MagicDNS
주소로 바꾼 뒤:

```bash
sudo cp deploy/nginx/upbit.conf /etc/nginx/sites-available/upbit.conf
sudo ln -sf /etc/nginx/sites-available/upbit.conf /etc/nginx/sites-enabled/upbit.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl enable nginx --now
```

`/api/`로 시작하는 요청은 backend(8000)로, 나머지는 frontend(3000)로 프록시된다.
포트 3000/8000 직접 접속은 그대로 살아있다(막지 않음) — 443만 새로 추가되는
경로다.

### 9-4. 환경변수를 https로 바꾸고 재빌드

`.env`의 `ALLOWED_ORIGIN`과 `frontend/.env.production`의 `NEXT_PUBLIC_API_URL`을
포트 없는 `https://upbit-server.tailXXXX.ts.net`로 바꾼다(둘 다 443이 기본
포트라 생략). **HTTPS 페이지에서 HTTP API를 부르면 브라우저가 mixed content로
막으므로 이 변경은 선택이 아니라 필수다.** 이후:

```bash
cd frontend && npm run build
sudo systemctl restart backend frontend
```

daemon은 건드릴 필요 없다(위 변경은 backend/frontend 설정일 뿐 실거래 로직과
무관).

### 9-5. 인증서 자동 갱신

Tailscale HTTPS 인증서는 Let's Encrypt 기반이라 90일마다 만료된다.
`deploy/nginx/renew-cert.sh`를 `/etc/cron.monthly/`에 넣어두면 매달 자동
재발급된다:

```bash
sudo cp deploy/nginx/renew-cert.sh /etc/cron.monthly/tailscale-cert-renew
sudo chmod +x /etc/cron.monthly/tailscale-cert-renew
```

(스크립트 안의 호스트명도 본인 MagicDNS 주소로 맞춰야 한다.)

### 9-6. 확인 + 홈 화면에 추가

`https://upbit-server.tailXXXX.ts.net`으로 접속해 정상 로딩되는지 확인한 뒤,
핸드폰 Chrome에서 우측 상단 메뉴 → **"설치"**(바로가기 만들기 아님) → 홈
화면 아이콘 생성. 탭하면 주소창 없이 `/journal`로 바로 랜딩된다.
