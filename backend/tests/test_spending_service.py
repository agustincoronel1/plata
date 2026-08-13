"""Totales por período y categoría: el dato del que dependen el atajo y la tool del agente.

Las sumas las hace PostgreSQL. Lo que se prueba acá es que el rango de fechas sea el que la
persona quiso decir —sobre todo "el mes pasado", que antes se resolvía como el mes en curso
y devolvía un total equivocado sin avisar— y que nunca se cuelen movimientos de otra cuenta.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.ai.agent.tools import ToolContext, run_tool
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.models import TransactionType
from app.schemas.transaction import TransactionCreate
from app.services import transaction_service
from app.services.draft_store import InMemoryDraftStore
from app.services.spending_service import Period, parse_period, period_bounds, spending_summary
from tests.conftest import OTHER_USER_ID, TEST_USER_ID, requires_postgres

# ---------- Vocabulario de períodos (sin base) ----------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("cuanto gaste hoy", Period.TODAY),
        ("cuanto gaste esta semana", Period.WEEK),
        ("cuanto gaste este mes", Period.MONTH),
        ("cuanto gaste el mes pasado", Period.PREVIOUS_MONTH),
        ("cuanto gaste el mes anterior", Period.PREVIOUS_MONTH),
        ("y el anterior", Period.PREVIOUS_MONTH),
        ("cuanto gaste", None),
    ],
)
def test_el_periodo_se_lee_del_texto(texto: str, esperado: Period | None) -> None:
    assert parse_period(texto) is esperado


def test_el_mes_pasado_gana_sobre_el_mes_en_curso() -> None:
    """ "El mes pasado" contiene "mes": si se evaluara después, caería en el mes en curso."""
    assert parse_period("cuanto gaste el mes pasado") is Period.PREVIOUS_MONTH


@pytest.mark.parametrize(
    ("period", "hoy", "esperado"),
    [
        (Period.TODAY, date(2026, 8, 13), (date(2026, 8, 13), date(2026, 8, 13))),
        (Period.WEEK, date(2026, 8, 12), (date(2026, 8, 10), date(2026, 8, 16))),
        (Period.MONTH, date(2026, 8, 12), (date(2026, 8, 1), date(2026, 8, 31))),
        (Period.PREVIOUS_MONTH, date(2026, 8, 12), (date(2026, 7, 1), date(2026, 7, 31))),
        # Enero: el mes anterior cambia de año.
        (Period.PREVIOUS_MONTH, date(2026, 1, 5), (date(2025, 12, 1), date(2025, 12, 31))),
        # Marzo sobre un año bisiesto: febrero termina el 29.
        (Period.PREVIOUS_MONTH, date(2028, 3, 10), (date(2028, 2, 1), date(2028, 2, 29))),
    ],
)
def test_el_rango_del_periodo(period: Period, hoy: date, esperado: tuple[date, date]) -> None:
    assert period_bounds(period, hoy) == esperado


# ---------- Sumas contra PostgreSQL ----------

pytestmark_db = requires_postgres


@pytest.fixture
def cargar(db_session: Session) -> Callable[..., None]:
    def _load(user_id: object, occurred_on: date, amount: str, category: str = "comida") -> None:
        transaction_service.create_transaction(
            db_session,
            user_id,
            TransactionCreate(
                type="expense",
                amount=Decimal(amount),
                category=category,
                description="movimiento de prueba",
                occurred_on=occurred_on,
            ),
        )

    return _load


HOY = date(2026, 8, 13)
MES_PASADO = date(2026, 7, 20)


@requires_postgres
def test_el_total_separa_el_mes_en_curso_del_anterior(
    db_session: Session, make_profile: Callable[..., dict], cargar: Callable[..., None]
) -> None:
    make_profile()
    cargar(TEST_USER_ID, HOY, "30000.00")
    cargar(TEST_USER_ID, MES_PASADO, "70000.00")

    actual = spending_summary(db_session, TEST_USER_ID, today=HOY, period=Period.MONTH)
    anterior = spending_summary(db_session, TEST_USER_ID, today=HOY, period=Period.PREVIOUS_MONTH)

    assert actual["total"] == "30000.00"
    assert actual["count"] == 1
    assert anterior["total"] == "70000.00"
    assert anterior["date_from"] == "2026-07-01"
    assert anterior["date_to"] == "2026-07-31"


@requires_postgres
def test_el_total_filtra_por_categoria(
    db_session: Session, make_profile: Callable[..., dict], cargar: Callable[..., None]
) -> None:
    make_profile()
    cargar(TEST_USER_ID, HOY, "30000.00", "comida")
    cargar(TEST_USER_ID, HOY, "20000.00", "transporte")

    comida = spending_summary(db_session, TEST_USER_ID, today=HOY, category="comida")

    assert comida["total"] == "30000.00"
    assert comida["count"] == 1


@requires_postgres
def test_el_total_no_ve_los_movimientos_de_otra_cuenta(
    db_session: Session,
    make_profile: Callable[..., dict],
    client_for: Callable[..., object],
    cargar: Callable[..., None],
) -> None:
    from tests.conftest import API, OTHER_USER_EMAIL, default_profile_payload

    make_profile()
    otra_persona = client_for(OTHER_USER_ID, OTHER_USER_EMAIL)
    otra_persona.put(f"{API}/profile", json=default_profile_payload())
    cargar(TEST_USER_ID, HOY, "30000.00")
    cargar(OTHER_USER_ID, HOY, "999999.00")

    propio = spending_summary(db_session, TEST_USER_ID, today=HOY)

    assert propio["total"] == "30000.00"


@requires_postgres
def test_sin_movimientos_el_total_es_cero_y_no_null(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    make_profile()

    assert spending_summary(db_session, TEST_USER_ID, today=HOY)["total"] == "0.00"


@requires_postgres
def test_los_ingresos_se_piden_aparte(
    db_session: Session, make_profile: Callable[..., dict], cargar: Callable[..., None]
) -> None:
    make_profile()
    cargar(TEST_USER_ID, HOY, "30000.00")

    ingresos = spending_summary(db_session, TEST_USER_ID, today=HOY, tx_type=TransactionType.INCOME)

    assert ingresos["total"] == "0.00"


# ---------- La misma cuenta, vista como tool del agente ----------


@requires_postgres
def test_la_tool_del_agente_devuelve_el_total_del_periodo(
    db_session: Session, make_profile: Callable[..., dict], cargar: Callable[..., None]
) -> None:
    """El agente accede al mismo dato que el atajo, por la tool y con el user del contexto."""
    make_profile()
    cargar(TEST_USER_ID, MES_PASADO, "70000.00")
    ctx = ToolContext(
        session=db_session,
        draft_store=InMemoryDraftStore(),
        gateway=AIGateway(MockAIProvider()),
        as_of=HOY,
        user_id=TEST_USER_ID,
    )

    result = run_tool(ctx, "get_spending_summary", {"period": "previous_month"})

    assert result["ok"] is True
    assert result["data"]["total"] == "70000.00"
    assert result["writes"] is False


@requires_postgres
def test_la_tool_no_acepta_un_user_id_del_modelo(
    db_session: Session, make_profile: Callable[..., dict]
) -> None:
    """El dueño sale del JWT: ningún schema de tool declara user_id y los extras se rechazan."""
    make_profile()
    ctx = ToolContext(
        session=db_session,
        draft_store=InMemoryDraftStore(),
        gateway=AIGateway(MockAIProvider()),
        as_of=HOY,
        user_id=TEST_USER_ID,
    )

    result = run_tool(ctx, "get_spending_summary", {"user_id": str(OTHER_USER_ID)})

    assert result["ok"] is False
    assert result["data"] is None
