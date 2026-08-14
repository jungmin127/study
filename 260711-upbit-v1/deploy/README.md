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
