"""Rate limiting: cuántas peticiones entran, y que eso no se confunda con la cuota de IA.

Son dos protecciones distintas y conviven:

- **Cuota diaria de IA** (`ai_daily_usage`): cuánto GASTA una cuenta por día. Solo la tocan
  las operaciones que invocan al modelo.
- **Rate limit** (`rate_limit_counters`): cuántas PETICIONES entran por ventana de tiempo.
  Cuenta todo, haya IA o no.

Las dos responden 429 y se distinguen por el `code` del cuerpo, no por el texto.

La suite general corre con el rate limiting apagado (ver conftest): acá se enciende a
propósito y se bajan los límites para poder alcanzarlos sin hacer cientos de peticiones.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.exceptions import DAILY_LIMIT_CODE
from app.core.config import settings
from app.core.rate_limit import RATE_LIMIT_CODE
from tests.conftest import (
    API,
    OTHER_USER_ID,
    TEST_USER_ID,
    default_profile_payload,
    requires_postgres,
)

pytestmark = requires_postgres


@pytest.fixture
def rate_limited() -> Iterator[None]:
    """Enciende el rate limiting y deja los límites altos salvo el que cada test baje.

    Restaura todo al terminar: si un límite bajo se filtrara al resto de la suite, cualquier
    módulo con varias peticiones empezaría a fallar con 429 sin motivo aparente.
    """
    original = {
        "enabled": settings.rate_limit_enabled,
        "ip": settings.rate_limit_ip_per_minute,
        "auth_ip": settings.rate_limit_auth_ip_per_minute,
        "ai_user": settings.rate_limit_ai_user_per_minute,
        "ai_ip": settings.rate_limit_ai_ip_per_hour,
        "write": settings.rate_limit_write_user_per_minute,
    }
    settings.rate_limit_enabled = True
    settings.rate_limit_ip_per_minute = 1000
    settings.rate_limit_auth_ip_per_minute = 1000
    settings.rate_limit_ai_user_per_minute = 1000
    settings.rate_limit_ai_ip_per_hour = 1000
    settings.rate_limit_write_user_per_minute = 1000
    try:
        yield
    finally:
        settings.rate_limit_enabled = original["enabled"]
        settings.rate_limit_ip_per_minute = original["ip"]
        settings.rate_limit_auth_ip_per_minute = original["auth_ip"]
        settings.rate_limit_ai_user_per_minute = original["ai_user"]
        settings.rate_limit_ai_ip_per_hour = original["ai_ip"]
        settings.rate_limit_write_user_per_minute = original["write"]


# ---------- 9. El rate limit devuelve 429 ----------


def test_el_limite_por_ip_devuelve_429(rate_limited: None, client: TestClient) -> None:
    settings.rate_limit_auth_ip_per_minute = 3

    codigos = [client.get(f"{API}/auth/me").status_code for _ in range(4)]

    assert codigos == [200, 200, 200, 429]


def test_el_429_de_rate_limit_trae_retry_after_y_su_propio_code(
    rate_limited: None, client: TestClient
) -> None:
    """El `code` es lo que impide que el frontend lo confunda con la cuota diaria.

    Uno se resuelve esperando unos segundos; el otro, recién mañana. Mostrar el mensaje
    equivocado mandaría a la persona a esperar un día sin motivo.
    """
    settings.rate_limit_auth_ip_per_minute = 1
    client.get(f"{API}/auth/me")

    respuesta = client.get(f"{API}/auth/me")

    assert respuesta.status_code == 429
    assert respuesta.json()["detail"]["code"] == RATE_LIMIT_CODE
    assert respuesta.json()["detail"]["code"] != DAILY_LIMIT_CODE
    assert int(respuesta.headers["Retry-After"]) >= 1


def test_el_429_no_revela_que_limite_se_alcanzo(rate_limited: None, client: TestClient) -> None:
    """Decir qué límite y cuánto queda le serviría a quien esté calibrando un ataque."""
    settings.rate_limit_auth_ip_per_minute = 1
    client.get(f"{API}/auth/me")

    detail = client.get(f"{API}/auth/me").json()["detail"]

    assert set(detail) == {"code", "message"}
    assert "ip:auth" not in str(detail)


def test_el_limite_por_usuario_es_independiente_del_de_otro_usuario(
    rate_limited: None, client_for: Callable[..., TestClient]
) -> None:
    """El sujeto del límite por cuenta es el `sub` del token, no la IP compartida."""
    settings.rate_limit_write_user_per_minute = 2
    client_for(TEST_USER_ID).put(f"{API}/profile", json=default_profile_payload())
    client_for(TEST_USER_ID).put(f"{API}/profile", json=default_profile_payload())

    # A ya agotó lo suyo.
    de_a = client_for(TEST_USER_ID).put(f"{API}/profile", json=default_profile_payload())
    # B arranca con su cupo intacto, desde la misma IP.
    de_b = client_for(OTHER_USER_ID).put(f"{API}/profile", json=default_profile_payload())

    assert de_a.status_code == 429
    assert de_b.status_code == 200


def test_el_contador_vive_en_postgresql_y_no_en_memoria(
    rate_limited: None, client: TestClient, db_session: Session
) -> None:
    """Es lo que hace que el límite sirva en Render: sobrevive al reinicio y lo comparten
    todas las instancias. Un contador en memoria se perdería y cada proceso contaría aparte.
    """
    settings.rate_limit_auth_ip_per_minute = 5
    client.get(f"{API}/auth/me")

    filas = db_session.execute(
        text("SELECT count(*) FROM rate_limit_counters WHERE scope = 'ip:auth'")
    ).scalar_one()

    assert filas == 1


def test_no_se_guarda_ninguna_ip_en_claro(
    rate_limited: None, client: TestClient, db_session: Session
) -> None:
    """La tabla guarda un HMAC-SHA256, no la dirección. Contar no necesita saber de quién."""
    settings.rate_limit_auth_ip_per_minute = 5
    client.get(f"{API}/auth/me")

    sujetos = list(
        db_session.execute(text("SELECT subject_hash FROM rate_limit_counters")).scalars()
    )

    assert sujetos
    for sujeto in sujetos:
        assert len(sujeto) == 64
        assert all(c in "0123456789abcdef" for c in sujeto)
        # Ni la IP del cliente de test ni el UUID del usuario aparecen en claro.
        assert "testclient" not in sujeto
        assert str(TEST_USER_ID) not in sujeto


def test_si_postgresql_falla_la_peticion_pasa_igual(
    rate_limited: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falla ABIERTO: sin poder contar, se deja pasar.

    El límite protege de un exceso de peticiones, no de una base caída. Cortar acá
    convertiría un problema de PostgreSQL en un 500 en todos los endpoints, incluido
    `/auth/me`, que antes de esto ni siquiera tocaba la base.
    """
    from app.core import rate_limit

    def explota(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("base caída")

    monkeypatch.setattr(rate_limit.Session, "execute", explota, raising=False)

    respuesta = client.get(f"{API}/auth/me")

    assert respuesta.status_code == 200


def test_apagar_el_rate_limiting_lo_desactiva_por_completo(client: TestClient) -> None:
    """`RATE_LIMIT_ENABLED=false` es la salida de emergencia si algo sale mal en la beta."""
    settings.rate_limit_enabled = False
    settings.rate_limit_auth_ip_per_minute = 1

    codigos = [client.get(f"{API}/auth/me").status_code for _ in range(3)]

    assert codigos == [200, 200, 200]


# ---------- 10. Las acciones manuales no consumen la cuota inteligente ----------


def _cuota_usada(db_session: Session, user_id: object) -> int:
    fila = db_session.execute(
        text("SELECT used FROM ai_daily_usage WHERE user_id = :id"), {"id": user_id}
    ).scalar_one_or_none()
    return fila or 0


def test_las_acciones_manuales_no_gastan_cuota_de_ia(
    client: TestClient, make_profile: Callable[..., dict], db_session: Session
) -> None:
    """Crear, editar y borrar a mano, ver el dashboard: nada de eso llama al modelo.

    Es la promesa que sostiene el mensaje del 429 de cuota ("podés seguir usando las
    funciones manuales"). Si una acción manual descontara, esa frase sería mentira.
    """
    make_profile()
    assert _cuota_usada(db_session, TEST_USER_ID) == 0

    creada = client.post(
        f"{API}/transactions",
        json={"type": "expense", "amount": "1000", "category": "comida"},
    )
    assert creada.status_code == 201
    client.patch(f"{API}/transactions/{creada.json()['id']}", json={"amount": "2000"})
    client.delete(f"{API}/transactions/{creada.json()['id']}")
    client.post(
        f"{API}/commitments",
        json={"name": "Luz", "amount": "5000", "due_date": "2026-08-10"},
    )
    client.get(f"{API}/dashboard/summary")
    client.get(f"{API}/transactions")
    client.post(
        f"{API}/simulations/purchase",
        json={
            "purchase_name": "Notebook",
            "total_amount": "600000",
            "installments": 6,
            "first_installment_date": "2026-08-15",
        },
    )

    assert _cuota_usada(db_session, TEST_USER_ID) == 0


def test_el_rate_limit_no_toca_la_cuota_de_ia(
    rate_limited: None, client: TestClient, make_profile: Callable[..., dict], db_session: Session
) -> None:
    """Son contadores separados: gastar el rate limit no gasta consultas inteligentes."""
    make_profile()
    settings.rate_limit_write_user_per_minute = 2
    for _ in range(4):
        client.post(
            f"{API}/transactions",
            json={"type": "expense", "amount": "1000", "category": "comida"},
        )

    assert _cuota_usada(db_session, TEST_USER_ID) == 0
