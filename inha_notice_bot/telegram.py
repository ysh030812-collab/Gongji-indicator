"""텔레그램 Bot API로 메시지를 보낸다."""

from __future__ import annotations

import html
import logging
import re
import time

import requests

from .models import Notice

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096   # 텔레그램 메시지 길이 제한
_TRUNCATE_AT = 4000      # 여유를 두고 자른다

# 봇 토큰은 '숫자:영문숫자_-' 형태다. 공백이나 줄바꿈이 섞이면 API가 404를 주는데
# 그 메시지만으로는 원인을 알기 어려워, 보내기 전에 형식을 먼저 확인한다.
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
# 채팅 ID는 정수(그룹은 음수)이거나 @채널이름 이다.
_CHAT_ID_RE = re.compile(r"^(-?\d+|@[A-Za-z0-9_]+)$")


class TelegramError(RuntimeError):
    """텔레그램 전송 실패."""


def format_notice(notice: Notice, board_name: str = "인하대 의과대학 공지") -> str:
    """공지 한 건을 텔레그램 HTML 메시지로 만든다."""
    title = html.escape(notice.title)
    lines = [f"📢 <b>{html.escape(board_name)}</b>", ""]

    if notice.url:
        lines.append(f'<a href="{html.escape(notice.url, quote=True)}">{title}</a>')
    else:
        lines.append(f"<b>{title}</b>")

    meta = []
    if notice.writer:
        meta.append(html.escape(notice.writer))
    if notice.date:
        meta.append(html.escape(notice.date))
    if notice.pinned:
        meta.append("고정 공지")
    if meta:
        lines.append("")
        lines.append("🗓 " + " · ".join(meta))

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_LEN:
        text = text[:_TRUNCATE_AT] + "…"
    return text


def format_listing(notices: list[Notice], board_url: str, limit: int = 20) -> str:
    """게시판 목록 전체를 한 통의 점검용 메시지로 만든다.

    PC 없이 휴대폰만으로 파싱이 잘 되는지 확인할 때 쓴다.
    """
    header = f"🔎 <b>게시판 확인 결과</b>\n공지 {len(notices)}건을 읽었습니다.\n"
    lines = [header]

    for index, notice in enumerate(notices[:limit], start=1):
        title = html.escape(notice.title)
        mark = "📌 " if notice.pinned else ""
        if notice.url:
            entry = f'{index}. {mark}<a href="{html.escape(notice.url, quote=True)}">{title}</a>'
        else:
            entry = f"{index}. {mark}{title}"
        if notice.date:
            entry += f"  <i>({html.escape(notice.date)})</i>"
        lines.append(entry)

    if len(notices) > limit:
        lines.append(f"\n… 외 {len(notices) - limit}건")
    lines.append(f"\n{html.escape(board_url)}")

    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_LEN:
        text = text[:_TRUNCATE_AT] + "\n…(생략)"
    return text


