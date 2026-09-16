"""Run the real monitoring pipeline against sample posts, no IITB login needed.

The live blog sits behind IIT Bombay SSO, so nobody outside the institute can
run the monitor against it. This script swaps only the HTTP fetch for a
fixture (demo/sample_posts.json) and runs two ordinary cycles through
app.pipeline.run_cycle: snapshot, change detection, LLM extraction, storage.

Round 1 sees three new posts. Round 2 sees one edited post (deadline
extended) and one new shortlist post, so change detection has real work.

Everything goes to a separate data/demo.db. No users exist there, so nothing
is pushed to Telegram or Google Calendar.

Usage:
    python scripts/demo.py            # needs GROQ_API_KEY (free) for extraction
    python scripts/demo.py --reset    # delete data/demo.db first
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = PROJECT_ROOT / "data" / "demo.db"
FIXTURE = PROJECT_ROOT / "demo" / "sample_posts.json"

# Must be set before app.config is imported: settings are read at import time.
os.environ["DATABASE_URL"] = f"sqlite:///{DEMO_DB}"
os.environ["SNAPSHOT_DIR"] = str(PROJECT_ROOT / "data" / "demo_snapshots")
sys.path.insert(0, str(PROJECT_ROOT))

from app.blog_client import FetchResult  # noqa: E402
from app.config import settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.session_state import SessionMonitor  # noqa: E402


class FixtureBlogClient:
    """Stands in for app.blog_client.BlogClient: same fetch_posts() contract,
    posts come from the fixture instead of the SSO-protected REST API."""

    def __init__(self, posts: list[dict]):
        self.posts = posts

    def fetch_posts(self) -> FetchResult:
        return FetchResult(posts=self.posts, http_status=200)


def load_rounds() -> tuple[list[dict], list[dict]]:
    fixture = json.loads(FIXTURE.read_text())
    round_1 = fixture["round_1"]
    changes = {post["id"]: post for post in fixture["round_2_changes"]}
    round_2 = [changes.pop(post["id"], post) for post in round_1] + list(changes.values())
    return round_1, round_2


def print_cycle_result(db, label: str) -> None:
    from app.models import Extraction, FetchLog, Post

    log = db.query(FetchLog).order_by(FetchLog.id.desc()).first()
    print(f"\n== {label}")
    print(
        f"   fetch status={log.status} posts={log.posts_count} "
        f"new={log.new_count} modified={log.modified_count} removed={log.removed_count}"
    )
    rows = (
        db.query(Extraction, Post)
        .join(Post, Extraction.post_id == Post.id)
        .order_by(Extraction.id)
        .all()
    )
    if not rows:
        print("   no extractions yet")
        return
    for extraction, post in rows:
        print(
            f"   [{extraction.category:<18}] {extraction.company or '-':<18} "
            f"role={extraction.role or '-'} | deadline={extraction.deadline or '-'} "
            f"| stipend={extraction.stipend or '-'}  (post {post.wp_id})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete data/demo.db before running")
    args = parser.parse_args()

    if args.reset and DEMO_DB.exists():
        DEMO_DB.unlink()

    setup_logging(settings.log_dir)

    from app.db import SessionLocal, init_db
    from app.pipeline import run_cycle

    init_db()
    if not (settings.groq_api_key or settings.llm_api_key):
        print(
            "No GROQ_API_KEY or LLM_API_KEY set: posts will be stored and diffed, "
            "but LLM extraction is skipped. Get a free key at https://console.groq.com "
            "and put it in .env to see extraction."
        )

    round_1, round_2 = load_rounds()
    monitor = SessionMonitor()

    run_cycle(FixtureBlogClient(round_1), monitor, SessionLocal)
    with SessionLocal() as db:
        print_cycle_result(db, "Round 1: first fetch of the blog")

    run_cycle(FixtureBlogClient(round_2), monitor, SessionLocal)
    with SessionLocal() as db:
        print_cycle_result(db, "Round 2: one post edited, one new post")

    print(f"\nDemo database: {DEMO_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
