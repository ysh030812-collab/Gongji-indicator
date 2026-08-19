"""상태 저장 테스트."""

import json

from inha_notice_bot.models import Notice
from inha_notice_bot.store import SeenStore


def make(uid: str) -> Notice:
    return Notice(uid=uid, title=f"공지 {uid}")


def test_new_store_is_not_initialized(tmp_path):
    store = SeenStore(tmp_path / "state.json")
    assert store.initialized is False
    assert len(store) == 0


def test_filter_new_returns_oldest_first(tmp_path):
    store = SeenStore(tmp_path / "state.json")
    # 게시판은 최신이 위 -> 알림은 오래된 순서로 나가야 한다.
    listing = [make("3"), make("2"), make("1")]
    assert [n.uid for n in store.filter_new(listing)] == ["1", "2", "3"]


def test_marked_notices_are_not_new_after_reload(tmp_path):
    path = tmp_path / "state.json"
    store = SeenStore(path)
    store.mark_all([make("1"), make("2")])
    store.save()

    reloaded = SeenStore(path)
    assert reloaded.initialized is True
    assert reloaded.filter_new([make("2"), make("1")]) == []
    assert [n.uid for n in reloaded.filter_new([make("3"), make("2")])] == ["3"]


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "state.json"
    store = SeenStore(path)
    store.mark(make("1"))
    store.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["seen"] == ["1"]
    assert data["last_run"]
    assert list(tmp_path.iterdir()) == [path]


def test_old_entries_are_trimmed(tmp_path):
    path = tmp_path / "state.json"
    store = SeenStore(path, max_tracked=5)
    store.mark_all([make(str(i)) for i in range(10)])
    store.save()

    reloaded = SeenStore(path, max_tracked=5)
    assert len(reloaded) == 5
    assert "9" in reloaded and "0" not in reloaded


def test_corrupt_state_file_does_not_crash(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = SeenStore(path)
    # 초기화 상태로 취급되어 첫 실행처럼 시딩된다(과거 글 재알림 방지).
    assert store.initialized is False
    assert len(store) == 0


def test_state_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    store = SeenStore(path)
    store.mark(make("1"))
    store.save()
    assert path.is_file()
