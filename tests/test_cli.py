"""명령행 인자 처리 테스트."""

import pytest

from inha_notice_bot.cli import _apply_overrides, build_parser, main
from inha_notice_bot.config import Config


def parse(argv):
    return build_parser().parse_args(argv)


def base_config():
    return Config(bot_token="T", chat_id="C")


def test_options_work_after_subcommand():
    # 사용자가 자연스럽게 쓰는 순서: `once --url ...`
    args = parse(["once", "--url", "https://example.com/b", "--interval", "120"])
    config = _apply_overrides(base_config(), args)
    assert args.command == "once"
    assert config.board_url == "https://example.com/b"
    assert config.interval == 120


def test_options_work_before_subcommand():
    args = parse(["--url", "https://example.com/b", "once"])
    assert _apply_overrides(base_config(), args).board_url == "https://example.com/b"


def test_subcommand_does_not_erase_earlier_option():
    # 서브커맨드 파서가 앞서 준 값을 None 으로 덮어쓰면 안 된다.
    args = parse(["--interval", "300", "watch"])
    assert _apply_overrides(base_config(), args).interval == 300


def test_defaults_are_kept_when_no_flags():
    default = Config(bot_token="T", chat_id="C")
    applied = _apply_overrides(base_config(), parse(["once"]))
    assert applied.board_url == default.board_url
    assert applied.interval == default.interval
    assert applied.notify_first_run is False


def test_command_defaults_to_watch():
    assert parse([]).command is None   # main() 에서 watch 로 해석한다


def test_missing_token_exits_with_config_error(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["once", "--env-file", ""]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_too_short_interval_rejected(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    assert main(["watch", "--env-file", "", "--interval", "5"]) == 2
    assert "60초" in capsys.readouterr().err


def test_list_command_needs_no_telegram_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    called = {}

    def fake_list(config, send_to_telegram=False):
        called["url"] = config.board_url
        called["telegram"] = send_to_telegram
        return 0

    monkeypatch.setattr("inha_notice_bot.cli.cmd_list", fake_list)
    assert main(["list", "--env-file", "", "--url", "https://example.com/b"]) == 0
    assert called["url"] == "https://example.com/b"
    assert called["telegram"] is False


def test_list_with_telegram_flag_requires_credentials(monkeypatch, capsys):
    # --telegram 을 쓰면 전송을 해야 하므로 토큰 검사가 살아나야 한다.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["list", "--env-file", "", "--telegram"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_list_passes_telegram_flag_through(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    called = {}

    def fake_list(config, send_to_telegram=False):
        called["telegram"] = send_to_telegram
        return 0

    monkeypatch.setattr("inha_notice_bot.cli.cmd_list", fake_list)
    assert main(["list", "--env-file", "", "--telegram"]) == 0
    assert called["telegram"] is True


# --- Actions 요약 페이지 출력 --------------------------------------------
# 로그를 뒤지지 않아도(특히 휴대폰에서) 결과가 보이도록 요약에도 남긴다.

def test_chats_writes_ids_to_step_summary(monkeypatch, tmp_path, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    class FakeNotifier:
        def __init__(self, *a, **k):
            pass

        def discover_chats(self):
            return [
                {"id": "7937362217", "type": "private", "name": "혁 윤"},
                {"id": "-1001234567890", "type": "supergroup", "name": "의대방"},
            ]

    monkeypatch.setattr("inha_notice_bot.cli.TelegramNotifier", FakeNotifier)
    from inha_notice_bot.cli import cmd_chats
    from inha_notice_bot.config import Config

    assert cmd_chats(Config(bot_token="123:AAE", chat_id="")) == 0

    written = summary.read_text(encoding="utf-8")
    assert "7937362217" in written
    assert "-1001234567890" in written
    assert "TELEGRAM_CHAT_ID" in written
    # 터미널 출력도 그대로 유지된다.
    assert "7937362217" in capsys.readouterr().out


def test_chats_summary_explains_empty_result(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    class EmptyNotifier:
        def __init__(self, *a, **k):
            pass

        def discover_chats(self):
            return []

    monkeypatch.setattr("inha_notice_bot.cli.TelegramNotifier", EmptyNotifier)
    from inha_notice_bot.cli import cmd_chats
    from inha_notice_bot.config import Config

    assert cmd_chats(Config(bot_token="123:AAE", chat_id="")) == 1
    assert "봇 대화방" in summary.read_text(encoding="utf-8")


def test_summary_is_skipped_outside_actions(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    class FakeNotifier:
        def __init__(self, *a, **k):
            pass

        def discover_chats(self):
            return [{"id": "1", "type": "private", "name": "가"}]

    monkeypatch.setattr("inha_notice_bot.cli.TelegramNotifier", FakeNotifier)
    from inha_notice_bot.cli import cmd_chats
    from inha_notice_bot.config import Config

    # 요약 파일이 없어도 그냥 동작해야 한다.
    assert cmd_chats(Config(bot_token="123:AAE", chat_id="")) == 0
    assert "TELEGRAM_CHAT_ID = 1" in capsys.readouterr().out
