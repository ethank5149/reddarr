import pytest
import os
import requests


def test_downloader_download_file():
    # Create a dummy file to download
    with open("test.txt", "w") as f:
        f.write("This is a test file.")

    # Get the URL of the dummy file
    file_url = f"file://{os.path.abspath('test.txt')}"

    # Download the file using the downloader service
    response = requests.get(f"http://downloader:8003/download?url={file_url}")

    # Check if the file was downloaded successfully
    assert response.status_code == 200
    assert response.content == b"This is a test file."

    # Clean up the dummy file
    os.remove("test.txt")
