# 서버 코드 업데이트 절차

로컬에서 코드를 고치고 `git push`한 뒤, 그 내용을 상시 가동 중인 AWS 서버에
반영하는 절차다. `git push`만으로는 서버에 자동 반영되지 않는다 — 아래 단계를
매번 직접 실행해야 한다. 전체 배포 런북은 `deploy/README.md` 참고.

## 1. SSH로 서버 접속

로컬 PC(Windows, Git Bash)에서:

```bash
ssh -i <다운로드한-키파일>.pem ubuntu@<탄력적 IP>
```

- `<다운로드한-키파일>.pem`: AWS 인스턴스를 만들 때 다운로드한 `.pem` 키 파일 경로
  (예: `~/Downloads/upbit-server-key.pem`)
- `<탄력적 IP>`: EC2에 연결해둔 고정 퍼블릭 IP

접속되면 프롬프트가 로컬(`jungm@...`)에서 서버 쪽(`ubuntu@upbit-server:~$` 형태)으로
바뀐다 — 이제 서버 셸 안에 들어와 있다는 뜻이다.

**키 파일 권한 에러가 날 때**: `chmod 400`이 적용되지 않아 "Permissions ... are too
open"으로 접속이 거부되면, PowerShell에서 다음을 실행해 키 파일 권한을 좁힌다.

```powershell
icacls "<다운로드한-키파일>.pem" /inheritance:r
icacls "<다운로드한-키파일>.pem" /grant:r "$($env:USERNAME):(R)"
```

## 2. 서버 셸 안에서 업데이트 스크립트 실행

SSH로 접속된 상태에서(로컬 창이 아니라 서버 프롬프트에서) 이어서 입력한다:

```bash
cd /opt/study/260711-upbit-v1
bash deploy/update.sh
```

두 줄을 순차로 입력해도 되고, 한 줄로 이어 써도 된다:

```bash
cd /opt/study/260711-upbit-v1 && bash deploy/update.sh
```

`update.sh`가 순서대로 처리한다:

1. `git pull` — 최신 코드 반영
2. `pip install -r requirements.txt` — 파이썬 의존성 갱신
3. `npm install && npm run build` — 프론트엔드 재빌드
4. `daemon`/`backend`/`frontend` 세 systemd 서비스 재시작

**주의**: 4번 단계에서 daemon이 재시작되는 몇 초간 실시간 손절/익절 감시가
끊긴다. 열린 포지션이 없을 때, 또는 직접 지켜보고 있을 때 실행하는 걸
권장한다.

## 3. 확인

스크립트 마지막에 `systemctl status daemon backend frontend` 결과가 자동으로
출력된다. 세 서비스 모두 `active (running)`이면 정상 반영된 것이다.

필요하면 직접 재확인:

```bash
systemctl status daemon backend frontend
journalctl -u daemon -f     # 실시간 로그, Ctrl+C로 종료
curl http://127.0.0.1:8000/health
```

## 4. 서버 세션 종료

```bash
exit
```

로 SSH 접속을 끊고 로컬 셸로 돌아온다.
