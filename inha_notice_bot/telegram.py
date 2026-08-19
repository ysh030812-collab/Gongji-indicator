"""텔레그램 Bot API로 메시지를 보낸다."""

from __future__ import annotations

import html
import logging
import time

import requests

from .models import Notice

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096   # 텔레그램 메시지 길이 제한
_TRUNCATE_AT = 4000      # 여유를 두고 자른다


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
                error = TelegramError(f"텔레그램 전송 실패 (HTTP {response.status_code}): {description}")
                # 400/401/403 은 토큰·chat_id·본문 문제라 재시도해도 그대로다.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise error
                last_error = error
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("텔레그램 요청 오류 (%d/%d): %s", attempt, self.max_retries, exc)

            if attempt < self.max_retries:
                time.sleep(2 ** attempt)

        raise TelegramError(f"텔레그램 전송을 {self.max_retries}회 시도했지만 실패했습니다: {last_error}")

    def send_notice(self, notice: Notice, board_name: str = "인하대 의과대학 공지") -> dict:
        return self.send_text(format_notice(notice, board_name))

    def check_auth(self) -> str:
        """getMe 로 토큰이 유효한지 확인하고 봇 이름을 돌려준다."""
        try:
            response = self.session.get(f"{self.api_base}/bot{self.token}/getMe", timeout=self.timeout)
        except requests.RequestException as exc:
            raise TelegramError(f"텔레그램 API에 접속할 수 없습니다: {exc}") from exc
        body = _json_or_none(response)
        if not (response.ok and body and body.get("ok")):
            raise TelegramError(f"봇 토큰이 유효하지 않습니다: {(body or {}).get('description', response.text[:200])}")
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
