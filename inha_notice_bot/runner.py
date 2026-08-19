"""한 번 확인 -> 새 공지 알림 -> 상태 저장, 그리고 반복 실행 루프."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

from .config import Config
from .models import Notice
from .scraper import ScrapeError, scrape
from .store import SeenStore
from .telegram import TelegramError, TelegramNotifier

logger = logging.getLogger(__name__)

# 연속 전송 시 텔레그램 속도 제한(초당 ~1건)에 걸리지 않도록 간격을 둔다.
SEND_GAP_SECONDS = 1.0


@dataclass
class CheckResult:
    """한 번 확인한 결과 요약."""

    total: int = 0
    new_notices: list[Notice] = field(default_factory=list)
    sent: list[Notice] = field(default_factory=list)
    skipped: int = 0          # max_per_run 때문에 알림 없이 넘긴 건수
    seeded: bool = False      # 첫 실행이라 알림 없이 기록만 한 경우
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def check_once(
    config: Config,
    *,
    notifier: TelegramNotifier | None = None,
    store: SeenStore | None = None,
    session: requests.Session | None = None,
) -> CheckResult:
    """게시판을 한 번 확인하고 새 공지를 텔레그램으로 보낸다."""
    result = CheckResult()
    store = store if store is not None else SeenStore(config.state_file)

    try:
        scraped = scrape(config.board_url, timeout=config.timeout, session=session)
    except ScrapeError as exc:
        logger.error("%s", exc)
        result.error = str(exc)
        return result

    result.total = len(scraped.notices)
    new_notices = store.filter_new(scraped.notices)   # 오래된 것부터
    result.new_notices = new_notices
    logger.info("공지 %d건 확인, 새 공지 %d건", result.total, len(new_notices))

    # 첫 실행(또는 상태 파일이 없어진 경우)에는 목록 전체가 '새 글'로 보인다.
    # 수십 건이 한꺼번에 날아가지 않도록 알림 없이 현재 목록만 기억한다.
    if not store.initialized and not config.notify_first_run:
        store.mark_all(scraped.notices)
        store.save()
        result.seeded = True
        logger.info("첫 실행입니다. 현재 공지 %d건을 알림 없이 기록했습니다.", result.total)
        return result

    if not new_notices:
        store.save()   # last_run 갱신
        return result

    to_send = new_notices
    if config.max_per_run > 0 and len(new_notices) > config.max_per_run:
        # 오래된 것부터 정렬돼 있으므로, 넘칠 때는 최신 max_per_run 건만 보낸다.
        result.skipped = len(new_notices) - config.max_per_run
        to_send = new_notices[-config.max_per_run :]
        logger.warning("새 공지가 %d건이라 최신 %d건만 보냅니다.", len(new_notices), config.max_per_run)
        # 보내지 않고 건너뛴 글도 다시 알리지 않도록 기록만 해 둔다.
        for notice in new_notices[: result.skipped]:
            store.mark(notice)

    if config.dry_run or notifier is None:
        for notice in to_send:
            logger.info("[dry-run] 전송 대상: %s", notice.title)
        result.sent = list(to_send)
        return result

    try:
        for index, notice in enumerate(to_send):
            notifier.send_notice(notice, config.board_name)
            store.mark(notice)
            result.sent.append(notice)
            logger.info("전송 완료: %s", notice.title)
            if index < len(to_send) - 1:
                time.sleep(SEND_GAP_SECONDS)
    except TelegramError as exc:
        # 보낸 것까지는 상태에 남기고 나머지는 다음 실행에서 재시도한다.
        logger.error("전송 중단: %s", exc)
        result.error = str(exc)
    finally:
        store.save()

    return result


def run_forever(
    config: Config,
    *,
    notifier: TelegramNotifier | None = None,
    max_iterations: int | None = None,
    sleep_func=time.sleep,
) -> None:
    """interval 초마다 반복해서 확인한다. Ctrl+C 로 종료."""
    logger.info(
        "감시 시작: %s (%d초 주기)", config.board_url, config.interval
    )
    session = requests.Session()
    iteration = 0
    consecutive_errors = 0

    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            result = check_once(config, notifier=notifier, session=session)
            consecutive_errors = 0 if result.ok else consecutive_errors + 1
        except Exception:  # 루프는 어떤 예외에도 죽지 않아야 한다.
            logger.exception("확인 중 예상치 못한 오류")
            consecutive_errors += 1

        # 오류가 이어지면 간격을 늘려 서버와 API를 덜 두드린다(최대 8배).
        delay = config.interval * min(2 ** consecutive_errors, 8) if consecutive_errors else config.interval
        if consecutive_errors:
            logger.warning("연속 오류 %d회, %d초 후 재시도합니다.", consecutive_errors, delay)

        if max_iterations is not None and iteration >= max_iterations:
            break
        sleep_func(delay)
