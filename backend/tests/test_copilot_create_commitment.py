"""Alta de compromisos desde el copiloto: robustez, aprobación y aislamiento.

Antes, esto solo funcionaba para cuatro conceptos hardcodeados (alquiler, internet, obra
social, colegio), el monto tenía que venir con unidad coloquial ("350 lucas") y no existía
ninguna fecha relativa. En los hechos, pedirle al copiloto "agendá netflix de 15 lucas para
el 20 de agosto" no creaba nada y la conversación se quedaba pidiendo el nombre para
siempre.

Estos tests fijan lo que tiene que seguir andando:

1. La extracción entiende frases naturales, no una plantilla.
2. Nada se escribe sin aprobación humana explícita.
3. El compromiso queda a nombre del usuario del token, nunca de otro.
4. Los argumentos se validan por schema antes de tocar la base.

La parte de extracción no necesita base ni modelo: son funciones puras y se prueban con un
`as_of` fijo, así los tests no cambian de resultado según el día en que corran.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.agent.router import extract_commitment_fields
from app.ai.agent.tools import TOOLS, CreateCommitmentArgs
from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockAIProvider
from app.main import app
from app.services.draft_store import InMemoryDraftStore, get_draft_store
from tests.conftest import (
    API,
    OTHER_USER_ID,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

# Un miércoles, para que "el viernes" tenga una respuesta única y comprobable.
AS_OF = date(2026, 8, 12)


def _fields(message: str, as_of: date = AS_OF) -> dict:
    return extract_commitment_fields(message, {}, as_of, None)


# ---------- 15. Valida nombres, montos, fechas y categorías ----------


@pytest.mark.parametrize(
    ("mensaje", "nombre", "monto", "vencimiento"),
    [
        (
            "agenda el alquiler de 350 lucas para el 5 de septiembre",
            "alquiler",
            "350000",
            "2026-09-05",
        ),
        ("agenda netflix de 15 lucas para el 20 de agosto", "netflix", "15000", "2026-08-20"),
        (
            "agrega la factura de luz de 30 lucas para el 15 de septiembre",
            "luz",
            "30000",
            "2026-09-15",
        ),
        (
            "quiero agendar el gimnasio, son 25000, vence el 3 de septiembre",
            "gimnasio",
            "25000",
            "2026-09-03",
        ),
        # Número plano, sin "lucas" ni "son": antes no se detectaba el monto.
        (
            "agenda el alquiler de 350000 para el 5 de septiembre",
            "alquiler",
            "350000",
            "2026-09-05",
        ),
    ],
)
def test_entiende_frases_naturales(mensaje: str, nombre: str, monto: str, vencimiento: str) -> None:
    campos = _fields(mensaje)

    assert campos["missing_fields"] == []
    assert campos["name"] == nombre
    assert Decimal(campos["amount"]) == Decimal(monto)
    assert campos["due_date"] == vencimiento


def test_el_dia_del_vencimiento_no_se_lee_como_monto() -> None:
    """ "350000 para el 5 de septiembre" tiene dos números y solo uno es plata.

    El tramo de la fecha se recorta antes de buscar el monto; sin eso, "el 5" ganaba.
    """
    campos = _fields("agenda el alquiler de 350000 para el 5 de septiembre")

    assert Decimal(campos["amount"]) == Decimal("350000")


@pytest.mark.parametrize(
    ("mensaje", "esperado"),
    [
        ("agenda el alquiler de 350 lucas para hoy", "2026-08-12"),
        ("agenda el alquiler de 350 lucas para manana", "2026-08-13"),
        ("agenda el alquiler de 350 lucas en 10 dias", "2026-08-22"),
        ("agenda el alquiler de 350 lucas en 2 semanas", "2026-08-26"),
        ("agenda el alquiler de 350 lucas para el mes que viene", "2026-09-12"),
        ("agenda el alquiler de 350 lucas el 5 del mes que viene", "2026-09-05"),
        # Miércoles 12 -> el viernes siguiente es el 14.
        ("agenda el alquiler de 350 lucas para el viernes", "2026-08-14"),
    ],
)
def test_entiende_fechas_relativas(mensaje: str, esperado: str) -> None:
    """Todas se resuelven contra `as_of`, que es el hoy de la zona de negocio."""
    assert _fields(mensaje)["due_date"] == esperado


def test_una_fecha_del_mes_que_ya_paso_salta_al_ano_siguiente() -> None:
    """ "5 de enero" dicho en diciembre es de enero del año que viene."""
    campos = _fields("agenda el alquiler de 350 lucas para el 5 de enero", date(2026, 12, 20))

    assert campos["due_date"] == "2027-01-05"


def test_una_fecha_vencida_del_mes_en_curso_se_conserva() -> None:
    """Vector trata los vencidos como caso normal: es una cuenta que se debe, no un error.

    Saltar al año siguiente agendaría el alquiler para dentro de doce meses, que es peor
    que registrarlo vencido por una semana.
    """
    campos = _fields("agenda el alquiler de 350 lucas para el 5 de agosto")

    assert campos["due_date"] == "2026-08-05"


def test_detecta_la_recurrencia_y_no_la_deja_en_el_nombre() -> None:
    campos = _fields("agenda el gimnasio de 25000 todos los meses el 10 de septiembre")

    assert campos["is_recurring"] is True
    assert campos["name"] == "gimnasio"


def test_sin_recurrencia_explicita_el_compromiso_no_es_recurrente() -> None:
    assert (
        _fields("agenda netflix de 15 lucas para el 20 de agosto").get("is_recurring", False)
        is False
    )


def test_la_categoria_se_deriva_del_nombre() -> None:
    """El vocabulario es el mismo del resto de la aplicación, no una lista aparte."""
    assert _fields("agenda la factura de luz de 30 lucas para el 15 de septiembre")["category"] == (
        "servicios"
    )
    assert _fields("agenda el alquiler de 350 lucas para el 5 de septiembre")["category"] == (
        "vivienda"
    )


def test_un_mensaje_incompleto_no_inventa_datos() -> None:
    """Sin monto ni fecha, se piden: nunca se rellenan con un valor por defecto."""
    campos = _fields("necesito pagar el gimnasio")

    assert set(campos["missing_fields"]) == {"amount", "due_date"}


# ---------- El schema de la tool valida antes de tocar la base ----------


def test_el_schema_rechaza_un_monto_negativo_o_cero() -> None:
    for monto in ("0", "-100"):
        with pytest.raises(ValidationError):
            CreateCommitmentArgs(name="Luz", amount=monto, due_date=date(2026, 9, 1))


def test_el_schema_rechaza_una_fecha_imposible() -> None:
    with pytest.raises(ValidationError):
        CreateCommitmentArgs(name="Luz", amount="1000", due_date="2026-02-30")


def test_el_schema_rechaza_un_nombre_vacio() -> None:
    with pytest.raises(ValidationError):
        CreateCommitmentArgs(name="", amount="1000", due_date=date(2026, 9, 1))


def test_el_schema_no_acepta_user_id_ni_ningun_campo_de_mas() -> None:
    """`extra="forbid"`: el modelo no puede colar el dueño del compromiso."""
    with pytest.raises(ValidationError):
        CreateCommitmentArgs(
            name="Luz",
            amount="1000",
            due_date=date(2026, 9, 1),
            user_id=str(OTHER_USER_ID),
        )


def test_la_tool_esta_declarada_como_escritura() -> None:
    """`writes=True` es lo que hace que el grafo pause antes de persistir."""
    assert TOOLS["create_commitment_draft"].writes is True


# ---------- Integración: aprobación, usuario correcto, sin duplicados ----------

pytestmark = requires_postgres


@pytest.fixture
def copilot(client: TestClient) -> Generator[TestClient, None, None]:
    store = InMemoryDraftStore()
    app.dependency_overrides[get_draft_store] = lambda: store
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider())
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


def _chat(client: TestClient, message: str, conversation_id: str | None = None) -> dict:
    response = client.post(
        f"{API}/ai/chat", json={"message": message, "conversation_id": conversation_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


MENSAJE = "agenda netflix de 15 lucas para el 20 de septiembre"


# ---------- 13. Exige aprobación antes de escribir ----------


def test_el_copiloto_no_persiste_nada_sin_aprobacion(
    copilot: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    body = _chat(copilot, MENSAJE)

    assert body["requires_approval"] is True
    assert body["pending_action"]["kind"] == "create_commitment"
    # Lo importante: hasta acá no se escribió nada.
    assert copilot.get(f"{API}/commitments").json() == []


def test_aprobar_persiste_el_compromiso_con_los_datos_propuestos(
    copilot: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _chat(copilot, MENSAJE)

    approved = copilot.post(
        f"{API}/ai/conversations/{body['conversation_id']}/approve",
        json={"action_id": body["pending_action"]["action_id"]},
    )

    assert approved.status_code == 200, approved.text
    commitments = copilot.get(f"{API}/commitments").json()
    assert len(commitments) == 1
    assert commitments[0]["name"] == "netflix"
    assert Decimal(commitments[0]["amount"]) == Decimal("15000")
    assert commitments[0]["due_date"] == "2026-09-20"
    assert commitments[0]["category"] == "suscripciones"
    assert commitments[0]["status"] == "pending"


def test_la_confirmacion_repite_los_datos_guardados(
    copilot: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Un "listo" a secas no deja detectar que la fecha salió mal."""
    make_profile()
    body = _chat(copilot, MENSAJE)

    approved = copilot.post(
        f"{API}/ai/conversations/{body['conversation_id']}/approve",
        json={"action_id": body["pending_action"]["action_id"]},
    ).json()

    assert "netflix" in approved["answer"].lower()
    assert "2026-09-20" in approved["answer"]
    assert "suscripciones" in approved["answer"]


