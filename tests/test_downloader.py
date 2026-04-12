import pytest


@pytest.mark.skip(reason="Downloader is a worker service without HTTP API")
def test_downloader_download_file():
    pass
