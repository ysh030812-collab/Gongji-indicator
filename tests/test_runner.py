"""전체 흐름 테스트: 첫 실행 시딩, 새 글만 알림, 중복 방지."""

import pytest
from conftest import fixture

from inha_notice_bot import runner
from inha_notice_bot.config import Config
from inha_notice_bot.models import Notice, ScrapeResult
from inha_notice_bot.runner import check_once
from inha_notice_bot.scraper import ScrapeError, parse_notices
from inha_notice_bot.store import SeenStore
from inha_notice_bot.telegram import TelegramError

BASE = "https://medicine.inha.ac.kr/medicine/13793/subview.do"


class FakeNotifier:
    """보낸 공지를 기억하는 가짜 텔레그램 클라이언트."""

    def __init__(self, fail_at=None):
        self.sent = []
        self.fail_at = fail_at

    def send_notice(self, notice, board_name="공지"):
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise TelegramError("전송 실패")
        self.sent.append(notice)
        return {"ok": True}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def make_config(tmp_path, **kwargs):
    return Config(
        bot_token="TOKEN",
        chat_id="12345",
        board_url=BASE,
        state_file=str(tmp_path / "state.json"),
        **kwargs,
    )


def patch_board(monkeypatch, notices):
    def fake_scrape(url, timeout=15.0, session=None):
        return ScrapeResult(notices=list(notices), source_url=url)

    monkeypatch.setattr(runner, "scrape", fake_scrape)


def board(*uids):
    """게시판 목록(최신이 앞)."""
    return [Notice(uid=uid, title=f"공지 {uid}", url=f"{BASE}?n={uid}") for uid in uids]


def test_first_run_seeds_without_notifying(tmp_path, monkeypatch):
    patch_board(monkeypatch, parse_notices(fixture("board_list.html"), BASE))
    notifier = FakeNotifier()

    result = check_once(make_config(tmp_path), notifier=notifier)

    assert result.seeded is True
    assert notifier.sent == []
    assert result.total == 3


def test_second_run_notifies_only_new_notices(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    patch_board(monkeypatch, board("3", "2", "1"))
    notifier = FakeNotifier()
    check_once(config, notifier=notifier)          # 시딩

    patch_board(monkeypatch, board("5", "4", "3", "2", "1"))
    result = check_once(config, notifier=notifier)

    # 오래된 글부터 순서대로 전송된다.
    assert [n.uid for n in notifier.sent] == ["4", "5"]
    assert len(result.sent) == 2


def test_no_duplicate_notification_across_runs(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    patch_board(monkeypatch, board("1"))
    notifier = FakeNotifier()
    check_once(config, notifier=notifier)

    patch_board(monkeypatch, board("2", "1"))
    check_once(config, notifier=notifier)
    check_once(config, notifier=notifier)          # 같은 목록 재확인
    check_once(config, notifier=notifier)

    assert [n.uid for n in notifier.sent] == ["2"]


def test_first_run_notifies_when_opted_in(tmp_path, monkeypatch):
    patch_board(monkeypatch, board("2", "1"))
    notifier = FakeNotifier()

    result = check_once(make_config(tmp_path, notify_first_run=True), notifier=notifier)

    assert [n.uid for n in notifier.sent] == ["1", "2"]
    assert result.seeded is False


def test_max_per_run_caps_burst_and_still_records_rest(tmp_path, monkeypatch):
    config = make_config(tmp_path, max_per_run=2)
    patch_board(monkeypatch, board("1"))
    notifier = FakeNotifier()
    check_once(config, notifier=notifier)          # 시딩

    patch_board(monkeypatch, board("6", "5", "4", "3", "2", "1"))
    result = check_once(config, notifier=notifier)

    assert [n.uid for n in notifier.sent] == ["5", "6"]   # 최신 2건만
    assert result.skipped == 3

    # 건너뛴 글도 기록돼 다음 실행에서 다시 알리지 않는다.
    check_once(config, notifier=notifier)
    assert [n.uid for n in notifier.sent] == ["5", "6"]


def test_send_failure_keeps_unsent_notices_for_next_run(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    patch_board(monkeypatch, board("1"))
    check_once(config, notifier=FakeNotifier())    # 시딩

    patch_board(monkeypatch, board("4", "3", "2", "1"))
    failing = FakeNotifier(fail_at=1)              # 2건 중 두 번째에서 실패
    result = check_once(config, notifier=failing)

    assert result.ok is False
    assert [n.uid for n in failing.sent] == ["2"]

    # 실패한 3, 4 는 다음 실행에서 다시 시도된다.
    retry = FakeNotifier()
    check_once(config, notifier=retry)
    assert [n.uid for n in retry.sent] == ["3", "4"]


def test_scrape_error_is_reported_and_state_untouched(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    patch_board(monkeypatch, board("1"))
    check_once(config, notifier=FakeNotifier())

    def boom(url, timeout=15.0, session=None):
        raise ScrapeError("접속 실패")

    monkeypatch.setattr(runner, "scrape", boom)
    result = check_once(config, notifier=FakeNotifier())

    assert result.ok is False
    assert "접속 실패" in result.error
    assert SeenStore(config.state_file).initialized is True


def test_dry_run_sends_nothing(tmp_path, monkeypatch):
    config = make_config(tmp_path, dry_run=True, notify_first_run=True)
    patch_board(monkeypatch, board("2", "1"))
    notifier = FakeNotifier()

    result = check_once(config, notifier=notifier)

    assert notifier.sent == []
    assert len(result.sent) == 2


def test_run_forever_stops_after_max_iterations(tmp_path, monkeypatch):
    config = make_config(tmp_path, interval=60)
    patch_board(monkeypatch, board("1"))
    slept = []

    runner.run_forever(
        config, notifier=FakeNotifier(), max_iterations=3, sleep_func=slept.append
    )

    assert slept == [60, 60]   # 마지막 반복 뒤에는 쉬지 않는다
