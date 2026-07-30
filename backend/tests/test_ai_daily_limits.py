"""Límites diarios de uso de IA.

Protegen el costo de una demo pública: 20 consultas al copiloto y 10 interpretaciones de
movimientos por día y por cuenta. Lo que se prueba acá es que el corte exista, que no se
pueda esquivar y —tan importante como eso— que no cobre de más:

1. El límite corta con 429 y no antes.
2. Es por usuario y por tipo de operación.
3. Lo que no llega a invocar al modelo no gasta cuota.
4. Las llamadas concurrentes no lo atraviesan.
5. El día se corta a las 00:00 de Argentina.

Las cuotas se reservan y se leen contra PostgreSQL, así que los tests de API cuelgan de la
fixture transaccional: cada caso arranca con los contadores en cero.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Generator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.ai.exceptions import (
    DAILY_LIMIT_CODE,
    AIDailyLimitReachedError,
    AIProviderUnavailableError,
)
from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockAIProvider
from app.api.usage_headers import (
    KIND_HEADER,
    LIMIT_HEADER,
    REMAINING_HEADER,
    WARN_AT_HEADER,
)
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.ai_daily_usage import AIDailyUsage
from app.services import ai_usage_service
from app.services.ai_usage_service import AIUsageKind
from app.services.draft_store import InMemoryDraftStore, get_draft_store
from tests.conftest import (
    API,
    OTHER_USER_ID,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

pytestmark = requires_postgres

CHAT = f"{API}/ai/chat"
PARSE = f"{API}/ai/transactions/parse"
USAGE = f"{API}/ai/usage"

TEXT = "Gasté 25 lucas ayer en nafta con débito"


@pytest.fixture
def ai_client_for(
    client_for: Callable[..., TestClient],
) -> Generator[Callable[..., TestClient], None, None]:
    """Clientes autenticados con gateway mock y un único draft store en memoria."""
    store = InMemoryDraftStore()
    app.dependency_overrides[get_draft_store] = lambda: store
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider())
    try:
        yield client_for
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


@pytest.fixture
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Límites chicos para poder agotarlos sin hacer 30 peticiones por test.

    Lo que se prueba es el mecanismo, no el número: los valores reales (20 y 10) tienen su
    propio test contra la configuración.
    """
    monkeypatch.setattr(settings, "ai_daily_chat_limit", 3)
    monkeypatch.setattr(settings, "ai_daily_parse_limit", 2)


def _profile(client: TestClient, **overrides: object) -> None:
    response = client.put(f"{API}/profile", json=default_profile_payload(**overrides))
    assert response.status_code == 200, response.text


# ---------- Los valores configurados ----------


def test_los_limites_por_defecto_son_los_pedidos() -> None:
    assert settings.ai_daily_chat_limit == 20
    assert settings.ai_daily_parse_limit == 10
    assert settings.ai_usage_warning_threshold == 3


# ---------- 1. El límite corta con 429 ----------


def test_el_copiloto_corta_al_agotar_la_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    for intento in range(settings.ai_daily_chat_limit):
        respuesta = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})
        assert respuesta.status_code == 200, f"cortó en el intento {intento + 1}"

    bloqueada = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})

    assert bloqueada.status_code == 429
    assert bloqueada.json()["detail"]["message"] == (
        "Alcanzaste el límite diario del copiloto. Vas a poder volver a usarlo mañana."
    )


def test_la_interpretacion_corta_al_agotar_la_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    for intento in range(settings.ai_daily_parse_limit):
        respuesta = client.post(PARSE, json={"text": TEXT})
        assert respuesta.status_code == 200, f"cortó en el intento {intento + 1}"

    bloqueada = client.post(PARSE, json={"text": TEXT})

    assert bloqueada.status_code == 429


