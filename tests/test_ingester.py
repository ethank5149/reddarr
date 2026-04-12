import pytest
import praw
import os


def test_ingester_reddit_connection():
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT")

    if not client_id or not client_secret:
        pytest.skip("Reddit credentials not provided")

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent or "reddarr-tests/1.0",
    )
    assert reddit.read_only
