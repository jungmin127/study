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

## 5. 로컬 백테스트 결과를 서버로 가져오기

무거운 grid search(9-오실레이터 전 교차, 20,700개 조합)를 AWS 서버에서 직접 돌리면
`t4g.small`의 CPU 크레딧을 상당히 소모한다. 대신 로컬 PC에서 grid search를 돌리고,
그 결과(`data/backtest_results.db`)만 서버로 보내 "실거래 전환"에 쓸 수 있다.

### 최초 1회 설정

로컬 저장소 루트의 `.env`에 다음 두 줄을 추가한다(1절의 SSH 접속에 쓴 값과 동일):

```
DEPLOY_SSH_KEY_PATH=<다운로드한-키파일>.pem의 절대 경로 (Git Bash 형식, 예: /c/Users/이름/Downloads/key.pem — C:\... 형식은 scp가 인식하지 못합니다)
DEPLOY_SERVER_HOST=ubuntu@<탄력적 IP 또는 Tailscale MagicDNS 주소>
```

### 실행

(이 절을 처음 실행하기 전에는 1~2절로 서버 코드를 최신으로 갱신해둬야 한다 —
`scripts/import_backtest_results.py`가 서버에 있어야 이 명령이 동작한다.)

**주의**: 로컬 백엔드나 그리드서치가 `data/backtest_results.db`에 한창 쓰고 있는 도중에
실행하면 전송되는 파일이 일관되지 않을 수 있다 — 그리드서치가 끝난 뒤, 가능하면 로컬
백엔드가 조용한 시점에 실행한다.

로컬 PC(Git Bash)에서 저장소 루트로 이동한 뒤:

```bash
bash scripts/push_backtest_results.sh
```

이 한 줄이 `data/backtest_results.db`를 서버로 전송하고, 서버에서 자동으로 병합까지
실행한다. `run_id`가 백테스트 조건의 내용 기반 해시라 이미 서버에 있는 결과는 자동
건너뛰므로, 로컬에서 grid search를 새로 돌릴 때마다 이 명령을 반복 실행해도 안전하다.

실행이 끝나면 "백테스트 결과 병합 완료: 신규 N건 추가, 기존 M건 건너뜀"이 출력된다.
이후 서버 프론트엔드의 백테스트 목록에서 새로 옮겨진 결과를 확인하고, 그 결과
상세 페이지의 "실거래 전환" 버튼으로 라이브 전략을 만들 수 있다.
