"""Tests de los schemas de Pydantic. No tocan PostgreSQL: validan reglas de contrato."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.commitment import CommitmentCreate, CommitmentUpdate
from app.schemas.profile import ProfileUpdate
from app.schemas.transaction import TransactionCreate, TransactionUpdate

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


# ---------- Perfil ----------


def test_perfil_valido() -> None:
    perfil = ProfileUpdate(
        name="  Agustín  ",
        currency="ars",
        current_balance="620000.00",
        next_income_amount="1200000",
        protected_amount="120000",
        safety_buffer="40000",
    )
    assert perfil.name == "Agustín"  # recortado
    assert perfil.currency == "ARS"  # normalizado a mayúsculas
    assert perfil.current_balance == Decimal("620000.00")
    assert isinstance(perfil.current_balance, Decimal)


def test_perfil_admite_current_balance_negativo() -> None:
    perfil = ProfileUpdate(name="Ana", current_balance="-500.00")
    assert perfil.current_balance == Decimal("-500.00")


def test_perfil_rechaza_protected_amount_negativo() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(name="Ana", protected_amount="-1")


def test_perfil_rechaza_safety_buffer_negativo() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(name="Ana", safety_buffer="-1")


def test_perfil_rechaza_next_income_amount_negativo() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(name="Ana", next_income_amount="-1")


@pytest.mark.parametrize("currency", ["USD", "EUR", "AR", "ARSS", "brl"])
def test_perfil_rechaza_moneda_distinta_de_ars(currency: str) -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(name="Ana", currency=currency)


def test_perfil_rechaza_nombre_vacio() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(name="   ")


def test_perfil_next_income_date_puede_ser_null() -> None:
    perfil = ProfileUpdate(name="Ana", next_income_date=None)
    assert perfil.next_income_date is None


# ---------- Movimientos ----------


def test_movimiento_valido_normaliza_categoria_y_recorta() -> None:
    tx = TransactionCreate(
        type="expense",
        amount="18000.00",
        category="  COMIDA ",
        description="  Compra semanal  ",
        occurred_on=TODAY,
        payment_method="  Mercado Pago ",
    )
    assert tx.category == "comida"
    assert tx.description == "Compra semanal"
    assert tx.payment_method == "Mercado Pago"
    assert isinstance(tx.amount, Decimal)


def test_movimiento_descripcion_vacia_se_vuelve_none() -> None:
    tx = TransactionCreate(type="income", amount="10", category="sueldo", description="   ")
    assert tx.description is None


@pytest.mark.parametrize("amount", ["0", "0.00", "-1"])
def test_movimiento_rechaza_amount_no_positivo(amount: str) -> None:
    with pytest.raises(ValidationError):
        TransactionCreate(type="expense", amount=amount, category="comida")


def test_movimiento_rechaza_fecha_futura() -> None:
    with pytest.raises(ValidationError):
        TransactionCreate(type="expense", amount="10", category="comida", occurred_on=TOMORROW)


def test_movimiento_rechaza_tipo_invalido() -> None:
    with pytest.raises(ValidationError):
        TransactionCreate(type="transfer", amount="10", category="comida")


def test_movimiento_con_categoria_vacia_no_falla_y_cae_en_otros() -> None:
    """La categoría dejó de ser obligatoria en la entrada: sin pistas, el gasto es 'otros'."""
    assert TransactionCreate(type="expense", amount="10", category="   ").category == "otros"


def test_movimiento_patch_vacio_rechazado() -> None:
    with pytest.raises(ValidationError):
        TransactionUpdate()


def test_movimiento_patch_parcial_valido() -> None:
    patch = TransactionUpdate(amount="30000")
    assert patch.model_dump(exclude_unset=True) == {"amount": Decimal("30000")}


def test_movimiento_patch_rechaza_fecha_futura() -> None:
    with pytest.raises(ValidationError):
        TransactionUpdate(occurred_on=TOMORROW)


# ---------- Compromisos ----------


def test_compromiso_valido() -> None:
    commitment = CommitmentCreate(
        name="  Alquiler ",
        amount="250000.00",
        due_date=TODAY,
        category="  Vivienda ",
        is_recurring=True,
    )
    assert commitment.name == "Alquiler"
    assert commitment.category == "vivienda"
    assert commitment.is_recurring is True


def test_compromiso_no_expone_status_en_create() -> None:
    """El status no es parte del alta: siempre nace pending del lado del servidor."""
    assert "status" not in CommitmentCreate.model_fields


@pytest.mark.parametrize("amount", ["0", "-1", "-250000"])
def test_compromiso_rechaza_amount_no_positivo(amount: str) -> None:
    with pytest.raises(ValidationError):
        CommitmentCreate(name="Alquiler", amount=amount, due_date=TODAY, category="vivienda")


def test_compromiso_patch_vacio_rechazado() -> None:
    with pytest.raises(ValidationError):
        CommitmentUpdate()


def test_compromiso_patch_rechaza_status_invalido() -> None:
    with pytest.raises(ValidationError):
        CommitmentUpdate(status="done")


@pytest.mark.parametrize("status", ["pending", "paid", "cancelled"])
def test_compromiso_patch_acepta_status_validos(status: str) -> None:
    patch = CommitmentUpdate(status=status)
    assert patch.status.value == status