def test_el_429_no_llama_al_modelo(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """Bloquear tiene que ser barato: si igual se llamara al proveedor, no serviría de nada."""

    class ContandoProvider(MockAIProvider):
        def __init__(self) -> None:
            super().__init__()
            self.llamadas = 0

        def generate_structured(self, *args: object, **kwargs: object):
            self.llamadas += 1
            return super().generate_structured(*args, **kwargs)

    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(settings.ai_daily_parse_limit):
        assert client.post(PARSE, json={"text": TEXT}).status_code == 200

    provider = ContandoProvider()
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(provider)
    respuesta = client.post(PARSE, json={"text": TEXT})

    assert respuesta.status_code == 429
    assert provider.llamadas == 0


# ---------- 2. Por usuario y por tipo ----------


def test_la_cuota_es_de_cada_cuenta(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    de_a = ai_client_for(TEST_USER_ID)
    _profile(de_a)
    _profile(ai_client_for(OTHER_USER_ID))

    de_a = ai_client_for(TEST_USER_ID)
    for _ in range(settings.ai_daily_chat_limit):
        assert de_a.post(CHAT, json={"message": "hola"}).status_code == 200
    assert de_a.post(CHAT, json={"message": "hola"}).status_code == 429

    # La otra cuenta arranca con su cuota intacta.
    assert ai_client_for(OTHER_USER_ID).post(CHAT, json={"message": "hola"}).status_code == 200


def test_agotar_una_cuota_no_bloquea_la_otra(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    for _ in range(settings.ai_daily_parse_limit):
        assert client.post(PARSE, json={"text": TEXT}).status_code == 200
    assert client.post(PARSE, json={"text": TEXT}).status_code == 429

    assert client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"}).status_code == 200


# ---------- 3. Lo que no llega al modelo no gasta ----------


def test_un_cuerpo_invalido_no_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """Un 422 lo resuelve FastAPI antes del endpoint: nunca llega al proveedor."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    assert client.post(PARSE, json={"text": ""}).status_code == 422
    assert client.post(PARSE, json={}).status_code == 422

    assert client.get(USAGE).json()["transaction_parse"]["used"] == 0


def test_un_409_por_accion_pendiente_no_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """El 409 se decide antes de correr el grafo, así que no cuesta una llamada."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    primera = client.post(CHAT, json={"message": TEXT})
    assert primera.status_code == 200
    assert primera.json()["requires_approval"] is True
    conversacion = primera.json()["conversation_id"]
    usados = client.get(USAGE).json()["copilot_chat"]["used"]

    bloqueada = client.post(CHAT, json={"message": "otra cosa", "conversation_id": conversacion})

    assert bloqueada.status_code == 409
    assert client.get(USAGE).json()["copilot_chat"]["used"] == usados


def test_confirmar_y_rechazar_un_borrador_no_gastan_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """Solo tocan PostgreSQL. Limitarlos dejaría borradores ya pagados sin poder resolver."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    uno = client.post(PARSE, json={"text": TEXT}).json()
    otro = client.post(PARSE, json={"text": TEXT}).json()
    usados = client.get(USAGE).json()["transaction_parse"]["used"]

    assert (
        client.post(
            f"{API}/ai/transactions/{uno['draft_id']}/confirm", json={"confirmed": True}
        ).status_code
        == 201
    )
    assert client.post(f"{API}/ai/transactions/{otro['draft_id']}/reject").status_code == 204

    assert client.get(USAGE).json()["transaction_parse"]["used"] == usados


def test_aprobar_una_accion_pendiente_no_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """Reanudar el grafo va derecho a `apply_write`: no vuelve a llamar al modelo."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    pendiente = client.post(CHAT, json={"message": TEXT}).json()
    usados = client.get(USAGE).json()["copilot_chat"]["used"]

    aprobada = client.post(
        f"{API}/ai/conversations/{pendiente['conversation_id']}/approve",
        json={"action_id": pendiente["pending_action"]["action_id"]},
    )

    assert aprobada.status_code == 200
    assert client.get(USAGE).json()["copilot_chat"]["used"] == usados


def test_consultar_la_cuota_no_gasta_cuota(ai_client_for: Callable[..., TestClient]) -> None:
    client = ai_client_for(TEST_USER_ID)

    for _ in range(3):
        assert client.get(USAGE).status_code == 200

    assert client.get(USAGE).json()["copilot_chat"]["used"] == 0


def test_si_el_proveedor_no_esta_disponible_se_devuelve_la_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """No se llegó a gastar plata: cobrar el intento sería cobrar de más."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    class ProveedorCaido(MockAIProvider):
        def generate_structured(self, *args: object, **kwargs: object):
            raise AIProviderUnavailableError

    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(ProveedorCaido())
    respuesta = client.post(PARSE, json={"text": TEXT})

    assert respuesta.status_code == 503
    assert client.get(USAGE).json()["transaction_parse"]["used"] == 0


def test_una_respuesta_invalida_del_modelo_si_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """Acá el proveedor SÍ se invocó: la llamada ya se pagó aunque la respuesta no sirviera."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider(force="invalid"))
    respuesta = client.post(PARSE, json={"text": TEXT})

    assert respuesta.status_code == 502
    assert client.get(USAGE).json()["transaction_parse"]["used"] == 1


# ---------- El estado que ve el frontend ----------


def test_la_respuesta_trae_las_cabeceras_de_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    primera = client.post(PARSE, json={"text": TEXT})
    segunda = client.post(PARSE, json={"text": TEXT})

    assert primera.headers[LIMIT_HEADER] == str(settings.ai_daily_parse_limit)
    assert primera.headers[KIND_HEADER] == "transaction_parse"
    assert int(primera.headers[REMAINING_HEADER]) == settings.ai_daily_parse_limit - 1
    assert int(segunda.headers[REMAINING_HEADER]) == settings.ai_daily_parse_limit - 2
    # El umbral del aviso viaja con la respuesta: el frontend no repite el número.
    assert primera.headers[WARN_AT_HEADER] == str(settings.ai_usage_warning_threshold)


def test_las_cabeceras_de_cuota_se_exponen_a_cors() -> None:
    """Sin `Access-Control-Expose-Headers` el navegador no deja leerlas desde el frontend."""
    client = TestClient(app)

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    expuestas = response.headers["access-control-expose-headers"].lower()
    assert LIMIT_HEADER.lower() in expuestas
    assert REMAINING_HEADER.lower() in expuestas


def test_el_endpoint_de_uso_refleja_el_consumo(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})

    body = client.get(USAGE).json()

    assert body["copilot_chat"] == {
        "limit": settings.ai_daily_chat_limit,
        "used": 1,
        "remaining": settings.ai_daily_chat_limit - 1,
    }
    assert body["transaction_parse"]["used"] == 0
    assert body["warning_threshold"] == settings.ai_usage_warning_threshold


def test_el_endpoint_de_uso_exige_sesion(anonymous_client: TestClient) -> None:
    assert anonymous_client.get(USAGE).status_code == 401


def test_el_endpoint_de_uso_solo_muestra_lo_propio(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    _profile(ai_client_for(TEST_USER_ID))
    ai_client_for(TEST_USER_ID).post(CHAT, json={"message": "hola"})

    assert ai_client_for(OTHER_USER_ID).get(USAGE).json()["copilot_chat"]["used"] == 0


# ---------- 5. El día se corta en Argentina ----------


def test_el_dia_se_corta_a_medianoche_de_argentina() -> None:
    """Argentina es UTC-3 todo el año: las 00:00 locales son las 03:00 UTC."""
    antes = datetime(2026, 7, 30, 2, 59, tzinfo=UTC)
    justo = datetime(2026, 7, 30, 3, 0, tzinfo=UTC)

    assert ai_usage_service.usage_day(antes) == date(2026, 7, 29)
    assert ai_usage_service.usage_day(justo) == date(2026, 7, 30)


def test_el_corte_no_es_a_medianoche_utc() -> None:
    """A las 00:30 UTC en Argentina siguen siendo las 21:30 del día anterior."""
    assert ai_usage_service.usage_day(datetime(2026, 7, 30, 0, 30, tzinfo=UTC)) == date(2026, 7, 29)


def test_agotar_un_dia_no_afecta_al_siguiente(db_session, small_limits: None) -> None:
    ayer = date(2026, 7, 29)
    hoy = date(2026, 7, 30)
    for _ in range(settings.ai_daily_chat_limit):
        ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT, day=ayer)

    with pytest.raises(AIDailyLimitReachedError):
        ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT, day=ayer)

    # El día siguiente arranca de cero: el contador es por (usuario, día, tipo).
    status = ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT, day=hoy)
    assert status.used == 1