def test_rechazar_no_persiste_nada(copilot: TestClient, make_profile: Callable[..., dict]) -> None:
    make_profile()
    body = _chat(copilot, MENSAJE)

    copilot.post(
        f"{API}/ai/conversations/{body['conversation_id']}/reject",
        json={"action_id": body["pending_action"]["action_id"]},
    )

    assert copilot.get(f"{API}/commitments").json() == []


# ---------- 16. Un reintento no duplica ----------


def test_aprobar_dos_veces_crea_un_solo_compromiso(
    copilot: TestClient, make_profile: Callable[..., dict]
) -> None:
    """La segunda aprobación no encuentra pausa activa: el borrador ya se consumió.

    Es la misma garantía que protege al doble clic en el botón de aprobar.
    """
    make_profile()
    body = _chat(copilot, MENSAJE)
    action = {"action_id": body["pending_action"]["action_id"]}
    url = f"{API}/ai/conversations/{body['conversation_id']}/approve"

    primera = copilot.post(url, json=action)
    segunda = copilot.post(url, json=action)

    assert primera.status_code == 200
    assert segunda.status_code != 200
    assert len(copilot.get(f"{API}/commitments").json()) == 1


# ---------- 14 y 17. Usuario autenticado y aislamiento ----------


def test_el_compromiso_queda_a_nombre_del_usuario_del_token(
    copilot: TestClient, make_profile: Callable[..., dict]
) -> None:
    make_profile()
    body = _chat(copilot, MENSAJE)

    copilot.post(
        f"{API}/ai/conversations/{body['conversation_id']}/approve",
        json={"action_id": body["pending_action"]["action_id"]},
    )

    creado = copilot.get(f"{API}/commitments").json()[0]
    assert creado["user_id"] == str(TEST_USER_ID)


