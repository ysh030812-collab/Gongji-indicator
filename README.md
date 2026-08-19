# 인하대 의과대학 공지 알림 봇

[인하대학교 의과대학 공지사항](https://medicine.inha.ac.kr/medicine/13793/subview.do)에
새 글이 올라오면 **제목과 링크를 텔레그램으로 보내주는** 프로그램입니다.

- 게시판을 주기적으로 확인해 **새로 올라온 글만** 알립니다.
- 이미 알린 글은 `state.json`에 기록해 **같은 글을 두 번 보내지 않습니다.**
- 첫 실행에는 알림을 보내지 않고 현재 목록만 기억합니다(수십 건이 한꺼번에 오는 것 방지).
- 서버에서 계속 띄워두거나(`watch`), cron·GitHub Actions로 주기 실행(`once`)할 수 있습니다.

## 1. 설치

```bash
git clone https://github.com/ysh030812-collab/gongji-indicator.git
cd gongji-indicator

python3 -m venv .venv
source .venv/bin/activate          # 윈도우: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather** 를 찾아 `/newbot` 을 보내고 안내대로 이름을 정합니다.
2. 받은 **토큰**(`123456789:AAE...` 형태)을 복사합니다.
3. **채팅 ID**를 확인합니다.
   - 개인으로 받기: **@userinfobot** 에게 아무 메시지나 보내면 `Id` 를 알려줍니다.
   - 그룹으로 받기: 봇을 그룹에 초대 → 그룹에 아무 메시지나 쓰기 →
     `https://api.telegram.org/bot<토큰>/getUpdates` 를 열어 `chat.id`(보통 음수)를 확인합니다.
4. **내 봇에게 먼저 말을 걸어야 합니다.** 봇 대화방에서 `/start` 를 한 번 눌러주세요.
   (텔레그램은 사용자가 먼저 시작하지 않은 대화에 봇이 메시지를 보내지 못하게 막습니다.)

## 3. 설정

`.env.example` 을 복사해 `.env` 를 만들고 토큰과 채팅 ID를 채웁니다.

```bash
cp .env.example .env
```

```ini
TELEGRAM_BOT_TOKEN=123456789:AAE...
TELEGRAM_CHAT_ID=987654321
```

나머지 항목(게시판 주소, 확인 주기 등)은 그대로 두면 기본값으로 동작합니다.
`.env` 는 `.gitignore` 에 들어 있어 실수로 커밋되지 않습니다.

## 4. 실행

먼저 설정이 맞는지 확인합니다.

```bash
python -m inha_notice_bot test     # 텔레그램으로 테스트 메시지 발송
python -m inha_notice_bot list     # 지금 게시판에 뭐가 보이는지 출력(전송 없음)
```

그다음 감시를 시작합니다.

```bash
python -m inha_notice_bot watch            # 10분마다 계속 확인 (Ctrl+C 로 종료)
python -m inha_notice_bot watch --interval 300   # 5분마다
python -m inha_notice_bot once             # 한 번만 확인하고 종료 (cron/CI용)
```

첫 실행은 **알림 없이** 현재 목록만 기록합니다. 그 뒤로 올라오는 글부터 알림이 옵니다.
지금 게시판에 있는 글도 한 번 받아보고 싶다면 `--notify-first-run` 을 붙이세요.

### 명령어와 옵션

| 명령어 | 설명 |
| --- | --- |
| `watch` | 주기적으로 확인 (기본값) |
| `once` | 한 번만 확인하고 종료 — cron, GitHub Actions용 |
| `list` | 현재 게시판 목록만 출력 (텔레그램 설정 없이도 동작) |
| `test` | 봇 토큰·채팅 ID 확인 후 테스트 메시지 발송 |

| 옵션 | 환경변수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `--url` | `BOARD_URL` | 의과대학 공지사항 | 감시할 게시판 주소 |
| `--state-file` | `STATE_FILE` | `state.json` | 이미 알린 글 기록 파일 |
| `--interval` | `CHECK_INTERVAL` | `600`(초) | `watch` 확인 주기 (최소 60) |
| `--max-per-run` | `MAX_PER_RUN` | `10` | 한 번에 보낼 최대 알림 수 (0이면 무제한) |
| `--notify-first-run` | `NOTIFY_FIRST_RUN` | `false` | 첫 실행에도 현재 목록을 모두 알림 |
| `--env-file` | — | `.env` | 설정 파일 경로 (`""` 이면 환경변수만 사용) |
| `-v` | — | — | 자세한 로그 |
| — | `BOARD_NAME` | 인하대 의과대학 공지 | 알림 머리말에 쓸 게시판 이름 |

> 다른 학과 게시판도 `--url` 만 바꾸면 그대로 쓸 수 있습니다(같은 형식의 게시판이면 동작).
> 게시판마다 `--state-file` 을 따로 지정하세요.

## 5. 계속 돌리기

### cron (10분마다)

```cron
*/10 * * * * cd /opt/inha-notice-bot && .venv/bin/python -m inha_notice_bot once >> bot.log 2>&1
```

### systemd

`deploy/inha-notice-bot.service` 를 `/etc/systemd/system/` 에 복사하고 경로·사용자를 맞춘 뒤:

```bash
sudo systemctl enable --now inha-notice-bot
sudo journalctl -u inha-notice-bot -f
```

### GitHub Actions (서버 없이)

`.github/workflows/check-notices.yml` 이 30분마다 `once` 를 실행합니다.
저장소 **Settings → Secrets and variables → Actions** 에
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 를 등록하면 바로 동작합니다.
알림 기록은 Actions 캐시에 저장됩니다.

> GitHub 무료 러너의 스케줄은 혼잡할 때 수십 분 밀릴 수 있습니다.
> 정확한 주기가 필요하면 서버에서 `watch` 로 돌리세요.

## 6. 알림 예시

```
📢 인하대 의과대학 공지

2026학년도 1학기 학사일정 안내      ← 누르면 공지 본문으로 이동

🗓 교학팀 · 2026.08.19 · 고정 공지
```

## 7. 구조

```
inha_notice_bot/
├── cli.py        명령행 진입점
├── config.py     .env / 환경변수 → 설정
├── scraper.py    게시판 HTML → 공지 목록
├── store.py      이미 알린 글 기록(state.json)
├── telegram.py   텔레그램 전송 및 메시지 포맷
├── runner.py     한 번 확인 / 반복 실행 루프
└── models.py     Notice 데이터 모델
```

게시판 파싱은 인하대가 쓰는 CMS의 표 구조(`_artclTdTitle` 등)를 먼저 보고,
스킨이 바뀌어 클래스가 달라지면 **게시글 링크가 들어있는 행**을 찾는 방식으로,
그것도 안 되면 **페이지의 게시글 링크 전체**를 훑는 방식으로 단계적으로 물러섭니다.

글의 식별자는 링크에서 뽑아냅니다(`.../500123/artclView.do` 의 `500123`,
또는 `?enc=...` 값의 해시). 제목이 아니라 글 번호를 쓰기 때문에
**제목이 수정돼도 같은 글로 인식**해 다시 알리지 않습니다.

## 8. 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

실제 사이트에 접속하지 않고, 저장된 게시판 HTML 샘플(`tests/fixtures/`)과
가짜 텔레그램 클라이언트로 전체 흐름(첫 실행 시딩 → 새 글만 알림 → 중복 방지 →
전송 실패 시 재시도)을 검증합니다.

## 9. 문제가 생기면

| 증상 | 확인할 것 |
| --- | --- |
| `chat not found` | 봇에게 `/start` 를 눌렀는지, `TELEGRAM_CHAT_ID` 가 맞는지 |
| `봇 토큰이 유효하지 않습니다` | 토큰 앞뒤 공백, 복사 누락 확인 |
| `공지를 한 건도 찾지 못했습니다` | `python -m inha_notice_bot list -v` 로 확인. 게시판 구조가 바뀌었다면 `scraper.py` 의 선택자를 조정해야 합니다 |
| 알림이 안 옴 | `state.json` 을 지우고 `--notify-first-run` 으로 한 번 돌려 전송 자체가 되는지 확인 |
| 알림이 한꺼번에 쏟아짐 | `state.json` 이 지워졌을 가능성. cron/CI에서 파일이 유지되는지 확인 |

## 참고

- 게시판에 과도한 요청을 보내지 않도록 확인 주기는 최소 60초로 제한되어 있습니다.
  기본값(10분)이면 충분합니다.
- 이 프로그램은 공개된 공지 목록만 읽으며 로그인이 필요한 정보는 다루지 않습니다.
