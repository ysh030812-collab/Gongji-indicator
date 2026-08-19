"""메시지 포맷 및 전송 테스트(HTTP는 가짜 세션으로 대체)."""

import json

import pytest
import requests

from inha_notice_bot.models import Notice
from inha_notice_bot.telegram import (
    TelegramError,
    TelegramNotifier,
    format_listing,
    format_notice,
)


VALID_TOKEN = "123456789:AAEexampleTokenForTests"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        return self.responses.pop(0)


def test_format_escapes_html_special_characters():
    notice = Notice(uid="1", title="의학과 <특강> & 안내", url="https://x/y?a=1&b=2")
    text = format_notice(notice)
    assert "&lt;특강&gt; &amp; 안내" in text
    assert 'href="https://x/y?a=1&amp;b=2"' in text
    # 원문 제목의 꺾쇠가 태그로 새어나가지 않아야 한다.
    assert "<특강>" not in text


def test_format_includes_title_writer_and_date():
    notice = Notice(uid="1", title="학사일정", writer="교학팀", date="2026.08.19")
    text = format_notice(notice, board_name="테스트 게시판")
    assert "학사일정" in text
    assert "교학팀" in text
    assert "2026.08.19" in text
    assert "테스트 게시판" in text


def test_format_truncates_overly_long_title():
    notice = Notice(uid="1", title="가" * 6000)
    assert len(format_notice(notice)) <= 4096


def test_send_text_posts_expected_payload():
    session = FakeSession([FakeResponse(200, {"ok": True, "result": {"message_id": 1}})])
    notifier = TelegramNotifier(VALID_TOKEN, "12345", session=session)
    notifier.send_text("안녕")

    url, data = session.calls[0]
    assert url.endswith(f"/bot{VALID_TOKEN}/sendMessage")
    assert data["chat_id"] == "12345"
    assert data["text"] == "안녕"
    assert data["parse_mode"] == "HTML"


def test_client_error_is_not_retried():
    session = FakeSession(
        [FakeResponse(400, {"ok": False, "description": "chat not found"})]
    )
    notifier = TelegramNotifier(VALID_TOKEN, "12345", session=session, max_retries=3)
    with pytest.raises(TelegramError, match="chat not found"):
        notifier.send_text("안녕")
    assert len(session.calls) == 1


def test_server_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("inha_notice_bot.telegram.time.sleep", lambda _s: None)
    session = FakeSession(
        [
            FakeResponse(500, {"ok": False, "description": "boom"}),
            FakeResponse(200, {"ok": True, "result": {"message_id": 2}}),
        ]
    )
    notifier = TelegramNotifier(VALID_TOKEN, "12345", session=session, max_retries=3)
    assert notifier.send_text("안녕")["ok"] is True
    assert len(session.calls) == 2


def test_missing_credentials_rejected():
    with pytest.raises(TelegramError):
        TelegramNotifier("", "12345")
    with pytest.raises(TelegramError):
        TelegramNotifier(VALID_TOKEN, "")


def test_format_listing_numbers_and_escapes_entries():
    notices = [
        Notice(uid="1", title="첫 번째 <공지>", url="https://x/1", date="2026.08.19", pinned=True),
        Notice(uid="2", title="두 번째 공지", url="https://x/2", date="2026.08.18"),
    ]
    text = format_listing(notices, "https://x/board")
    assert "공지 2건" in text
    assert "1. 📌" in text
    assert "&lt;공지&gt;" in text
    assert "https://x/board" in text


def test_format_listing_caps_entries_and_notes_remainder():
    notices = [Notice(uid=str(i), title=f"공지 {i}") for i in range(30)]
    text = format_listing(notices, "https://x/board", limit=5)
    assert "외 25건" in text
    assert len(text) <= 4096


def test_format_listing_stays_within_message_limit():
    notices = [Notice(uid=str(i), title="가" * 200, url=f"https://x/{i}") for i in range(50)]
    assert len(format_listing(notices, "https://x/board")) <= 4096


