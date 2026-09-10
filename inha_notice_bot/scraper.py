"""공지 목록 페이지를 받아 Notice 목록으로 파싱한다.

인하대 사이트가 쓰는 CMS(subview.do 게시판)는 목록을 아래와 같은 표로 그린다.

    <tr>
      <td class="_artclTdNum td-num">1234</td>
      <td class="_artclTdTitle td-subject">
        <a href="/bbs/medicine/1234/56789/artclView.do">제목</a>
      </td>
      <td class="_artclTdWriter td-write">작성자</td>
      <td class="_artclTdRdate td-date">2026.08.19</td>
    </tr>

다만 스킨/템플릿이 바뀌면 클래스 이름이 달라질 수 있어서,
1) 전용 클래스 선택자 -> 2) 게시글 링크가 들어있는 표 행 -> 3) 페이지 전체의
게시글 링크 순으로 단계적으로 물러서며 파싱한다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import Notice, ScrapeResult

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 게시글 본문으로 가는 링크인지 판별한다.
_ARTICLE_HREF_RE = re.compile(r"(artclView\.do|[?&]enc=|[?&]artclNo=)", re.IGNORECASE)
# /bbs/medicine/1234/56789/artclView.do 의 56789 (게시글 번호)
_ARTICLE_NO_IN_PATH_RE = re.compile(r"/(\d+)/artclView\.do", re.IGNORECASE)
_PINNED_HINT_RE = re.compile(r"공지|notice|fixed|top", re.IGNORECASE)


class ScrapeError(RuntimeError):
    """목록 페이지를 가져오거나 파싱하지 못했을 때."""


def _is_retryable(exc: requests.RequestException) -> bool:
    """다시 시도해볼 만한 오류인지 판단한다.

    학교 서버가 가끔 느려져 읽기 시간이 초과된다. 이런 일시적인 문제는
    잠시 뒤 다시 요청하면 대개 성공한다. 반면 404 같은 응답은 몇 번을
    시도해도 결과가 같으므로 곧바로 포기한다.
    """
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    return response is not None and response.status_code >= 500


def fetch_html(
    url: str,
    *,
    timeout: float = 20.0,
    session: requests.Session | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    retries: int = 3,
    sleep_func=time.sleep,
) -> str:
    """목록 페이지 HTML을 문자열로 받아온다.

    일시적인 네트워크 오류는 점점 간격을 늘려가며 다시 시도한다.
    """
    sess = session or requests.Session()
    attempts = max(1, retries)
    last_error: requests.RequestException | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = sess.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                },
            )
            response.raise_for_status()
            break
        except requests.RequestException as exc:   # 네트워크/HTTP 오류
            last_error = exc
            if attempt >= attempts or not _is_retryable(exc):
                raise ScrapeError(
                    f"목록 페이지를 가져오지 못했습니다: {url} ({exc})"
                ) from exc
            delay = 2 ** attempt
            logger.warning(
                "게시판 접속 실패 (%d/%d), %d초 후 다시 시도합니다: %s",
                attempt,
                attempts,
                delay,
                exc,
            )
            sleep_func(delay)
    else:   # pragma: no cover - 위 루프는 성공하거나 예외를 던진다
        raise ScrapeError(f"목록 페이지를 가져오지 못했습니다: {url} ({last_error})")

    # 이 CMS는 대개 UTF-8이지만 헤더에 charset이 없으면 requests가 ISO-8859-1로
    # 추측해 한글이 깨진다. 명시가 없을 때만 apparent_encoding을 쓴다.
    if not response.encoding or "charset" not in response.headers.get("Content-Type", "").lower():
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _clean(text: str) -> str:
    """공백/줄바꿈을 정리한다."""
    return re.sub(r"\s+", " ", text or "").strip()


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml이 없는 환경 대비
        return BeautifulSoup(html, "html.parser")


def extract_uid(href: str, title: str) -> str:
    """링크에서 게시글을 유일하게 식별하는 키를 뽑는다.

    우선순위: 경로의 게시글 번호 > artclNo 파라미터 > enc 파라미터 >
    링크 해시 > 제목 해시. 어떤 경우에도 같은 글이면 같은 값이 나와야 한다.
    """
    href = (href or "").strip()
    if href:
        path_match = _ARTICLE_NO_IN_PATH_RE.search(href)
        if path_match:
            return path_match.group(1)

        query = parse_qs(urlparse(href).query)
        for key in ("artclNo", "artcl_no", "nttId", "bbsIdx", "idx"):
            values = [v for v in query.get(key, []) if v.strip()]
            if values:
                return values[0].strip()

        enc = [v for v in query.get("enc", []) if v.strip()]
        if enc:
            # enc는 게시글 경로를 인코딩한 값이라 글마다 고유하다.
            return "enc-" + hashlib.sha1(enc[0].encode("utf-8")).hexdigest()[:16]

        return "url-" + hashlib.sha1(href.encode("utf-8")).hexdigest()[:16]

    return "title-" + hashlib.sha1(_clean(title).encode("utf-8")).hexdigest()[:16]


def _class_contains(*needles: str):
    """클래스 이름에 주어진 문자열이 들어있는 요소를 찾기 위한 매처.

    bs4는 class_에 함수를 넘기면 개별 클래스명과 전체 클래스 문자열을
    각각 문자열로 넘겨준다.
    """

    lowered = tuple(n.lower() for n in needles)

    def matcher(value) -> bool:
        if not value:
            return False
        text = " ".join(value).lower() if isinstance(value, list) else str(value).lower()
        return any(n in text for n in lowered)

    return matcher


def _find_cell(row, *class_names: str):
    """행에서 주어진 클래스를 가진 칸을 찾는다."""
    return row.find(["td", "th"], class_=_class_contains(*class_names))


def _cell_text(row, *class_names: str) -> str:
    """행에서 주어진 클래스를 가진 칸의 텍스트를 찾는다."""
    for name in class_names:
        cell = _find_cell(row, name)
        if cell:
            return _clean(cell.get_text(" "))
    return ""


def _is_pinned(row, number: str) -> bool:
    """상단 고정 공지인지 추정한다."""
    if number and not number.isdigit():
        return True
    classes = " ".join(row.get("class") or [])
    if _PINNED_HINT_RE.search(classes):
        return True
    num_cell = _find_cell(row, "artclTdNum", "td-num", "num")
    if num_cell and num_cell.find("img"):  # '공지' 아이콘 이미지
        return True
    return False


def _title_from_anchor(anchor) -> str:
    """제목 링크에서 제목만 뽑는다(첨부/새글 아이콘의 대체 텍스트 제외)."""
    strong = anchor.find("strong")
    title = _clean(strong.get_text(" ")) if strong else ""
    if not title:
        title = _clean(anchor.get_text(" "))
    if not title:
        title = _clean(anchor.get("title", ""))
    return title


def _row_to_notice(row, base_url: str) -> Notice | None:
    """표의 한 행을 Notice로 바꾼다. 게시글 링크가 없으면 None."""
    anchor = None
    title_cell = _find_cell(row, "artclTdTitle", "td-subject", "subject", "title")
    if title_cell:
        anchor = title_cell.find("a", href=True)
    if anchor is None:
        for candidate in row.find_all("a", href=True):
            if _ARTICLE_HREF_RE.search(candidate["href"]):
                anchor = candidate
                break
    if anchor is None:
        return None

    title = _title_from_anchor(anchor)
    if not title:
        return None

    href = anchor["href"].strip()
    number = _cell_text(row, "artclTdNum", "td-num", "num")
    writer = _cell_text(row, "artclTdWriter", "td-write", "writer")
    date = _cell_text(row, "artclTdRdate", "td-date", "date")

    if not date:
        # 클래스가 없으면 날짜처럼 생긴 칸을 찾는다.
        for cell in row.find_all("td"):
            text = _clean(cell.get_text(" "))
            if re.fullmatch(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\.?", text):
                date = text
                break

    return Notice(
        uid=extract_uid(href, title),
        title=title,
        url=urljoin(base_url, href) if href else "",
        writer=writer,
        date=date,
        number=number,
        pinned=_is_pinned(row, number),
    )


def parse_notices(html: str, base_url: str = "") -> list[Notice]:
    """목록 페이지 HTML에서 공지 목록을 파싱한다(위에서부터 순서 유지)."""
    soup = _make_soup(html)
    notices: list[Notice] = []
    seen: set[str] = set()

    rows = soup.select("tr")
    for row in rows:
        notice = _row_to_notice(row, base_url)
        if notice and notice.uid not in seen:
            seen.add(notice.uid)
            notices.append(notice)

    if notices:
        return notices

    # 표가 아니라 목록(ul/li)으로 그리는 스킨 대비: 페이지의 게시글 링크를 모두 훑는다.
    for anchor in soup.find_all("a", href=True):
        if not _ARTICLE_HREF_RE.search(anchor["href"]):
            continue
        title = _title_from_anchor(anchor)
        if not title:
            continue
        uid = extract_uid(anchor["href"], title)
        if uid in seen:
            continue
        seen.add(uid)
        notices.append(
            Notice(uid=uid, title=title, url=urljoin(base_url, anchor["href"].strip()))
        )

    return notices


def scrape(
    url: str,
    *,
    timeout: float = 20.0,
    session: requests.Session | None = None,
    retries: int = 3,
) -> ScrapeResult:
    """목록 페이지를 받아 파싱한 결과를 돌려준다."""
    html = fetch_html(url, timeout=timeout, session=session, retries=retries)
    notices = parse_notices(html, base_url=url)
    if not notices:
        raise ScrapeError(
            "공지를 한 건도 찾지 못했습니다. 게시판 HTML 구조가 바뀌었을 수 있습니다: " + url
        )
    logger.debug("공지 %d건 파싱 완료 (%s)", len(notices), url)
    return ScrapeResult(notices=notices, source_url=url)
