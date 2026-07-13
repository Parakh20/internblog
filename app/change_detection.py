"""Diff fetched posts against known database state.

Detection uses both the WordPress modified timestamp and a content hash, so
an edit that WordPress timestamps but does not change content is ignored,
and a content change is caught even if timestamps misbehave.
"""

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnownPost:
    modified_gmt: str
    content_hash: str
    removed: bool = False


@dataclass
class ChangeSet:
    new: list[dict] = field(default_factory=list)
    modified: list[dict] = field(default_factory=list)
    removed_wp_ids: list[int] = field(default_factory=list)
    restored: list[dict] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.modified or self.removed_wp_ids or self.restored)


def compute_content_hash(title: str, content_html: str) -> str:
    payload = f"{title}\x1f{content_html}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def post_hash(post: dict) -> str:
    title = post.get("title", {}).get("rendered", "")
    content = post.get("content", {}).get("rendered", "")
    return compute_content_hash(title, content)


def detect_changes(fetched: list[dict], known: dict[int, KnownPost]) -> ChangeSet:
    """Compare the full fetched post list against known state.

    fetched: raw WP REST post dicts (must be the complete current list).
    known: wp_id -> KnownPost from the database.
    """
    changes = ChangeSet()
    fetched_ids = set()

    for post in fetched:
        wp_id = post["id"]
        fetched_ids.add(wp_id)
        existing = known.get(wp_id)
        if existing is None:
            changes.new.append(post)
        elif existing.removed:
            changes.restored.append(post)
        elif (
            post.get("modified_gmt") != existing.modified_gmt
            and post_hash(post) != existing.content_hash
        ):
            changes.modified.append(post)

    for wp_id, existing in known.items():
        if wp_id not in fetched_ids and not existing.removed:
            changes.removed_wp_ids.append(wp_id)

    return changes
