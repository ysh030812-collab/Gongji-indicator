"""게시판 HTML 파싱 테스트."""

from conftest import fixture

from inha_notice_bot.scraper import extract_uid, parse_notices

BASE = "https://medicine.inha.ac.kr/medicine/13793/subview.do"


def test_parses_all_rows_in_order():
    notices = parse_notices(fixture("board_list.html"), BASE)
    assert [n.title for n in notices] == [
        "2026학년도 1학기 학사일정 안내",
        "의학과 & 의예과 <진로특강> 신청 안내",
        "2026년 하계 봉사활동 결과 보고",
    ]


def test_title_excludes_new_badge():
    first = parse_notices(fixture("board_list.html"), BASE)[0]
    assert "NEW" not in first.title


def test_metadata_and_absolute_url():
    notices = parse_notices(fixture("board_list.html"), BASE)
    second = notices[1]
    assert second.writer == "학생지원팀"
    assert second.date == "2026.08.18"
    assert second.number == "482"
    assert second.url == "https://medicine.inha.ac.kr/bbs/medicine/1234/500122/artclView.do"
    assert second.pinned is False


def test_pinned_row_detected():
    first = parse_notices(fixture("board_list.html"), BASE)[0]
    assert first.pinned is True


def test_uid_comes_from_article_number():
    notices = parse_notices(fixture("board_list.html"), BASE)
    assert notices[0].uid == "500123"
    assert notices[1].uid == "500122"


def test_enc_style_link_gets_stable_uid():
    notices = parse_notices(fixture("board_list.html"), BASE)
    third = notices[2]
    assert third.uid.startswith("enc-")
    # 같은 HTML을 다시 파싱해도 uid가 같아야 중복 알림이 없다.
    again = parse_notices(fixture("board_list.html"), BASE)[2]
    assert third.uid == again.uid


def test_uids_are_unique():
    notices = parse_notices(fixture("board_list.html"), BASE)
    assert len({n.uid for n in notices}) == len(notices)


def test_fallback_parses_non_table_skin():
    notices = parse_notices(fixture("board_list_alt.html"), BASE)
    titles = [n.title for n in notices]
    assert titles == ["새 스킨 공지 제목 하나", "새 스킨 공지 제목 둘"]
    # 게시글이 아닌 '목록으로' 링크는 걸러진다.
    assert "목록으로" not in titles


def test_empty_page_returns_nothing():
    assert parse_notices(fixture("board_empty.html"), BASE) == []


def test_extract_uid_variants():
    assert extract_uid("/bbs/medicine/1234/500123/artclView.do", "t") == "500123"
    assert extract_uid("/x/view.do?artclNo=777", "t") == "777"
    assert extract_uid("?enc=AAA", "t").startswith("enc-")
    assert extract_uid("/some/other/page", "t").startswith("url-")
    assert extract_uid("", "제목") == extract_uid("", " 제목 ")


# --- 일시적인 네트워크 오류 재시도 ---------------------------------------
# 실제 운영에서 학교 서버 응답이 느려 read timeout 으로 실행이 실패했다.
# 한 번의 시간 초과로 그 회차를 통째로 날리지 않도록 다시 시도한다.

import pytest
import requests

from inha_notice_bot.scraper import ScrapeError, fetch_html


class FakeResponse:
    def __init__(self, text="<html></html>", status=200):
        self.text = text
        self.status_code = status
        self.encoding = "utf-8"
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code}")
            error.response = self
            raise error


class ScriptedSession:
    """미리 정해둔 순서대로 응답하거나 예외를 던지는 세션."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, timeout=None, headers=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_timeout_is_retried_then_succeeds():
    session = ScriptedSession(
        requests.Timeout("read timed out"),
        FakeResponse("<html>ok</html>"),
    )
    html = fetch_html("https://x/board", session=session, sleep_func=lambda _s: None)

    assert html == "<html>ok</html>"
    assert session.calls == 2


def test_connection_error_is_retried():
    session = ScriptedSession(
        requests.ConnectionError("reset"),
        requests.ConnectionError("reset"),
        FakeResponse("<html>ok</html>"),
    )
    fetch_html("https://x/board", session=session, sleep_func=lambda _s: None)
    assert session.calls == 3


def test_gives_up_after_all_retries():
    session = ScriptedSession(*[requests.Timeout("t")] * 3)
    with pytest.raises(ScrapeError, match="가져오지 못했습니다"):
        fetch_html("https://x/board", session=session, retries=3, sleep_func=lambda _s: None)
    assert session.calls == 3


def test_server_error_is_retried():
    session = ScriptedSession(FakeResponse(status=503), FakeResponse("<html>ok</html>"))
    fetch_html("https://x/board", session=session, sleep_func=lambda _s: None)
    assert session.calls == 2


def test_not_found_is_not_retried():
    # 404 는 몇 번을 시도해도 같으므로 곧바로 포기한다.
    session = ScriptedSession(FakeResponse(status=404))
    with pytest.raises(ScrapeError):
        fetch_html("https://x/board", session=session, sleep_func=lambda _s: None)
    assert session.calls == 1


def test_retry_delays_grow():
    delays = []
    session = ScriptedSession(
        requests.Timeout("t"), requests.Timeout("t"), FakeResponse("<html>ok</html>")
    )
    fetch_html("https://x/board", session=session, sleep_func=delays.append)
    assert delays == [2, 4]
