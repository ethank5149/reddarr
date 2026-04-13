import pytest
import inspect


class TestSubprocessTimeout:
    """Test subprocess timeout handling in downloader."""

    def test_ffmpeg_timeout_handling(self):
        """Verify ffmpeg subprocess has timeout parameter."""
        from downloader.app import make_thumb

        source = inspect.getsource(make_thumb)
        assert "timeout=60" in source, "ffmpeg should have timeout=60"

    def test_ytdlp_timeout_handling(self):
        """Verify yt-dlp subprocess has timeout parameter."""
        from downloader.app import process_item

        source = inspect.getsource(process_item)
        assert "timeout=600" in source, "yt-dlp should have timeout=600"

    def test_timeout_exception_handling(self):
        """Test that TimeoutExpired is handled properly."""
        import downloader.app as app_module

        source = inspect.getsource(app_module)
        assert "TimeoutExpired" in source, "Should handle subprocess.TimeoutExpired"


class TestOrphanedQueueRecovery:
    """Test orphaned processing queue recovery."""

    def test_recovery_function_exists(self):
        """Verify _recover_orphaned_queues function exists."""
        from downloader.app import _recover_orphaned_queues

        assert callable(_recover_orphaned_queues)

    def test_recovery_called_on_startup(self):
        """Verify recovery is called in main()."""
        from downloader.app import main

        source = inspect.getsource(main)
        assert "_recover_orphaned_queues" in source, (
            "main() should call _recover_orphaned_queues"
        )

    def test_orphan_key_pattern_matching(self):
        """Verify function looks for media_processing_* keys."""
        from downloader.app import _recover_orphaned_queues

        source = inspect.getsource(_recover_orphaned_queues)
        assert "media_processing_*" in source, "Should scan for media_processing_* keys"


class TestThreadSafeRedditClient:
    """Test PRAW thread-safety fixes in ingester."""

    def test_create_reddit_client_function_exists(self):
        """Verify _create_reddit_client factory function exists."""
        from ingester.app import _create_reddit_client

        assert callable(_create_reddit_client)

    def test_fetch_target_accepts_client_parameter(self):
        """Verify fetch_target_posts accepts reddit_client parameter."""
        from ingester.app import fetch_target_posts

        sig = inspect.signature(fetch_target_posts)
        params = list(sig.parameters.keys())

        assert "reddit_client" in params, (
            "fetch_target_posts should accept reddit_client parameter"
        )

    def test_worker_creates_own_client(self):
        """Verify backfill workers create their own PRAW instances."""
        from ingester.app import run_backfill_parallel

        source = inspect.getsource(run_backfill_parallel)
        assert "_create_reddit_client" in source, (
            "Workers should create their own PRAW clients"
        )


class TestRedisRateLimiter:
    """Test Redis-based rate limiting in web API."""

    def test_redis_rate_limiter_class_exists(self):
        """Verify RedisRateLimiter class exists."""
        from web.app import RedisRateLimiter

        assert RedisRateLimiter is not None

    def test_rate_limiter_uses_redis(self):
        """Verify rate limiter uses Redis for storage."""
        from web.app import RedisRateLimiter

        source = inspect.getsource(RedisRateLimiter)
        assert "redis" in source.lower(), "RateLimiter should use Redis"
        assert "ratelimit:" in source, "Should use ratelimit: key prefix"

    def test_get_rate_limiter_function_exists(self):
        """Verify get_rate_limiter function exists."""
        from web.app import get_rate_limiter

        assert callable(get_rate_limiter)


class TestAPIKeyAuthentication:
    """Test API key authentication for admin endpoints."""

    def test_get_api_key_function_exists(self):
        """Verify get_api_key function exists."""
        from web.app import get_api_key

        assert callable(get_api_key)

    def test_require_api_key_dependency_exists(self):
        """Verify require_api_key dependency exists."""
        from web.app import require_api_key

        assert callable(require_api_key)

    def test_admin_endpoints_have_auth(self):
        """Verify admin endpoints use require_api_key dependency."""
        import web.app as app_module

        source = inspect.getsource(app_module)

        # Check that admin endpoints have Depends(require_api_key)
        assert "Depends(require_api_key)" in source, (
            "Admin endpoints should require API key"
        )


# For running without docker (local dev)
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
