"""공지 한 건을 표현하는 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Notice:
    """게시판 목록에서 파싱한 공지 한 건.

    Attributes:
        uid: 게시글을 유일하게 식별하는 키. 게시글 번호(artclNo)나 링크의
            enc 파라미터에서 뽑아내며, 둘 다 없으면 링크/제목의 해시를 쓴다.
            상태 파일에 저장되는 값이므로 같은 글이면 항상 같아야 한다.
        title: 공지 제목.
        url: 공지 본문 링크(절대 URL).
        writer: 작성자(없으면 빈 문자열).
        date: 작성일(없으면 빈 문자열).
        number: 목록의 '번호' 칸 값. 고정 공지는 보통 '공지'.
        pinned: 상단 고정 공지 여부.
    """

    uid: str
    title: str
    url: str = ""
    writer: str = ""
    date: str = ""
    number: str = ""
    pinned: bool = False

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "url": self.url,
            "writer": self.writer,
            "date": self.date,
            "number": self.number,
            "pinned": self.pinned,
        }


@dataclass
class ScrapeResult:
    """한 번의 스크래핑 결과."""

    notices: list[Notice] = field(default_factory=list)
    source_url: str = ""

    def __len__(self) -> int:
        return len(self.notices)

    def __iter__(self):
        return iter(self.notices)
