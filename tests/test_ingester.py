import pytest
import praw
import os


def test_ingester_reddit_connection():
    reddit = praw.Reddit(
        client_id=os.environ.get("REDDIT_CLIENT_ID"),
        client_secret=os.environ.get("REDDIT_CLIENT_SECRET"),
        user_agent=os.environ.get("REDDIT_USER_AGENT"),
    )
    assert reddit.read_only
