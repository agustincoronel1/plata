"""Carga los datos de demostración.

Ejecutar desde `backend/`:

    python -m app.scripts.seed_demo

Es idempotente: los registros llevan UUID fijos y el script solo inserta los que
faltan. Nunca borra ni sobrescribe, así que los cambios que hagas sobre un registro
demo sobreviven a una nueva ejecución.

No es un script de producción: crea un perfil con datos inventados y está pensado para
desarrollo y demo.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import (
    Commitment,
    CommitmentStatus,
    PurchaseSimulation,
    Transaction,
    TransactionType,
    UserProfile,
)

# UUID fijos: son la clave de la idempotencia. No cambiarlos, porque una base ya
# sembrada quedaría con registros duplicados.
#
# DEMO_USER_ID vive acá y en ningún otro lado. Es del perfil que crea ESTE script, no una
# identidad del sistema: ningún módulo de producción lo importa, ninguna función lo toma
# como valor por defecto y el flujo de IA jamás lo usa como respaldo. El usuario de una
# petición sale siempre del JWT verificado (`app.core.security`).
DEMO_USER_ID = UUID("11111111-1111-4111-8111-111111111111")

SUPERMARKET_TRANSACTION_ID = UUID("22222222-2222-4222-8222-222222222201")
FUEL_TRANSACTION_ID = UUID("22222222-2222-4222-8222-222222222202")
DELIVERY_TRANSACTION_ID = UUID("22222222-2222-4222-8222-222222222203")

RENT_COMMITMENT_ID = UUID("33333333-3333-4333-8333-333333333301")
UTILITIES_COMMITMENT_ID = UUID("33333333-3333-4333-8333-333333333302")
CREDIT_CARD_COMMITMENT_ID = UUID("33333333-3333-4333-8333-333333333303")

EXPECTED_COUNTS = {
    UserProfile: 1,
    Transaction: 3,
    Commitment: 3,
    PurchaseSimulation: 0,
}


def first_day_of_next_month(today: date) -> date:
    """Primer día del mes siguiente. En diciembre salta al año siguiente."""
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def build_user_profile(today: date) -> UserProfile:
    return UserProfile(
        id=DEMO_USER_ID,
        name="Agustín Demo",
        currency="ARS",
        current_balance=Decimal("620000.00"),
        next_income_amount=Decimal("1200000.00"),
        next_income_date=first_day_of_next_month(today),
        protected_amount=Decimal("120000.00"),
        safety_buffer=Decimal("40000.00"),
    )


def build_transactions(today: date) -> list[Transaction]:
    return [
        Transaction(
            id=SUPERMARKET_TRANSACTION_ID,
            user_id=DEMO_USER_ID,
            type=TransactionType.EXPENSE,
            amount=Decimal("18000.00"),
            category="comida",
            description="Compra semanal",
            occurred_on=today,
            payment_method="Mercado Pago",
        ),
        Transaction(
            id=FUEL_TRANSACTION_ID,
            user_id=DEMO_USER_ID,
            type=TransactionType.EXPENSE,
            amount=Decimal("24000.00"),
            category="transporte",
            description="Carga de combustible",
            occurred_on=today - timedelta(days=1),
            payment_method="Tarjeta de débito",
        ),
        Transaction(
            id=DELIVERY_TRANSACTION_ID,
            user_id=DEMO_USER_ID,
            type=TransactionType.EXPENSE,
            amount=Decimal("12500.00"),
            category="comida",
            description="Pedido de comida",
            occurred_on=today - timedelta(days=2),
            payment_method="Mercado Pago",
        ),
    ]


def build_commitments(today: date) -> list[Commitment]:
    return [
        Commitment(
            id=RENT_COMMITMENT_ID,
            user_id=DEMO_USER_ID,
            name="Alquiler",
            amount=Decimal("250000.00"),
            due_date=today + timedelta(days=5),
            category="vivienda",
            status=CommitmentStatus.PENDING,
            is_recurring=True,
        ),
        Commitment(
            id=UTILITIES_COMMITMENT_ID,
            user_id=DEMO_USER_ID,
            name="Servicios",
            amount=Decimal("60000.00"),
            due_date=today + timedelta(days=10),
            category="servicios",
            status=CommitmentStatus.PENDING,
            is_recurring=True,
        ),
        Commitment(
            id=CREDIT_CARD_COMMITMENT_ID,
            user_id=DEMO_USER_ID,
            name="Tarjeta",
            amount=Decimal("100000.00"),
            due_date=today + timedelta(days=15),
            category="tarjeta",
            status=CommitmentStatus.PENDING,
            is_recurring=False,
        ),
    ]


def _insert_if_missing(session: Session, records: list) -> tuple[int, int]:
    """Inserta solo los registros cuyo UUID todavía no existe.

    Devuelve (creados, ya existentes). Nunca modifica un registro existente.
    """
    created = 0
    existing = 0

    for record in records:
        if session.get(type(record), record.id) is None:
            session.add(record)
            created += 1
        else:
            existing += 1

    return created, existing


def seed(session: Session, today: date | None = None) -> None:
    today = today or date.today()

    user_created, user_existing = _insert_if_missing(session, [build_user_profile(today)])
    # El flush deja el perfil disponible para las FK de los hijos dentro de la misma
    # transacción, sin cerrarla.
    session.flush()

    tx_created, tx_existing = _insert_if_missing(session, build_transactions(today))
    cm_created, cm_existing = _insert_if_missing(session, build_commitments(today))

    for etiqueta, creados, existentes in (
        ("Perfil", user_created, user_existing),
        ("Transacciones", tx_created, tx_existing),
        ("Compromisos", cm_created, cm_existing),
    ):
        if creados and existentes:
            print(f"  {etiqueta:<15} {creados} creado/s, {existentes} ya existía/n")
        elif creados:
            print(f"  {etiqueta:<15} {creados} creado/s")
        else:
            print(f"  {etiqueta:<15} sin cambios, {existentes} ya existía/n")

    if creados_totales := user_created + tx_created + cm_created:
        print(f"\nSe crearon {creados_totales} registro/s.")
    else:
        print("\nLos datos demo ya estaban cargados. No se modificó nada.")


def report_counts(session: Session) -> None:
    """Muestra cuántas filas hay por tabla, y cuántas son del perfil demo."""
    print("\n  tabla                    demo  total")
    for model, esperado in EXPECTED_COUNTS.items():
        total = session.scalar(select(func.count()).select_from(model))

        if model is UserProfile:
            demo = session.scalar(
                select(func.count()).select_from(model).where(model.id == DEMO_USER_ID)
            )
        else:
            demo = session.scalar(
                select(func.count()).select_from(model).where(model.user_id == DEMO_USER_ID)
            )

        marca = "" if demo == esperado else f"  <-- se esperaban {esperado}"
        print(f"  {model.__tablename__:<22} {demo:>4}  {total:>5}{marca}")


def main() -> None:
    print("Cargando datos de demostración...\n")
    session = SessionLocal()
    try:
        seed(session)
        session.commit()
        report_counts(session)
    except Exception:
        session.rollback()
        print("\nError durante el seed. No se guardó nada.")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