# ---------- El aviso de "te quedan pocos" ----------


def test_avisa_cuando_quedan_tres_usos_o_menos(db_session) -> None:
    limite = ai_usage_service.limit_for(AIUsageKind.COPILOT_CHAT)
    umbral = settings.ai_usage_warning_threshold

    for _ in range(limite - umbral - 1):
        status = ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT)
    assert status.warning is False, "avisó demasiado pronto"

    status = ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT)
    assert status.remaining == umbral
    assert status.warning is True

    for _ in range(umbral):
        status = ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT)
    # Ya agotada, deja de ser un aviso y pasa a ser un bloqueo.
    assert status.remaining == 0
    assert status.warning is False
    assert status.exhausted is True


# ---------- 4. Concurrencia ----------


@pytest.fixture
def usuario_concurrente() -> Generator[uuid.UUID, None, None]:
    """Usuario propio con limpieza real: este test necesita commits de verdad."""
    user_id = uuid.uuid4()
    yield user_id
    with SessionLocal() as session:
        session.execute(delete(AIDailyUsage).where(AIDailyUsage.user_id == user_id))
        session.commit()


def test_las_llamadas_concurrentes_no_atraviesan_el_limite(
    usuario_concurrente: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El punto del `INSERT ... ON CONFLICT ... WHERE`: reservar sin leer primero.

    Con un "leer, comparar y escribir" en tres pasos, veinte hilos simultáneos leerían el
    mismo contador y pasarían todos. Acá, exactamente `limite` reservas tienen éxito.
    """
    limite = 5
    monkeypatch.setattr(settings, "ai_daily_chat_limit", limite)
    hilos = 20
    barrera = threading.Barrier(hilos)
    concedidas: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrera.wait()
        with SessionLocal() as session:
            try:
                ai_usage_service.consume(session, usuario_concurrente, AIUsageKind.COPILOT_CHAT)
                resultado = True
            except AIDailyLimitReachedError:
                resultado = False
        with lock:
            concedidas.append(resultado)

    threads = [threading.Thread(target=worker) for _ in range(hilos)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert concedidas.count(True) == limite
    assert concedidas.count(False) == hilos - limite

    with SessionLocal() as session:
        final = ai_usage_service.get_status(session, usuario_concurrente, AIUsageKind.COPILOT_CHAT)
    assert final.used == limite


def test_el_contador_nunca_supera_el_limite(db_session, small_limits: None) -> None:
    limite = settings.ai_daily_parse_limit
    for _ in range(limite):
        ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.TRANSACTION_PARSE)

    for _ in range(5):
        with pytest.raises(AIDailyLimitReachedError):
            ai_usage_service.consume(db_session, TEST_USER_ID, AIUsageKind.TRANSACTION_PARSE)

    status = ai_usage_service.get_status(db_session, TEST_USER_ID, AIUsageKind.TRANSACTION_PARSE)
    assert status.used == limite


def test_devolver_una_cuota_nunca_baja_de_cero(db_session) -> None:
    ai_usage_service.refund(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT)

    status = ai_usage_service.get_status(db_session, TEST_USER_ID, AIUsageKind.COPILOT_CHAT)
    assert status.used == 0


# ---------- Forma del 429 ----------


def test_el_429_del_copiloto_explica_todo_lo_necesario(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """El detalle es un objeto, no un string: el frontend necesita más que el texto."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(settings.ai_daily_chat_limit):
        client.post(CHAT, json={"message": "hola"})

    respuesta = client.post(CHAT, json={"message": "hola"})
    detail = respuesta.json()["detail"]

    assert respuesta.status_code == 429
    assert detail["code"] == DAILY_LIMIT_CODE
    assert detail["kind"] == "copilot_chat"
    assert detail["limit"] == settings.ai_daily_chat_limit
    assert detail["used"] == settings.ai_daily_chat_limit
    assert detail["remaining"] == 0
    assert detail["resets_at"]
    # Nunca se publica de quién es la cuota: quien recibe el error ya sabe quién es.
    assert "user_id" not in detail


def test_el_429_de_interpretacion_usa_su_propio_mensaje(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """Decir cuál se agotó evita que parezca que Plata entera dejó de funcionar."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(settings.ai_daily_parse_limit):
        client.post(PARSE, json={"text": TEXT})

    detail = client.post(PARSE, json={"text": TEXT}).json()["detail"]

    assert detail["kind"] == "transaction_parse"
    assert detail["message"] == (
        "Alcanzaste el límite diario de interpretaciones con IA. "
        "Vas a poder volver a usarla mañana."
    )


def test_el_429_trae_retry_after_y_las_cabeceras_de_cuota(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(settings.ai_daily_parse_limit):
        client.post(PARSE, json={"text": TEXT})

    respuesta = client.post(PARSE, json={"text": TEXT})

    # Un cliente HTTP genérico busca Retry-After en la cabecera, no en el cuerpo.
    assert int(respuesta.headers["retry-after"]) > 0
    # Y las mismas cabeceras que un 200, para que el frontend actualice el contador igual.
    assert respuesta.headers[REMAINING_HEADER] == "0"
    assert respuesta.headers[KIND_HEADER] == "transaction_parse"


def test_retry_after_no_pasa_de_un_dia(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """El reinicio es a la próxima medianoche: nunca puede faltar más de 24 horas."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(settings.ai_daily_parse_limit):
        client.post(PARSE, json={"text": TEXT})

    respuesta = client.post(PARSE, json={"text": TEXT})

    assert 0 < int(respuesta.headers["retry-after"]) <= 24 * 60 * 60


# ---------- `usage` en el cuerpo ----------


def test_el_chat_devuelve_la_cuota_en_el_cuerpo(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    body = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"}).json()

    assert body["usage"]["kind"] == "copilot_chat"
    assert body["usage"]["limit"] == settings.ai_daily_chat_limit
    assert body["usage"]["used"] == 1
    assert body["usage"]["remaining"] == settings.ai_daily_chat_limit - 1
    assert body["usage"]["resets_at"]
    assert "user_id" not in body["usage"]


def test_el_parse_devuelve_la_cuota_en_el_cuerpo(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    body = client.post(PARSE, json={"text": TEXT}).json()

    assert body["usage"]["kind"] == "transaction_parse"
    assert body["usage"]["remaining"] == settings.ai_daily_parse_limit - 1


def test_agregar_usage_no_rompio_los_campos_de_siempre(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    """`usage` es un campo agregado: lo que ya devolvían las respuestas sigue igual."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    chat = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"}).json()
    parse = client.post(PARSE, json={"text": TEXT}).json()

    for campo in (
        "conversation_id",
        "message_id",
        "answer",
        "intent",
        "tools_used",
        "evidence",
        "assumptions",
        "requires_approval",
        "trace_id",
    ):
        assert campo in chat, campo
    for campo in (
        "draft_id",
        "intent",
        "transaction",
        "confidence",
        "missing_fields",
        "ambiguities",
        "explanation",
        "requires_confirmation",
        "is_confirmable",
        "prompt_version",
        "provider",
        "model",
        "latency_ms",
    ):
        assert campo in parse, campo


def test_el_aviso_viaja_en_la_metadata_al_llegar_al_umbral(
    ai_client_for: Callable[..., TestClient], small_limits: None
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    monkeypatched = settings.ai_daily_chat_limit  # 3, con umbral de aviso en 3

    body = client.post(CHAT, json={"message": "hola"}).json()

    assert body["usage"]["remaining"] == monkeypatched - 1
    assert body["usage"]["warning"] is True


# ---------- resets_at ----------


def test_resets_at_es_la_medianoche_siguiente_de_argentina() -> None:
    status = ai_usage_service.AIUsageStatus(
        kind=AIUsageKind.COPILOT_CHAT, limit=20, used=1, day=date(2026, 7, 29)
    )

    resets = status.resets_at

    assert resets.date() == date(2026, 7, 30)
    assert (resets.hour, resets.minute) == (0, 0)
    # Con offset explícito de Argentina, no en UTC.
    assert resets.utcoffset().total_seconds() == -3 * 3600
    assert resets.isoformat().startswith("2026-07-30T00:00:00-03:00")
