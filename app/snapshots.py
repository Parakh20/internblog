"""Timestamped raw snapshots of every successful fetch, for debugging and replay."""

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path


def save_snapshot(snapshot_dir: Path, posts: list[dict]) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = snapshot_dir / f"posts_{ts}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False)
    return path
