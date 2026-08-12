"""Límite diario de consultas inteligentes.

Una sola cuota por cuenta y por día —10 por defecto— compartida por todos los canales de
IA: el copiloto web, la interpretación de movimientos y, más adelante, WhatsApp. Lo que se
prueba acá es que el corte exista, que no se pueda esquivar y —tan importante como eso—
que no cobre de más:

1. El límite corta con 429 en la consulta 11 y no antes.
2. Es por usuario, y es el mismo para todos los canales.
3. Lo que no llega a invocar al modelo no gasta cuota.
4. Las llamadas concurrentes no lo atraviesan.
5. El día se corta a las 00:00 de Argentina y ahí se reinicia.

Las cuotas se reservan y se leen contra PostgreSQL, así que los tests de API cuelgan de la
fixture transaccional: cada caso arranca con los contadores en cero.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Generator
from datetime import UTC, date, datetime, timedelta

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
    LIMIT_HEADER,
    REMAINING_HEADER,
    RESET_AT_HEADER,
    WARN_AT_HEADER,
)
from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models.ai_daily_usage import AIDailyUsage
from app.services import ai_usage_service
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

LIMIT_MESSAGE = (
    "Llegaste al límite de 10 consultas inteligentes por hoy. Podés seguir usando las "
    "funciones manuales de Vector y volver a consultar mañana."
)


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
def small_limit(monkeypatch: pytest.MonkeyPatch) -> int:
    """Límite chico para agotarlo sin hacer decenas de peticiones por test.

    Lo que se prueba es el mecanismo, no el número: el valor real (10) tiene su propio
    test contra la configuración, y el recorrido completo de 0 a 11 también.
    """
    monkeypatch.setattr(settings, "ai_daily_limit", 3)
    return 3


def _profile(client: TestClient, **overrides: object) -> None:
    response = client.put(f"{API}/profile", json=default_profile_payload(**overrides))
    assert response.status_code == 200, response.text


# ---------- El valor configurado ----------


def test_el_limite_por_defecto_es_diez() -> None:
    assert settings.ai_daily_limit == 10
    assert settings.ai_usage_warning_threshold == 3


def test_la_zona_del_corte_es_argentina() -> None:
    assert settings.ai_usage_timezone == "America/Argentina/Buenos_Aires"


# ---------- 1. De 0 a 11: el recorrido completo con el límite real ----------


def test_una_cuenta_nueva_arranca_con_cero_usos(ai_client_for: Callable[..., TestClient]) -> None:
    body = ai_client_for(TEST_USER_ID).get(USAGE).json()

    assert body["used"] == 0
    assert body["limit"] == 10
    assert body["remaining"] == 10


def test_las_diez_consultas_pasan_y_la_once_da_429(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """El caso que importa de verdad: las 10 permitidas, la 11 bloqueada."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    for numero in range(1, 11):
        respuesta = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})
        assert respuesta.status_code == 200, f"cortó en la consulta {numero}"
        assert respuesta.json()["usage"]["used"] == numero
        assert respuesta.json()["usage"]["remaining"] == 10 - numero

    once = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})

    assert once.status_code == 429
    assert once.json()["detail"]["message"] == LIMIT_MESSAGE
    assert client.get(USAGE).json()["used"] == 10


def test_del_uno_al_nueve_siempre_queda_cuota(db_session) -> None:
    """Ningún uso intermedio bloquea ni deja el contador en un estado raro."""
    for numero in range(1, 10):
        status = ai_usage_service.consume(db_session, TEST_USER_ID)
        assert status.used == numero
        assert status.remaining == 10 - numero
        assert status.exhausted is False


def test_la_consulta_diez_es_la_ultima_permitida(db_session) -> None:
    for _ in range(9):
        ai_usage_service.consume(db_session, TEST_USER_ID)

    decima = ai_usage_service.consume(db_session, TEST_USER_ID)

    assert decima.used == 10
    assert decima.remaining == 0
    assert decima.exhausted is True
    with pytest.raises(AIDailyLimitReachedError):
        ai_usage_service.consume(db_session, TEST_USER_ID)


