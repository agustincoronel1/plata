"""Tests de la lógica auxiliar del seed. No tocan PostgreSQL.

La prueba de idempotencia contra la base real se hace a mano ejecutando el script dos
veces con el contenedor levantado.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.models import (
    Commitment,
    CommitmentStatus,
    PurchaseSimulation,
    Transaction,
    TransactionType,
    UserProfile,
)
from app.scripts import seed_demo
from app.scripts.seed_demo import (
    DEMO_USER_ID,
    EXPECTED_COUNTS,
    build_commitments,
    build_transactions,
    build_user_profile,
    first_day_of_next_month,
)

DEMO_UUIDS = [
    seed_demo.DEMO_USER_ID,
    seed_demo.SUPERMARKET_TRANSACTION_ID,
    seed_demo.FUEL_TRANSACTION_ID,
    seed_demo.DELIVERY_TRANSACTION_ID,
    seed_demo.RENT_COMMITMENT_ID,
    seed_demo.UTILITIES_COMMITMENT_ID,
    seed_demo.CREDIT_CARD_COMMITMENT_ID,
]

TODAY = date(2026, 7, 21)


@pytest.mark.parametrize(
    ("hoy", "esperado"),
    [
        (date(2026, 7, 21), date(2026, 8, 1)),
        (date(2026, 7, 1), date(2026, 8, 1)),
        (date(2026, 7, 31), date(2026, 8, 1)),
        (date(2026, 1, 15), date(2026, 2, 1)),
        (date(2026, 11, 30), date(2026, 12, 1)),
        # Enero del año siguiente.
        (date(2026, 12, 1), date(2027, 1, 1)),
        (date(2026, 12, 31), date(2027, 1, 1)),
        # Año bisiesto: febrero de 2028 tiene 29 días.
        (date(2028, 2, 29), date(2028, 3, 1)),
    ],
)
def test_primer_dia_del_mes_siguiente(hoy: date, esperado: date) -> None:
    assert first_day_of_next_month(hoy) == esperado


def test_primer_dia_del_mes_siguiente_siempre_es_futuro_y_dia_uno() -> None:
    dia = date(2026, 1, 1)
    while dia < date(2029, 1, 1):
        siguiente = first_day_of_next_month(dia)
        assert siguiente.day == 1
        assert siguiente > dia
        dia += timedelta(days=1)


def test_los_uuid_demo_son_validos_y_unicos() -> None:
    assert len(set(DEMO_UUIDS)) == len(DEMO_UUIDS)
    for identificador in DEMO_UUIDS:
        assert isinstance(identificador, UUID)
        # Round-trip: descarta un UUID mal escrito.
        assert UUID(str(identificador)) == identificador


def test_el_conjunto_esperado_es_1_3_3_0() -> None:
    assert EXPECTED_COUNTS == {
        UserProfile: 1,
        Transaction: 3,
        Commitment: 3,
        PurchaseSimulation: 0,
    }


def test_los_builders_generan_la_cantidad_esperada() -> None:
    assert len(build_transactions(TODAY)) == EXPECTED_COUNTS[Transaction]
    assert len(build_commitments(TODAY)) == EXPECTED_COUNTS[Commitment]


def test_todos_los_montos_son_decimal() -> None:
    perfil = build_user_profile(TODAY)
    montos = [
        perfil.current_balance,
        perfil.next_income_amount,
        perfil.protected_amount,
        perfil.safety_buffer,
    ]
    montos += [transaccion.amount for transaccion in build_transactions(TODAY)]
    montos += [compromiso.amount for compromiso in build_commitments(TODAY)]

    for monto in montos:
        assert isinstance(monto, Decimal), f"{monto!r} no es Decimal"
        assert not isinstance(monto, float)


def test_perfil_demo() -> None:
    perfil = build_user_profile(TODAY)

    assert perfil.id == DEMO_USER_ID
    assert perfil.name == "Agustín Demo"
    assert perfil.currency == "ARS"
    assert perfil.current_balance == Decimal("620000.00")
    assert perfil.next_income_amount == Decimal("1200000.00")
    assert perfil.next_income_date == date(2026, 8, 1)
    assert perfil.protected_amount == Decimal("120000.00")
    assert perfil.safety_buffer == Decimal("40000.00")


def test_transacciones_demo() -> None:
    supermercado, nafta, delivery = build_transactions(TODAY)

    assert all(t.type is TransactionType.EXPENSE for t in (supermercado, nafta, delivery))
    assert all(t.user_id == DEMO_USER_ID for t in (supermercado, nafta, delivery))

    assert (supermercado.amount, supermercado.category) == (Decimal("18000.00"), "comida")
    assert supermercado.occurred_on == TODAY
    assert supermercado.payment_method == "Mercado Pago"

    assert (nafta.amount, nafta.category) == (Decimal("24000.00"), "transporte")
    assert nafta.occurred_on == TODAY - timedelta(days=1)

    assert (delivery.amount, delivery.category) == (Decimal("12500.00"), "comida")
    assert delivery.occurred_on == TODAY - timedelta(days=2)


def test_compromisos_demo() -> None:
    alquiler, servicios, tarjeta = build_commitments(TODAY)

    assert all(c.status is CommitmentStatus.PENDING for c in (alquiler, servicios, tarjeta))
    assert all(c.user_id == DEMO_USER_ID for c in (alquiler, servicios, tarjeta))

    assert (alquiler.amount, alquiler.category, alquiler.is_recurring) == (
        Decimal("250000.00"),
        "vivienda",
        True,
    )
    assert alquiler.due_date == TODAY + timedelta(days=5)

    assert (servicios.amount, servicios.category, servicios.is_recurring) == (
        Decimal("60000.00"),
        "servicios",
        True,
    )
    assert servicios.due_date == TODAY + timedelta(days=10)

    assert (tarjeta.amount, tarjeta.category, tarjeta.is_recurring) == (
        Decimal("100000.00"),
        "tarjeta",
        False,
    )
    assert tarjeta.due_date == TODAY + timedelta(days=15)


def test_el_seed_no_crea_simulaciones() -> None:
    assert EXPECTED_COUNTS[PurchaseSimulation] == 0
    assert not hasattr(seed_demo, "build_purchase_simulations")
