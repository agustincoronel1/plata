"""Arranque y cierre de la aplicación: el checkpointer se inicializa y se libera."""

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


def test_el_checkpointer_se_inicializa_en_el_arranque(monkeypatch):
    """En el arranque, no en la primera petición.

    Es lo que evita que el DDL de `setup()` y la aplicación de RLS caigan en medio del
    primer mensaje de alguien, con un 500 de premio si algo sale mal.
    """
    called = {"start": 0}

    def fake_start():
        called["start"] += 1
        return True

    monkeypatch.setattr("app.ai.agent.checkpointer.start_checkpointer", fake_start)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        # Ya estaba inicializado antes de atender la primera petición.
        assert called["start"] == 1

    assert called["start"] == 1


def test_un_checkpointer_caido_no_impide_arrancar(monkeypatch):
    """Vector sirve sin copiloto: dashboard, movimientos y compromisos siguen andando.

    Tumbar la aplicación entera porque el copiloto no puede checkpointear sería peor que
    dejar ese pedazo en 503.
    """

    def fake_start():
        return False

    monkeypatch.setattr("app.ai.agent.checkpointer.start_checkpointer", fake_start)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
