"""환경변수/명령행 인자에서 설정을 읽는다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BOARD_URL = "https://medicine.inha.ac.kr/medicine/13793/subview.do"
DEFAULT_BOARD_NAME = "인하대 의과대학 공지"
DEFAULT_STATE_FILE = "state.json"
DEFAULT_INTERVAL = 600      # 초
DEFAULT_MAX_PER_RUN = 10    # 한 번에 보낼 최대 알림 수(폭주 방지)


class ConfigError(RuntimeError):
    """설정이 부족하거나 잘못됐을 때."""


@dataclass
class Config:
    bot_token: str
    chat_id: str
    board_url: str = DEFAULT_BOARD_URL
    board_name: str = DEFAULT_BOARD_NAME
    state_file: str = DEFAULT_STATE_FILE
    interval: int = DEFAULT_INTERVAL
    max_per_run: int = DEFAULT_MAX_PER_RUN
    timeout: float = 15.0
    notify_first_run: bool = False
    dry_run: bool = False

    def validate(self) -> None:
        if not self.dry_run:
            if not self.bot_token:
                raise ConfigError(
                    "TELEGRAM_BOT_TOKEN 이 설정되지 않았습니다. "
                    "(.env 를 만들거나 환경변수로 넘겨주세요)"
                )
            if not self.chat_id:
                raise ConfigError("TELEGRAM_CHAT_ID 가 설정되지 않았습니다.")
        if not self.board_url.startswith(("http://", "https://")):
            raise ConfigError(f"게시판 주소가 올바르지 않습니다: {self.board_url}")
        if self.interval < 60:
            raise ConfigError("확인 주기(interval)는 60초 이상이어야 합니다. 서버에 부담을 주지 마세요.")


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """의존성 없이 간단히 .env 를 읽어 os.environ 에 채운다.

    이미 설정된 환경변수는 덮어쓰지 않는다.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 은(는) 정수여야 합니다: {raw!r}") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def load_config(env_file: str | os.PathLike[str] | None = ".env") -> Config:
    """.env 와 환경변수에서 설정을 만든다."""
    if env_file:
        load_dotenv(env_file)
    return Config(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        board_url=os.environ.get("BOARD_URL", DEFAULT_BOARD_URL).strip() or DEFAULT_BOARD_URL,
        board_name=os.environ.get("BOARD_NAME", DEFAULT_BOARD_NAME).strip() or DEFAULT_BOARD_NAME,
        state_file=os.environ.get("STATE_FILE", DEFAULT_STATE_FILE).strip() or DEFAULT_STATE_FILE,
        interval=_env_int("CHECK_INTERVAL", DEFAULT_INTERVAL),
        max_per_run=_env_int("MAX_PER_RUN", DEFAULT_MAX_PER_RUN),
        timeout=float(os.environ.get("HTTP_TIMEOUT", "15") or 15),
        notify_first_run=_env_bool("NOTIFY_FIRST_RUN", False),
    )
