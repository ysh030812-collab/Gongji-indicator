"""환경변수에서 설정을 읽을 때의 정리 규칙."""

import pytest

from inha_notice_bot.config import load_config


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("8961027468: AAEabc", "8961027468:AAEabc"),   # 콜론 뒤 공백
        ("  8961027468:AAEabc  ", "8961027468:AAEabc"), # 앞뒤 공백
        ("8961027468:AAEabc\n", "8961027468:AAEabc"),   # 줄바꿈
    ],
)
def test_token_whitespace_is_removed(monkeypatch, raw, expected):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", raw)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert load_config(None).bot_token == expected


def test_chat_id_whitespace_is_removed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "8961027468:AAEabc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", " -100 123 ")
    assert load_config(None).chat_id == "-100123"
