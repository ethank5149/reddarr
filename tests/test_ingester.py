import pytest
import inspect


class TestThreadSafeRedditClient:
    """Test PRAW thread-safety fixes in ingester."""

    def test_create_reddit_client_exists(self):
        """Verify _create_reddit_client factory function exists."""
        from ingester.app import _create_reddit_client

        assert callable(_create_reddit_client)

    def test_fetch_target_accepts_client_param(self):
        """Verify fetch_target_posts accepts reddit_client parameter."""
        from ingester.app import fetch_target_posts

        sig = inspect.signature(fetch_target_posts)
        params = list(sig.parameters.keys())
        assert "reddit_client" in params

    def test_worker_creates_own_client(self):
        """Verify backfill workers create their own PRAW instances."""
        from ingester.app import run_backfill_parallel

        source = inspect.getsource(run_backfill_parallel)
        assert "_create_reddit_client" in source


# For running without docker (local dev)
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
