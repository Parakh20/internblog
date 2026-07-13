from app.change_detection import KnownPost, detect_changes, post_hash


def make_post(wp_id: int, title: str = "Acme [Internship]", content: str = "<p>Apply now</p>",
              modified: str = "2026-07-13T04:00:00") -> dict:
    return {
        "id": wp_id,
        "modified_gmt": modified,
        "title": {"rendered": title},
        "content": {"rendered": content},
    }


def known_from(post: dict, removed: bool = False) -> KnownPost:
    return KnownPost(
        modified_gmt=post["modified_gmt"],
        content_hash=post_hash(post),
        removed=removed,
    )


def test_first_run_marks_everything_new():
    fetched = [make_post(1), make_post(2)]
    changes = detect_changes(fetched, known={})
    assert len(changes.new) == 2
    assert not changes.modified
    assert not changes.removed_wp_ids


def test_unchanged_posts_produce_no_changes():
    post = make_post(1)
    changes = detect_changes([post], {1: known_from(post)})
    assert not changes.has_changes


def test_content_change_with_new_timestamp_is_modified():
    old = make_post(1, content="<p>Deadline Friday</p>")
    new = make_post(1, content="<p>Deadline extended to Sunday</p>",
                    modified="2026-07-13T09:00:00")
    changes = detect_changes([new], {1: known_from(old)})
    assert changes.modified == [new]


def test_timestamp_bump_without_content_change_is_ignored():
    old = make_post(1)
    new = make_post(1, modified="2026-07-13T09:00:00")
    changes = detect_changes([new], {1: known_from(old)})
    assert not changes.has_changes


def test_missing_post_is_removed():
    old = make_post(1)
    changes = detect_changes([], {1: known_from(old)})
    assert changes.removed_wp_ids == [1]


def test_already_removed_post_not_reported_again():
    old = make_post(1)
    changes = detect_changes([], {1: known_from(old, removed=True)})
    assert not changes.has_changes


def test_removed_post_reappearing_is_restored():
    old = make_post(1)
    changes = detect_changes([old], {1: known_from(old, removed=True)})
    assert changes.restored == [old]
