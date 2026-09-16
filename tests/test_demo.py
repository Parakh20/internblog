import importlib.util
import os
from pathlib import Path

DEMO_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "demo.py"


def _load_demo_module(monkeypatch):
    # demo.py points DATABASE_URL/SNAPSHOT_DIR at demo paths on import; keep
    # that from leaking into the rest of the test session.
    for key in ("DATABASE_URL", "SNAPSHOT_DIR"):
        monkeypatch.setenv(key, os.environ.get(key, ""))
    spec = importlib.util.spec_from_file_location("demo_script", DEMO_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_round_two_replaces_edited_post_and_appends_new_one(monkeypatch):
    demo = _load_demo_module(monkeypatch)

    round_1, round_2 = demo.load_rounds()

    assert [p["id"] for p in round_1] == [9001, 9002, 9003]
    assert [p["id"] for p in round_2] == [9001, 9002, 9003, 9004]
    edited = round_2[0]
    assert edited["modified_gmt"] != round_1[0]["modified_gmt"]
    assert "Extended" in edited["title"]["rendered"]


def test_fixture_client_returns_posts_in_blog_client_shape(monkeypatch):
    demo = _load_demo_module(monkeypatch)
    posts = [{"id": 1}]

    result = demo.FixtureBlogClient(posts).fetch_posts()

    assert result.posts == posts
    assert result.http_status == 200


def test_fixture_contains_no_real_institute_links(monkeypatch):
    demo = _load_demo_module(monkeypatch)

    round_1, round_2 = demo.load_rounds()

    for post in round_1 + round_2:
        assert post["link"].startswith("https://example.com/")
