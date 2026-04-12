import pytest
import requests


def test_api_health_endpoint():
    response = requests.get("http://api:8080/health")
    assert response.status_code == 200