# --- 자격 증명 형식 검사 -------------------------------------------------
# 실제로 겪은 사고: BotFather 메시지에서 복사할 때 콜론 뒤에 공백이 섞여
# '123: AAE...' 가 되면 텔레그램이 404만 돌려줘 원인을 찾기 어려웠다.

@pytest.mark.parametrize(
    "token",
    [
        "8961027468: AAEabcdef",   # 콜론 뒤 공백
        "8961027468:AAE abcdef",   # 중간 공백
        "8961027468:AAE\nabc",     # 줄바꿈
        "AAEabcdef",               # 숫자:토큰 형태가 아님
        "8961027468",              # 콜론 없음
    ],
)
def test_malformed_token_rejected_before_sending(token):
    with pytest.raises(TelegramError, match="토큰 형식"):
        TelegramNotifier(token, "12345")


@pytest.mark.parametrize("chat_id", ["-1001234567890", "12345", "@my_channel"])
def test_valid_chat_ids_accepted(chat_id):
    assert TelegramNotifier(VALID_TOKEN, chat_id).chat_id == chat_id


@pytest.mark.parametrize("chat_id", ["abc", "12 345", "123abc"])
def test_malformed_chat_id_rejected(chat_id):
    with pytest.raises(TelegramError, match="채팅 ID 형식"):
        TelegramNotifier(VALID_TOKEN, chat_id)


def test_404_explains_that_the_token_is_wrong():
    session = FakeSession([FakeResponse(404, {"ok": False, "description": "Not Found"})])
    notifier = TelegramNotifier(VALID_TOKEN, "12345", session=session)
    with pytest.raises(TelegramError, match="토큰이 잘못"):
        notifier.send_text("안녕")


def test_403_mentions_pressing_start():
    session = FakeSession([FakeResponse(403, {"ok": False, "description": "Forbidden"})])
    notifier = TelegramNotifier(VALID_TOKEN, "12345", session=session)
    with pytest.raises(TelegramError, match="/start"):
        notifier.send_text("안녕")


# --- 토큰이 로그/오류로 새지 않아야 한다 ----------------------------------
# 실제로 겪은 사고: requests 예외 메시지에 요청 URL(=토큰 포함)이 들어 있어
# CI 로그에 봇 토큰이 그대로 남았다.

SECRET_TOKEN = "8961027468:AAEsecretValueThatMustNotLeak"


def test_network_error_message_hides_token():
    class ExplodingSession:
        def post(self, url, data=None, timeout=None):
            # requests 는 실패 URL을 예외 메시지에 그대로 담는다.
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    notifier = TelegramNotifier(
        SECRET_TOKEN, "12345", session=ExplodingSession(), max_retries=1
    )
    with pytest.raises(TelegramError) as excinfo:
        notifier.send_text("안녕")

    assert "AAEsecretValueThatMustNotLeak" not in str(excinfo.value)
    assert "<봇토큰>" in str(excinfo.value)


def test_api_error_description_hides_token():
    session = FakeSession(
        [FakeResponse(500, {"ok": False, "description": f"failed at /bot{SECRET_TOKEN}/x"})]
    )
    notifier = TelegramNotifier(SECRET_TOKEN, "12345", session=session, max_retries=1)
    with pytest.raises(TelegramError) as excinfo:
        notifier.send_text("안녕")
    assert "AAEsecretValueThatMustNotLeak" not in str(excinfo.value)


def test_warning_logs_hide_token(caplog):
    class ExplodingSession:
        def post(self, url, data=None, timeout=None):
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    notifier = TelegramNotifier(
        SECRET_TOKEN, "12345", session=ExplodingSession(), max_retries=1
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(TelegramError):
            notifier.send_text("안녕")
    assert "AAEsecretValueThatMustNotLeak" not in caplog.text
