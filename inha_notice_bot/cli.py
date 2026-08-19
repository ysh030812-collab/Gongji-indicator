"""명령행 진입점."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import Config, ConfigError, load_config
from .runner import check_once, run_forever
from .scraper import ScrapeError, scrape
from .telegram import TelegramError, TelegramNotifier, format_listing


def _common_options(parser: argparse.ArgumentParser) -> None:
    """전역 옵션. 서브커맨드 앞뒤 어디에 써도 먹도록 부모 파서로도 쓴다.

    기본값을 SUPPRESS 로 두어, 서브커맨드 파서가 앞에서 지정한 값을
    None 으로 덮어쓰지 않게 한다.
    """
    parser.add_argument("--url", default=argparse.SUPPRESS, help="감시할 게시판 주소 (기본: 의과대학 공지사항)")
    parser.add_argument("--state-file", default=argparse.SUPPRESS, help="이미 알린 글을 기록할 파일 (기본: state.json)")
    parser.add_argument("--interval", type=int, default=argparse.SUPPRESS, help="watch 모드 확인 주기(초, 최소 60)")
    parser.add_argument("--max-per-run", type=int, default=argparse.SUPPRESS, help="한 번에 보낼 최대 알림 수 (0이면 무제한)")
    parser.add_argument("--env-file", default=argparse.SUPPRESS, help="설정을 읽을 .env 경로 (기본: .env)")
    parser.add_argument(
        "--notify-first-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="첫 실행에도 현재 목록을 모두 알린다(기본은 알림 없이 기록만).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="자세한 로그")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _common_options(common)

    parser = argparse.ArgumentParser(
        prog="inha-notice-bot",
        parents=[common],
        description="인하대 의과대학 공지사항에 새 글이 올라오면 텔레그램으로 제목을 보냅니다.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("once", parents=[common], help="한 번만 확인하고 종료 (cron/GitHub Actions용)")
    sub.add_parser("watch", parents=[common], help="주기적으로 계속 확인 (기본)")
    list_parser = sub.add_parser(
        "list", parents=[common], help="현재 게시판 목록만 출력 (파싱 점검용)"
    )
    list_parser.add_argument(
        "--telegram",
        action="store_true",
        default=argparse.SUPPRESS,
        help="목록을 텔레그램으로도 보낸다 (PC 없이 휴대폰에서 확인할 때).",
    )
    sub.add_parser("test", parents=[common], help="텔레그램 설정 확인 후 테스트 메시지 전송")
    sub.add_parser(
        "chats",
        parents=[common],
        help="봇에게 말을 건 채팅들의 ID를 찾아준다 (chat not found 해결용)",
    )
    return parser


def _apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """명령행으로 준 값이 있으면 설정을 덮어쓴다."""
    url = getattr(args, "url", None)
    if url:
        config.board_url = url
    state_file = getattr(args, "state_file", None)
    if state_file:
        config.state_file = state_file
    interval = getattr(args, "interval", None)
    if interval is not None:
        config.interval = interval
    max_per_run = getattr(args, "max_per_run", None)
    if max_per_run is not None:
        config.max_per_run = max_per_run
    if getattr(args, "notify_first_run", False):
        config.notify_first_run = True
    return config


def _setup_logging(verbose: bool) -> None:
    # 파이프로 연결되면 stdout 이 통째로 버퍼링돼, stderr 로 나가는 오류 메시지가
    # 출력 한가운데 끼어 보인다(CI 로그에서 특히 헷갈린다). 줄 단위로 내보낸다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(line_buffering=True)

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # urllib3 는 DEBUG 로 요청 URL을 통째로 찍는데, 텔레그램 API 주소에는
    # 봇 토큰이 들어 있다. -v 로 돌린 로그(특히 CI 로그)에 토큰이 남지 않도록 막는다.
    for noisy in ("urllib3", "requests", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_list(config: Config, send_to_telegram: bool = False) -> int:
    """게시판 목록을 출력한다. 파싱이 잘 되는지 확인할 때 쓴다."""
    try:
        result = scrape(config.board_url, timeout=config.timeout)
    except ScrapeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        if send_to_telegram:
            _try_report_failure(config, str(exc))
        return 1

    print(f"{config.board_url} — 공지 {len(result)}건\n")
    for notice in result:
        mark = "[공지] " if notice.pinned else ""
        meta = " · ".join(x for x in (notice.writer, notice.date) if x)
        print(f"{mark}{notice.title}")
        print(f"    uid={notice.uid}  {meta}")
        if notice.url:
            print(f"    {notice.url}")

    if send_to_telegram:
        notifier = TelegramNotifier(config.bot_token, config.chat_id, timeout=config.timeout)
        try:
            notifier.send_text(format_listing(result.notices, config.board_url))
        except TelegramError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            if "찾을 수 없" in str(exc):
                _suggest_chat_ids(config)
            return 1
        print(f"\n목록을 chat_id={config.chat_id} 로 보냈습니다.")
    return 0


def _try_report_failure(config: Config, message: str) -> None:
    """파싱 실패도 텔레그램으로 알려준다(폰에서 확인할 수 있게)."""
    try:
        notifier = TelegramNotifier(config.bot_token, config.chat_id, timeout=config.timeout)
        notifier.send_text(
            "⚠️ <b>게시판 확인 실패</b>\n\n" + __import__("html").escape(message)
        )
    except TelegramError as exc:
        print(f"(실패 내용을 텔레그램으로 보내지도 못했습니다: {exc})", file=sys.stderr)


def cmd_chats(config: Config) -> int:
    """봇이 받은 대화 목록을 보여준다. chat_id 를 확인할 때 쓴다."""
    notifier = TelegramNotifier(config.bot_token, config.chat_id or "0", timeout=config.timeout)
    chats = notifier.discover_chats()
    if not chats:
        print(
            "봇이 받은 메시지가 없습니다.\n"
            "  1) 텔레그램에서 봇 대화방을 열고 /start 또는 아무 메시지나 보내세요.\n"
            "     (그룹이라면 봇을 초대한 뒤 그룹에 아무 메시지나 쓰세요)\n"
            "  2) 그 직후에 이 명령을 다시 실행하세요.",
            file=sys.stderr,
        )
        return 1

    print("봇에게 말을 건 채팅 목록입니다. 아래 ID 를 TELEGRAM_CHAT_ID 에 넣으세요.\n")
    for chat in chats:
        label = chat["name"] or "(이름 없음)"
        kind = {"private": "개인", "group": "그룹", "supergroup": "그룹", "channel": "채널"}.get(
            chat["type"], chat["type"]
        )
        print(f"  TELEGRAM_CHAT_ID = {chat['id']}    ← {kind} · {label}")
    return 0


def _suggest_chat_ids(config: Config) -> None:
    """전송이 'chat not found' 로 실패했을 때 올바른 ID 후보를 알려준다."""
    try:
        notifier = TelegramNotifier(config.bot_token, config.chat_id or "0", timeout=config.timeout)
        chats = notifier.discover_chats()
    except TelegramError:
        return
    if not chats:
        print(
            "\n힌트: 봇 대화방에서 /start 를 누른 뒤 'chats' 명령을 실행하면 "
            "올바른 채팅 ID를 찾아드립니다.",
            file=sys.stderr,
        )
        return
    print("\n힌트: 봇이 실제로 받은 대화의 ID 는 다음과 같습니다.", file=sys.stderr)
    for chat in chats:
        print(f"  {chat['id']}  ({chat['type']} · {chat['name'] or '이름 없음'})", file=sys.stderr)


def cmd_test(config: Config) -> int:
    """토큰/채팅 ID가 맞는지 확인하고 테스트 메시지를 보낸다."""
    try:
        notifier = TelegramNotifier(config.bot_token, config.chat_id, timeout=config.timeout)
        username = notifier.check_auth()
        print(f"봇 연결 확인: @{username}")
        notifier.send_text(
            "✅ <b>인하대 의과대학 공지 알림 봇</b>\n\n설정이 정상입니다. "
            f"새 공지가 올라오면 여기로 알려드릴게요.\n\n감시 중: {config.board_url}"
        )
        print(f"테스트 메시지를 chat_id={config.chat_id} 로 보냈습니다.")
    except TelegramError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        if "찾을 수 없" in str(exc):
            _suggest_chat_ids(config)
        return 1
    return 0


def cmd_once(config: Config) -> int:
    notifier = TelegramNotifier(config.bot_token, config.chat_id, timeout=config.timeout)
    result = check_once(config, notifier=notifier)
    if result.seeded:
        print(f"첫 실행: 현재 공지 {result.total}건을 알림 없이 기록했습니다.")
    elif result.sent:
        print(f"새 공지 {len(result.sent)}건을 전송했습니다.")
        for notice in result.sent:
            print(f"  - {notice.title}")
    elif result.ok:
        print(f"새 공지가 없습니다. (목록 {result.total}건 확인)")
    if result.skipped:
        print(f"  ({result.skipped}건은 한 번에 보낼 수 있는 개수를 넘어 기록만 했습니다)")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    command = args.command or "watch"
    try:
        env_file = getattr(args, "env_file", ".env")
        config = load_config(env_file)
        config = _apply_overrides(config, args)
        if command == "list":
            # 그냥 출력만 할 때는 토큰이 없어도 동작하게 한다.
            config.dry_run = not getattr(args, "telegram", False)
        if command == "chats":
            # 찾으려는 대상이 chat_id 이므로 chat_id 검사는 건너뛴다.
            config.chat_id = config.chat_id or "0"
        config.validate()
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    try:
        if command == "list":
            return cmd_list(config, send_to_telegram=getattr(args, "telegram", False))
        if command == "test":
            return cmd_test(config)
        if command == "chats":
            return cmd_chats(config)
        if command == "once":
            return cmd_once(config)

        notifier = TelegramNotifier(config.bot_token, config.chat_id, timeout=config.timeout)
        run_forever(config, notifier=notifier)
        return 0
    except TelegramError as exc:
        print(f"텔레그램 오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n종료합니다.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