def test_la_interpretacion_corta_al_agotar_la_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    for intento in range(small_limit):
        respuesta = client.post(PARSE, json={"text": TEXT})
        assert respuesta.status_code == 200, f"cortó en el intento {intento + 1}"

    assert client.post(PARSE, json={"text": TEXT}).status_code == 429


def test_el_429_no_llama_al_modelo(
    ai_client_for: Callable[..., TestClient], small_limit: int
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
    for _ in range(small_limit):
        assert client.post(PARSE, json={"text": TEXT}).status_code == 200

    provider = ContandoProvider()
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(provider)
    respuesta = client.post(PARSE, json={"text": TEXT})

    assert respuesta.status_code == 429
    assert provider.llamadas == 0


# ---------- 2. Una sola cuota, por usuario, para todos los canales ----------


def test_la_cuota_es_de_cada_cuenta(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    de_a = ai_client_for(TEST_USER_ID)
    _profile(de_a)
    _profile(ai_client_for(OTHER_USER_ID))

    de_a = ai_client_for(TEST_USER_ID)
    for _ in range(small_limit):
        assert de_a.post(CHAT, json={"message": "hola"}).status_code == 200
    assert de_a.post(CHAT, json={"message": "hola"}).status_code == 429

    # La otra cuenta arranca con su cuota intacta.
    otra = ai_client_for(OTHER_USER_ID)
    assert otra.post(CHAT, json={"message": "hola"}).status_code == 200
    assert otra.get(USAGE).json()["used"] == 1


def test_los_contadores_de_dos_usuarios_son_independientes(db_session) -> None:
    for _ in range(4):
        ai_usage_service.consume(db_session, TEST_USER_ID)
    ai_usage_service.consume(db_session, OTHER_USER_ID)

    assert ai_usage_service.get_status(db_session, TEST_USER_ID).used == 4
    assert ai_usage_service.get_status(db_session, OTHER_USER_ID).used == 1


def test_el_copiloto_y_la_interpretacion_comparten_la_misma_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """Es UN límite del usuario, no uno por operación: los canales se suman."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    assert client.post(CHAT, json={"message": "hola"}).status_code == 200
    assert client.post(PARSE, json={"text": TEXT}).status_code == 200
    assert client.get(USAGE).json()["used"] == 2

    assert client.post(PARSE, json={"text": TEXT}).status_code == 200
    # Tercera consulta con límite 3: la cuarta ya no entra, venga del canal que venga.
    assert client.post(CHAT, json={"message": "hola"}).status_code == 429
    assert client.post(PARSE, json={"text": TEXT}).status_code == 429


def test_el_contador_vive_en_postgres(ai_client_for: Callable[..., TestClient], db_session) -> None:
    """Si el contador viviera en memoria, otra instancia del backend no vería este consumo.

    Acá se pide por HTTP y se lee la fila directamente: lo que queda escrito en PostgreSQL
    es lo que cualquier otra instancia va a leer.
    """
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    client.post(CHAT, json={"message": "hola"})

    fila = db_session.get(
        AIDailyUsage,
        (TEST_USER_ID, ai_usage_service.usage_day(), ai_usage_service.USAGE_BUCKET),
    )

    assert fila is not None
    assert fila.used == 1


# ---------- 3. Lo que no llega al modelo no gasta ----------


def test_un_cuerpo_invalido_no_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """Un 422 lo resuelve FastAPI antes del endpoint: nunca llega al proveedor."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    assert client.post(PARSE, json={"text": ""}).status_code == 422
    assert client.post(PARSE, json={}).status_code == 422

    assert client.get(USAGE).json()["used"] == 0


def test_un_409_por_accion_pendiente_no_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """El 409 se decide antes de correr el grafo, así que no cuesta una llamada."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    primera = client.post(CHAT, json={"message": TEXT})
    assert primera.status_code == 200
    assert primera.json()["requires_approval"] is True
    conversacion = primera.json()["conversation_id"]
    usados = client.get(USAGE).json()["used"]

    bloqueada = client.post(CHAT, json={"message": "otra cosa", "conversation_id": conversacion})

    assert bloqueada.status_code == 409
    assert client.get(USAGE).json()["used"] == usados


def test_confirmar_y_rechazar_un_borrador_no_gastan_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """Solo tocan PostgreSQL. Limitarlos dejaría borradores ya pagados sin poder resolver."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    uno = client.post(PARSE, json={"text": TEXT}).json()
    otro = client.post(PARSE, json={"text": TEXT}).json()
    usados = client.get(USAGE).json()["used"]

    assert (
        client.post(
            f"{API}/ai/transactions/{uno['draft_id']}/confirm", json={"confirmed": True}
        ).status_code
        == 201
    )
    assert client.post(f"{API}/ai/transactions/{otro['draft_id']}/reject").status_code == 204

    assert client.get(USAGE).json()["used"] == usados


def test_aprobar_una_accion_pendiente_no_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """Reanudar el grafo va derecho a `apply_write`: no vuelve a llamar al modelo."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    pendiente = client.post(CHAT, json={"message": TEXT}).json()
    usados = client.get(USAGE).json()["used"]

    aprobada = client.post(
        f"{API}/ai/conversations/{pendiente['conversation_id']}/approve",
        json={"action_id": pendiente["pending_action"]["action_id"]},
    )

    assert aprobada.status_code == 200
    assert client.get(USAGE).json()["used"] == usados


def test_las_acciones_manuales_no_gastan_cuota(
    ai_client_for: Callable[..., TestClient],
) -> None:
    """Cargar, editar y borrar a mano, el dashboard y las simulaciones son gratis."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    creado = client.post(
        f"{API}/transactions",
        json={
            "type": "expense",
            "amount": "1000.00",
            "category": "supermercado",
            "occurred_on": "2026-07-30",
        },
    )
    assert creado.status_code == 201
    assert client.get(f"{API}/transactions").status_code == 200
    assert client.get(f"{API}/commitments").status_code == 200
    assert client.get(f"{API}/dashboard/summary").status_code == 200
    assert (
        client.post(
            f"{API}/simulations/purchase",
            json={
                "purchase_name": "Notebook",
                "total_amount": "50000.00",
                "installments": 6,
                "first_installment_date": str(date.today() + timedelta(days=7)),
            },
        ).status_code
        == 201
    )
    assert client.patch(
        f"{API}/transactions/{creado.json()['id']}", json={"amount": "1200.00"}
    ).status_code in (200, 201)
    assert client.delete(f"{API}/transactions/{creado.json()['id']}").status_code == 204

    assert client.get(USAGE).json()["used"] == 0


def test_el_health_no_gasta_cuota(ai_client_for: Callable[..., TestClient]) -> None:
    client = ai_client_for(TEST_USER_ID)

    for _ in range(5):
        assert client.get("/health").status_code == 200

    assert client.get(USAGE).json()["used"] == 0


def test_consultar_la_cuota_no_gasta_cuota(ai_client_for: Callable[..., TestClient]) -> None:
    client = ai_client_for(TEST_USER_ID)

    for _ in range(3):
        assert client.get(USAGE).status_code == 200

    assert client.get(USAGE).json()["used"] == 0


def test_si_el_proveedor_no_esta_disponible_se_devuelve_la_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
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
    assert client.get(USAGE).json()["used"] == 0


def test_una_respuesta_invalida_del_modelo_si_gasta_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """Política de conteo: si el proveedor se invocó, la llamada ya se pagó y se cuenta."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider(force="invalid"))
    respuesta = client.post(PARSE, json={"text": TEXT})

    assert respuesta.status_code == 502
    assert client.get(USAGE).json()["used"] == 1


def test_una_misma_peticion_no_descuenta_dos_veces(db_session) -> None:
    """`DailyQuota.consume` es idempotente dentro del mismo request."""
    with ai_usage_service.daily_quota(db_session, TEST_USER_ID) as quota:
        quota.consume()
        quota.consume()
        quota.consume()

    assert ai_usage_service.get_status(db_session, TEST_USER_ID).used == 1


# ---------- El estado que ve el frontend ----------


def test_la_respuesta_trae_las_cabeceras_de_cuota(
    ai_client_for: Callable[..., TestClient],
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    primera = client.post(PARSE, json={"text": TEXT})
    segunda = client.post(PARSE, json={"text": TEXT})

    assert primera.headers[LIMIT_HEADER] == "10"
    assert int(primera.headers[REMAINING_HEADER]) == 9
    assert int(segunda.headers[REMAINING_HEADER]) == 8
    # El umbral del aviso viaja con la respuesta: el frontend no repite el número.
    assert primera.headers[WARN_AT_HEADER] == str(settings.ai_usage_warning_threshold)
    # Y cuándo se renueva, con el offset de Argentina.
    assert primera.headers[RESET_AT_HEADER].endswith("-03:00")


def test_las_cabeceras_de_cuota_se_exponen_a_cors() -> None:
    """Sin `Access-Control-Expose-Headers` el navegador no deja leerlas desde el frontend."""
    client = TestClient(app)

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    expuestas = response.headers["access-control-expose-headers"].lower()
    assert LIMIT_HEADER.lower() in expuestas
    assert REMAINING_HEADER.lower() in expuestas
    assert RESET_AT_HEADER.lower() in expuestas


def test_el_endpoint_de_uso_refleja_el_consumo(
    ai_client_for: Callable[..., TestClient],
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"})

    body = client.get(USAGE).json()

    assert body["limit"] == 10
    assert body["used"] == 1
    assert body["remaining"] == 9
    assert body["warning_threshold"] == settings.ai_usage_warning_threshold
    assert body["timezone"] == "America/Argentina/Buenos_Aires"
    assert body["reset_at"] == body["resets_at"]


def test_el_endpoint_de_uso_exige_sesion(anonymous_client: TestClient) -> None:
    assert anonymous_client.get(USAGE).status_code == 401


def test_el_endpoint_de_uso_solo_muestra_lo_propio(
    ai_client_for: Callable[..., TestClient],
) -> None:
    _profile(ai_client_for(TEST_USER_ID))
    ai_client_for(TEST_USER_ID).post(CHAT, json={"message": "hola"})

    assert ai_client_for(OTHER_USER_ID).get(USAGE).json()["used"] == 0


# ---------- 5. El día se corta en Argentina y ahí se reinicia ----------


def test_el_dia_se_corta_a_medianoche_de_argentina() -> None:
    """Argentina es UTC-3 todo el año: las 00:00 locales son las 03:00 UTC."""
    antes = datetime(2026, 7, 30, 2, 59, tzinfo=UTC)
    justo = datetime(2026, 7, 30, 3, 0, tzinfo=UTC)

    assert ai_usage_service.usage_day(antes) == date(2026, 7, 29)
    assert ai_usage_service.usage_day(justo) == date(2026, 7, 30)


def test_el_corte_no_es_a_medianoche_utc() -> None:
    """A las 00:30 UTC en Argentina siguen siendo las 21:30 del día anterior."""
    assert ai_usage_service.usage_day(datetime(2026, 7, 30, 0, 30, tzinfo=UTC)) == date(2026, 7, 29)


def test_al_cambiar_el_dia_el_contador_se_reinicia(db_session) -> None:
    ayer = date(2026, 7, 29)
    hoy = date(2026, 7, 30)
    for _ in range(10):
        ai_usage_service.consume(db_session, TEST_USER_ID, day=ayer)

    with pytest.raises(AIDailyLimitReachedError):
        ai_usage_service.consume(db_session, TEST_USER_ID, day=ayer)

    # El día siguiente arranca de cero: el contador es por (usuario, día).
    assert ai_usage_service.get_status(db_session, TEST_USER_ID, day=hoy).used == 0
    assert ai_usage_service.consume(db_session, TEST_USER_ID, day=hoy).used == 1
    # Y lo de ayer queda como estaba: reiniciar no borra el historial.
    assert ai_usage_service.get_status(db_session, TEST_USER_ID, day=ayer).used == 10


# ---------- El aviso de "te quedan pocos" ----------


def test_avisa_cuando_quedan_tres_usos_o_menos(db_session) -> None:
    limite = settings.ai_daily_limit
    umbral = settings.ai_usage_warning_threshold

    for _ in range(limite - umbral - 1):
        status = ai_usage_service.consume(db_session, TEST_USER_ID)
    assert status.warning is False, "avisó demasiado pronto"

    status = ai_usage_service.consume(db_session, TEST_USER_ID)
    assert status.remaining == umbral
    assert status.warning is True

    for _ in range(umbral):
        status = ai_usage_service.consume(db_session, TEST_USER_ID)
    # Ya agotada, deja de ser un aviso y pasa a ser un bloqueo.
    assert status.remaining == 0
    assert status.warning is False
    assert status.exhausted is True


# ---------- 4. Concurrencia ----------


@pytest.fixture
def usuario_concurrente() -> Generator[uuid.UUID, None, None]:
    """Usuario propio con limpieza real: estos tests necesitan commits de verdad."""
    user_id = uuid.uuid4()
    yield user_id
    with SessionLocal() as session:
        session.execute(delete(AIDailyUsage).where(AIDailyUsage.user_id == user_id))
        session.commit()


def _consumos_en_paralelo(user_id: uuid.UUID, hilos: int) -> list[bool]:
    """Lanza `hilos` reservas simultáneas y devuelve cuáles fueron concedidas."""
    barrera = threading.Barrier(hilos)
    concedidas: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrera.wait()
        with SessionLocal() as session:
            try:
                ai_usage_service.consume(session, user_id)
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
    return concedidas


def test_las_llamadas_concurrentes_no_atraviesan_el_limite(
    usuario_concurrente: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El punto del `INSERT ... ON CONFLICT ... WHERE`: reservar sin leer primero.

    Con un "leer, comparar y escribir" en tres pasos, veinte hilos simultáneos leerían el
    mismo contador y pasarían todos. Acá, exactamente `limite` reservas tienen éxito.
    """
    limite = 5
    monkeypatch.setattr(settings, "ai_daily_limit", limite)
    hilos = 20

    concedidas = _consumos_en_paralelo(usuario_concurrente, hilos)

    assert concedidas.count(True) == limite
    assert concedidas.count(False) == hilos - limite

    with SessionLocal() as session:
        final = ai_usage_service.get_status(session, usuario_concurrente)
    assert final.used == limite


def test_dos_solicitudes_simultaneas_al_borde_del_limite(
    usuario_concurrente: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con 9 de 10 usados y dos consultas a la vez, pasa una sola."""
    monkeypatch.setattr(settings, "ai_daily_limit", 10)
    with SessionLocal() as session:
        for _ in range(9):
            ai_usage_service.consume(session, usuario_concurrente)

    concedidas = _consumos_en_paralelo(usuario_concurrente, 2)

    assert concedidas.count(True) == 1
    assert concedidas.count(False) == 1
    with SessionLocal() as session:
        assert ai_usage_service.get_status(session, usuario_concurrente).used == 10


def test_el_contador_nunca_supera_el_limite(db_session, small_limit: int) -> None:
    for _ in range(small_limit):
        ai_usage_service.consume(db_session, TEST_USER_ID)

    for _ in range(5):
        with pytest.raises(AIDailyLimitReachedError):
            ai_usage_service.consume(db_session, TEST_USER_ID)

    assert ai_usage_service.get_status(db_session, TEST_USER_ID).used == small_limit


def test_devolver_una_cuota_nunca_baja_de_cero(db_session) -> None:
    ai_usage_service.refund(db_session, TEST_USER_ID)

    assert ai_usage_service.get_status(db_session, TEST_USER_ID).used == 0


# ---------- Forma del 429 ----------


def test_el_429_explica_todo_lo_necesario(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """El detalle es un objeto, no un string: el frontend necesita más que el texto."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(small_limit):
        client.post(CHAT, json={"message": "hola"})

    respuesta = client.post(CHAT, json={"message": "hola"})
    detail = respuesta.json()["detail"]

    assert respuesta.status_code == 429
    assert detail["code"] == DAILY_LIMIT_CODE
    assert detail["limit"] == small_limit
    assert detail["used"] == small_limit
    assert detail["remaining"] == 0
    assert detail["resets_at"] == detail["reset_at"]
    assert detail["timezone"] == "America/Argentina/Buenos_Aires"
    # Nunca se publica de quién es la cuota: quien recibe el error ya sabe quién es.
    assert "user_id" not in detail


def test_el_mensaje_del_429_usa_el_limite_configurado(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """Con AI_DAILY_LIMIT distinto de 10, el texto no puede seguir diciendo 10."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(small_limit):
        client.post(CHAT, json={"message": "hola"})

    mensaje = client.post(CHAT, json={"message": "hola"}).json()["detail"]["message"]

    assert mensaje.startswith(
        f"Llegaste al límite de {small_limit} consultas inteligentes por hoy."
    )
    assert "funciones manuales de Vector" in mensaje


def test_el_429_trae_retry_after_y_las_cabeceras_de_cuota(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(small_limit):
        client.post(PARSE, json={"text": TEXT})

    respuesta = client.post(PARSE, json={"text": TEXT})

    # Un cliente HTTP genérico busca Retry-After en la cabecera, no en el cuerpo.
    assert int(respuesta.headers["retry-after"]) > 0
    # Y las mismas cabeceras que un 200, para que el frontend actualice el contador igual.
    assert respuesta.headers[REMAINING_HEADER] == "0"
    assert respuesta.headers[LIMIT_HEADER] == str(small_limit)


def test_retry_after_no_pasa_de_un_dia(
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    """El reinicio es a la próxima medianoche: nunca puede faltar más de 24 horas."""
    client = ai_client_for(TEST_USER_ID)
    _profile(client)
    for _ in range(small_limit):
        client.post(PARSE, json={"text": TEXT})

    respuesta = client.post(PARSE, json={"text": TEXT})

    assert 0 < int(respuesta.headers["retry-after"]) <= 24 * 60 * 60


# ---------- `usage` en el cuerpo ----------


def test_el_chat_devuelve_la_cuota_en_el_cuerpo(
    ai_client_for: Callable[..., TestClient],
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    usage = client.post(CHAT, json={"message": "¿Cuánto puedo gastar hoy?"}).json()["usage"]

    assert usage["limit"] == 10
    assert usage["used"] == 1
    assert usage["remaining"] == 9
    assert usage["resets_at"] == usage["reset_at"]
    assert usage["timezone"] == "America/Argentina/Buenos_Aires"
    assert "user_id" not in usage


def test_el_parse_devuelve_la_cuota_en_el_cuerpo(
    ai_client_for: Callable[..., TestClient],
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    assert client.post(PARSE, json={"text": TEXT}).json()["usage"]["remaining"] == 9


def test_agregar_usage_no_rompio_los_campos_de_siempre(
    ai_client_for: Callable[..., TestClient],
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
    ai_client_for: Callable[..., TestClient], small_limit: int
) -> None:
    client = ai_client_for(TEST_USER_ID)
    _profile(client)

    usage = client.post(CHAT, json={"message": "hola"}).json()["usage"]

    # Límite 3 con umbral de aviso 3: la primera respuesta ya avisa.
    assert usage["remaining"] == small_limit - 1
    assert usage["warning"] is True


# ---------- resets_at ----------


def test_resets_at_es_la_medianoche_siguiente_de_argentina() -> None:
    status = ai_usage_service.AIUsageStatus(limit=10, used=1, day=date(2026, 7, 29))

    resets = status.resets_at

    assert resets.date() == date(2026, 7, 30)
    assert (resets.hour, resets.minute) == (0, 0)
    # Con offset explícito de Argentina, no en UTC.
    assert resets.utcoffset().total_seconds() == -3 * 3600
    assert resets.isoformat().startswith("2026-07-30T00:00:00-03:00")
    assert status.timezone == "America/Argentina/Buenos_Aires"
