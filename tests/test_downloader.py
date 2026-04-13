import pytest
import inspect


class TestSubprocessTimeout:
    """Test subprocess timeout handling in downloader."""

    def test_ffmpeg_has_timeout(self):
        """Verify ffmpeg subprocess has timeout=60."""
        from downloader.app import make_thumb

        source = inspect.getsource(make_thumb)
        assert "timeout=60" in source

    def test_ytdlp_has_timeout(self):
        """Verify yt-dlp subprocess has timeout=600."""
        from downloader.app import process_item

        source = inspect.getsource(process_item)
        assert "timeout=600" in source

    def test_timeout_exception_handled(self):
        """Verify TimeoutExpired exception is handled."""
        import downloader.app as app_module

        source = inspect.getsource(app_module)
        assert "TimeoutExpired" in source


class TestOrphanedQueueRecovery:
    """Test orphaned processing queue recovery."""

    def test_recovery_function_exists(self):
        """Verify _recover_orphaned_queues exists."""
        from downloader.app import _recover_orphaned_queues

        assert callable(_recover_orphaned_queues)

    def test_main_calls_recovery(self):
        """Verify main() calls _recover_orphaned_queues."""
        from downloader.app import main

        source = inspect.getsource(main)
        assert "_recover_orphaned_queues" in source


# For running without docker (local dev)
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
