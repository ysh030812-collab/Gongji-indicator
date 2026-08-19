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
