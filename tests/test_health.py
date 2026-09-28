"""Tests for the Stage 1 API.

Run from the project root so that `backend.main` is importable:

    pytest tests/test_health.py
"""

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_returns_ok_json():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_content_type_is_json():
    response = client.get("/api/health")
    assert response.headers["content-type"].startswith("application/json")


def test_root_serves_frontend_html():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Paper-Trader" in response.text
