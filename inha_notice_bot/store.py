"""이미 알린 공지를 파일에 기록해 중복 알림을 막는다."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Notice

logger = logging.getLogger(__name__)

# 상태 파일이 무한정 커지지 않도록 최근 N건만 남긴다.
MAX_TRACKED = 1000


class SeenStore:
    """알림을 보낸 공지 uid 집합을 JSON 파일로 관리한다."""

    def __init__(self, path: str | os.PathLike[str], max_tracked: int = MAX_TRACKED) -> None:
        self.path = Path(path)
        self.max_tracked = max_tracked
        self._order: list[str] = []   # 오래된 것부터
        self._seen: set[str] = set()
        self.initialized = False      # 한 번이라도 저장된 적 있는 상태 파일인지
        self.last_run: str | None = None
        self._load()

    # ------------------------------------------------------------------ 조회
    def __contains__(self, uid: str) -> bool:
        return uid in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def is_new(self, notice: Notice) -> bool:
        return notice.uid not in self._seen

    def filter_new(self, notices: list[Notice]) -> list[Notice]:
        """아직 알리지 않은 공지만 오래된 것 -> 최신 순으로 돌려준다.

        게시판 목록은 최신이 위에 오므로 뒤집어서 알림 순서를 시간순으로 맞춘다.
        """
        fresh = [n for n in notices if n.uid not in self._seen]
        return list(reversed(fresh))

    # ------------------------------------------------------------------ 변경
    def mark(self, notice: Notice) -> None:
        if notice.uid in self._seen:
            return
        self._seen.add(notice.uid)
        self._order.append(notice.uid)

    def mark_all(self, notices: list[Notice]) -> None:
        for notice in notices:
            self.mark(notice)

    # ------------------------------------------------------------------ 입출력
    def _load(self) -> None:
        if not self.path.exists():
            logger.info("상태 파일이 없어 새로 시작합니다: %s", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # 손상된 상태 파일 때문에 봇이 죽지는 않게 한다. 다만 이미 본 글을
            # 다시 알리지 않도록 초기화 상태로 두어 첫 실행처럼 시딩한다.
            logger.warning("상태 파일을 읽지 못해 초기화합니다 (%s): %s", self.path, exc)
            return

        uids = data.get("seen") if isinstance(data, dict) else data
        if isinstance(uids, list):
            for uid in uids:
                uid = str(uid)
                if uid not in self._seen:
                    self._seen.add(uid)
                    self._order.append(uid)
        if isinstance(data, dict):
            self.last_run = data.get("last_run")
        self.initialized = True

    def save(self) -> None:
        """원자적으로 저장한다(중간에 죽어도 파일이 깨지지 않도록)."""
        if len(self._order) > self.max_tracked:
            dropped = self._order[: -self.max_tracked]
            self._order = self._order[-self.max_tracked :]
            self._seen.difference_update(dropped)

        payload = {
            "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(self._order),
            "seen": self._order,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        self.initialized = True
        self.last_run = payload["last_run"]
