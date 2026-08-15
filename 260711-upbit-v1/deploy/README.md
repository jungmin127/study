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

## 2. SSH 접속 및 배포 스크립트 실행

```bash
ssh -i <다운로드한-키파일> ubuntu@<공인IP>
```

접속 후:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/jungmin127/study.git /opt/study
sudo chown -R $USER:$USER /opt/study
cd /opt/study/260711-upbit-v1
bash deploy/setup.sh
```

`.env`와 `frontend/.env.production`이 없으면 스크립트가 중간에 멈추고 만드는 법을
알려준다 — 안내대로 채운 뒤 `bash deploy/setup.sh`를 다시 실행한다.

## 3. Tailscale 연결

`deploy/setup.sh`가 Tailscale을 설치한다. 로그인이 아직이면:

```bash
sudo tailscale up
```

화면에 뜨는 링크를 눌러 브라우저에서 로그인(사용자의 Tailscale 계정으로).

핸드폰/노트북에도 각각 Tailscale 앱을 설치하고 **같은 계정으로 로그인**한다. 이후
Tailscale 관리 콘솔(https://login.tailscale.com/admin/machines)에서 서버의
MagicDNS 이름(예: `oracle-server.tailXXXX.ts.net`)을 확인할 수 있다.

## 4. 업비트 API IP 화이트리스트 등록

`deploy/setup.sh` 마지막에 출력되는 서버 공인 IP를, 업비트 웹사이트의 API 키 관리
페이지에서 해당 키의 IP 화이트리스트에 추가한다.

## 5. 확인

```bash
systemctl status daemon backend frontend
journalctl -u daemon -f     # 실시간 로그, Ctrl+C로 종료
curl http://127.0.0.1:8000/health
```

핸드폰에서 Tailscale 앱 로그인 후 브라우저로
`http://oracle-server.tailXXXX.ts.net:3000` 접속 — 라이브 전략 목록이 보이면 완료.

**보안 확인(중요):** Tailscale을 끄고 핸드폰 LTE로 `http://<서버-공인IP>:8000`,
`http://<서버-공인IP>:3000`에 직접 접속을 시도해서 응답이 없는지(타임아웃) 확인한다
— 방화벽이 실제로 막고 있는지 검증하는 단계다.

## 6. 이후 코드 업데이트할 때

로컬 PC에서 평소처럼 개발 → `git push` 한 뒤, 서버에 SSH 접속해서:

```bash
cd /opt/study/260711-upbit-v1
bash deploy/update.sh
```

**주의:** daemon 재시작 중 몇 초간 실시간 손절/익절 감시가 끊긴다. 포지션이 없을 때,
또는 직접 지켜보고 있을 때 실행하는 걸 권장한다.

## 7. Oracle이 안 되면: 다른 클라우드로

이 저장소는 Docker를 쓰지 않아 특정 클라우드에 종속되지 않는다. 우분투 VM을 아무
데서나(AWS, 다른 VPS 등) 새로 만들고 위 2~5단계를 그대로 반복하면 된다. 달라지는 건
`.env`/`frontend/.env.production`을 새로 채우는 것과, 업비트 IP 화이트리스트를 새
IP로 바꾸는 것뿐이다.