def test_el_compromiso_del_copiloto_no_lo_ve_la_otra_cuenta(
    client_for: Callable[..., TestClient], make_profile: Callable[..., dict]
) -> None:
    """El alta por chat no es una puerta lateral al aislamiento entre cuentas."""
    store = InMemoryDraftStore()
    app.dependency_overrides[get_draft_store] = lambda: store
    app.dependency_overrides[get_ai_gateway] = lambda: AIGateway(MockAIProvider())
    try:
        # `client_for` reescribe la identidad global en cada llamada, así que se pide un
        # cliente nuevo justo antes de cada petición: guardarse uno en una variable haría
        # que la petición saliera con el usuario de la última llamada, no con el esperado.
        client_for(TEST_USER_ID).put(f"{API}/profile", json=default_profile_payload())
        client_for(OTHER_USER_ID).put(f"{API}/profile", json=default_profile_payload())

        body = _chat(client_for(TEST_USER_ID), MENSAJE)
        client_for(TEST_USER_ID).post(
            f"{API}/ai/conversations/{body['conversation_id']}/approve",
            json={"action_id": body["pending_action"]["action_id"]},
        )

        assert len(client_for(TEST_USER_ID).get(f"{API}/commitments").json()) == 1
        assert client_for(OTHER_USER_ID).get(f"{API}/commitments").json() == []
    finally:
        app.dependency_overrides.pop(get_draft_store, None)
        app.dependency_overrides.pop(get_ai_gateway, None)


# ---------- 18. La escritura no cobra una segunda consulta ----------


def test_aprobar_no_consume_otra_cuota_de_ia(
    copilot: TestClient, make_profile: Callable[..., dict]
) -> None:
    """Aprobar va derecho a `apply_write`: no vuelve a llamar al modelo, no cuesta plata.

    Cobrar de nuevo dejaría acciones pendientes imposibles de resolver al agotar la cuota.
    """
    make_profile()
    body = _chat(copilot, MENSAJE)
    antes = copilot.get(f"{API}/ai/usage").json()["used"]

    copilot.post(
        f"{API}/ai/conversations/{body['conversation_id']}/approve",
        json={"action_id": body["pending_action"]["action_id"]},
    )

    assert copilot.get(f"{API}/ai/usage").json()["used"] == antes