class TelegramNotifier:
    """봇 토큰과 채팅 ID를 들고 메시지를 보내는 클라이언트."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        session: requests.Session | None = None,
        disable_web_page_preview: bool = True,
        api_base: str = API_BASE,
    ) -> None:
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN 이 비어 있습니다.")
        if not chat_id:
            raise TelegramError("TELEGRAM_CHAT_ID 가 비어 있습니다.")
        if not _TOKEN_RE.match(token):
            raise TelegramError(
                "봇 토큰 형식이 올바르지 않습니다. "
                "'123456789:AAE...' 처럼 숫자와 콜론으로 시작해야 하며 "
                "공백이 섞이면 안 됩니다. BotFather 메시지에서 토큰만 다시 복사해 주세요."
            )
        if not _CHAT_ID_RE.match(str(chat_id)):
            raise TelegramError(
                f"채팅 ID 형식이 올바르지 않습니다: {chat_id!r} "
                "숫자(그룹은 -1001234567890 처럼 음수)여야 합니다."
            )
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.session = session or requests.Session()
        self.disable_web_page_preview = disable_web_page_preview
        self.api_base = api_base.rstrip("/")

    @property
    def _send_url(self) -> str:
        return f"{self.api_base}/bot{self.token}/sendMessage"

    def _redact(self, text: object) -> str:
        """로그/오류 문구에서 봇 토큰을 가린다.

        requests 의 예외 메시지에는 요청 URL이 통째로 들어 있고, 그 URL에는
        봇 토큰이 박혀 있다. 그대로 찍으면 CI 로그에 토큰이 남는다.
        """
        message = str(text)
        if self.token:
            message = message.replace(self.token, "<봇토큰>")
            # 토큰의 비밀 부분(콜론 뒤)만 남는 경우도 막는다.
            _, _, secret = self.token.partition(":")
            if len(secret) >= 8:
                message = message.replace(secret, "<봇토큰>")
        return message

    def send_text(self, text: str) -> dict:
        """텍스트 메시지 한 건을 보낸다. 실패하면 지수 백오프로 재시도."""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": self.disable_web_page_preview,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(self._send_url, data=payload, timeout=self.timeout)
                if response.status_code == 429:
                    # 텔레그램이 알려주는 대기 시간만큼 쉰다.
                    retry_after = _retry_after(response)
                    logger.warning("텔레그램 속도 제한, %.1f초 대기", retry_after)
                    time.sleep(retry_after)
                    last_error = TelegramError("429 Too Many Requests")
                    continue

                body = _json_or_none(response)
                if response.ok and body and body.get("ok"):
                    return body

                description = (body or {}).get("description", response.text[:200])
                if response.status_code == 404:
                    description = (
                        "봇 토큰이 잘못됐습니다(해당 봇을 찾을 수 없음). "
                        "토큰에 공백이나 오타가 없는지, 토큰을 재발급(/revoke)한 뒤 "
                        "설정을 갱신했는지 확인하세요."
                    )
                elif response.status_code == 403:
                    description = (
                        f"{description} — 봇 대화방에서 /start 를 눌렀는지, "
                        "그룹이라면 봇이 아직 그룹에 있는지 확인하세요."
                    )
                error = TelegramError(
                    f"텔레그램 전송 실패 (HTTP {response.status_code}): "
                    + self._redact(description)
                )
                # 400/401/403 은 토큰·chat_id·본문 문제라 재시도해도 그대로다.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise error
                last_error = error
            except requests.RequestException as exc:
                last_error = self._redact(exc)
                logger.warning(
                    "텔레그램 요청 오류 (%d/%d): %s", attempt, self.max_retries, last_error
                )

            if attempt < self.max_retries:
                time.sleep(2 ** attempt)

        raise TelegramError(
            f"텔레그램 전송을 {self.max_retries}회 시도했지만 실패했습니다: "
            + self._redact(last_error)
        )

    def send_notice(self, notice: Notice, board_name: str = "인하대 의과대학 공지") -> dict:
        return self.send_text(format_notice(notice, board_name))

    def check_auth(self) -> str:
        """getMe 로 토큰이 유효한지 확인하고 봇 이름을 돌려준다."""
        try:
            response = self.session.get(f"{self.api_base}/bot{self.token}/getMe", timeout=self.timeout)
        except requests.RequestException as exc:
            raise TelegramError(
                "텔레그램 API에 접속할 수 없습니다: " + self._redact(exc)
            ) from exc
        body = _json_or_none(response)
        if not (response.ok and body and body.get("ok")):
            detail = (body or {}).get("description", response.text[:200])
            raise TelegramError(
                f"봇 토큰이 유효하지 않습니다 (HTTP {response.status_code}): "
                + self._redact(detail)
            )
        return body["result"].get("username", "unknown")


def _json_or_none(response: requests.Response) -> dict | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _retry_after(response: requests.Response, default: float = 3.0) -> float:
    body = _json_or_none(response) or {}
    parameters = body.get("parameters") or {}
    for value in (parameters.get("retry_after"), response.headers.get("Retry-After")):
        try:
            if value is not None:
                return min(float(value), 60.0)
        except (TypeError, ValueError):
            continue
    return default
