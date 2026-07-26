from fastapi.testclient import TestClient

from app.main import app


def test_lifespan_cierra_checkpointer(monkeypatch):
    called = {"close": 0}

    def fake_close():
        called["close"] += 1

    monkeypatch.setattr("app.ai.agent.graph.close_checkpointer", fake_close)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert called["close"] == 1
