"""Tests de integración de /api/v1/ai/transactions (parse, confirm, reject).

Transaccionales contra PostgreSQL. El gateway y el draft store se inyectan por test para
forzar errores del proveedor y aislar el estado. Nunca se llama a un proveedor real.
"""

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient

from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockAIProvider
from app.main import app
from app.services.draft_store import InMemoryDraftStore, get_draft_store
from tests.conftest import API, requires_postgres

pytestmark = requires_postgres


@pytest.fixture
def ai_client(client: TestClient) -> Generator[TestClient, None, None]:
    """`client` + gateway mock + draft store en memoria fresco, inyectados por dependencia."""
    store = InMemoryDraftStore()
    app.dependency_overrides[get_draft_store] = lambda: store
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider())
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


def _use_gateway(force: str) -> None:
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider(force=force))


def _parse(client: TestClient, text: str):
    return client.post(f"{API}/ai/transactions/parse", json={"text": text})


def test_parse_devuelve_borrador(ai_client: TestClient) -> None:
    resp = _parse(ai_client, "Gasté 25 lucas ayer en nafta con débito")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "create_transaction"
    assert body["requires_confirmation"] is True
    assert body["is_confirmable"] is True
    assert body["draft_id"]


def test_parse_texto_corto_es_422(ai_client: TestClient) -> None:
    assert _parse(ai_client, "a").status_code == 422


def test_confirm_crea_movimiento(ai_client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    draft_id = _parse(ai_client, "Gasté 25 lucas ayer en nafta con débito").json()["draft_id"]
    resp = ai_client.post(f"{API}/ai/transactions/{draft_id}/confirm", json={"confirmed": True})
    assert resp.status_code == 201, resp.text
    assert str(resp.json()["transaction"]["amount"]) == "25000.00"


def test_confirm_dos_veces_es_409(ai_client: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    draft_id = _parse(ai_client, "Gasté 25 lucas ayer en nafta con débito").json()["draft_id"]
    ai_client.post(f"{API}/ai/transactions/{draft_id}/confirm", json={"confirmed": True})
    again = ai_client.post(f"{API}/ai/transactions/{draft_id}/confirm", json={"confirmed": True})
    assert again.status_code == 409


def test_confirm_draft_inexistente_es_404(
    ai_client: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    import uuid

    resp = ai_client.post(f"{API}/ai/transactions/{uuid.uuid4()}/confirm", json={"confirmed": True})
    assert resp.status_code == 404


def test_reject_descarta(ai_client: TestClient) -> None:
    draft_id = _parse(ai_client, "Gasté 25 lucas ayer en nafta con débito").json()["draft_id"]
    resp = ai_client.post(f"{API}/ai/transactions/{draft_id}/reject")
    assert resp.status_code == 204


def test_provider_timeout_es_504(ai_client: TestClient) -> None:
    _use_gateway("timeout")
    assert _parse(ai_client, "lo que sea").status_code == 504


def test_provider_error_es_503(ai_client: TestClient) -> None:
    _use_gateway("error")
    assert _parse(ai_client, "lo que sea").status_code == 503


def test_provider_salida_invalida_es_502(ai_client: TestClient) -> None:
    _use_gateway("invalid")
    assert _parse(ai_client, "lo que sea").status_code == 502
