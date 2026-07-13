from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from claude_watch.webhook import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret-token")
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ask_unauthorized(client):
    r = client.post("/ask", json={"prompt": "hi"})
    assert r.status_code == 401


def test_ask_wrong_token(client):
    r = client.post(
        "/ask",
        json={"prompt": "hi"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_ask_missing_token_config(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "")
    with TestClient(app) as c:
        r = c.post(
            "/ask",
            json={"prompt": "hi"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert r.status_code == 500


def test_ask_success(client):
    async def fake_run(prompt, timeout=None):
        return (0, f"answer to: {prompt}", "")

    with patch("claude_watch.webhook.run_claude", side_effect=fake_run):
        r = client.post(
            "/ask",
            json={"prompt": "what is 2+2?"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert r.status_code == 200
    assert r.json() == {"answer": "answer to: what is 2+2?"}


def test_ask_claude_failure(client):
    async def fake_run(prompt, timeout=None):
        return (1, "", "boom")

    with patch("claude_watch.webhook.run_claude", side_effect=fake_run):
        r = client.post(
            "/ask",
            json={"prompt": "fail please"},
            headers={"Authorization": "Bearer secret-token"},
        )
    assert r.status_code == 502
    assert "rc=1" in r.json()["detail"]


def test_ask_validation_empty_prompt(client):
    r = client.post(
        "/ask",
        json={"prompt": ""},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert r.status_code == 422
