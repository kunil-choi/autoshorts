import time

from autoshorts import library


def test_add_and_list_items_newest_first(tmp_path):
    item1 = library.new_item("프로그램A", "t1", "s1", "th1", "vid1", "https://youtu.be/vid1")
    library.add_item(tmp_path, item1)
    time.sleep(0.01)
    item2 = library.new_item("프로그램A", "t2", "s2", "th2", "vid2", "https://youtu.be/vid2")
    library.add_item(tmp_path, item2)

    items = library.list_items(tmp_path)
    assert [it["id"] for it in items] == [item2.id, item1.id]
    assert all(it["status"] == "private" for it in items)


def test_update_status_changes_only_matching_item(tmp_path):
    item1 = library.new_item("프로그램A", "t1", "s1", "th1", "vid1", "https://youtu.be/vid1")
    item2 = library.new_item("프로그램A", "t2", "s2", "th2", "vid2", "https://youtu.be/vid2")
    library.add_item(tmp_path, item1)
    library.add_item(tmp_path, item2)

    updated = library.update_status(tmp_path, item1.id, "public")
    assert updated["status"] == "public"

    items = {it["id"]: it for it in library.list_items(tmp_path)}
    assert items[item1.id]["status"] == "public"
    assert items[item2.id]["status"] == "private"


def test_update_status_unknown_id_returns_none(tmp_path):
    assert library.update_status(tmp_path, "nonexistent", "public") is None


def test_get_item(tmp_path):
    item = library.new_item("프로그램A", "t1", "s1", "th1", "vid1", "https://youtu.be/vid1")
    library.add_item(tmp_path, item)

    found = library.get_item(tmp_path, item.id)
    assert found["title"] == "t1"
    assert library.get_item(tmp_path, "nonexistent") is None


def test_list_items_empty_when_no_file(tmp_path):
    assert library.list_items(tmp_path) == []
