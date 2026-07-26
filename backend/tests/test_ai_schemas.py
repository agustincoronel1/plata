"""Tests de los schemas del flujo de IA (sin base)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.ai_transaction import (
    ParsedTransactionDraft,
    TransactionConfirmRequest,
    TransactionParseModelOutput,
    TransactionParseRequest,
)


def test_confidence_fuera_de_rango_falla() -> None:
    with pytest.raises(ValidationError):
        TransactionParseModelOutput(intent="create_transaction", confidence=Decimal("5"))


def test_confidence_valida() -> None:
    out = TransactionParseModelOutput(intent="unknown", confidence=Decimal("0.5"))
    assert out.confidence == Decimal("0.5")


def test_categoria_se_normaliza_a_minusculas() -> None:
    draft = ParsedTransactionDraft(category="  Transporte ")
    assert draft.category == "transporte"


def test_parse_request_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        TransactionParseRequest(text="un gasto", amount="1000")


def test_parse_request_texto_minimo() -> None:
    with pytest.raises(ValidationError):
        TransactionParseRequest(text="a")


def test_confirm_request_rechaza_campos_extra() -> None:
    with pytest.raises(ValidationError):
        TransactionConfirmRequest(confirmed=True, user_id="x")


def test_correcciones_rechazan_monto_negativo() -> None:
    with pytest.raises(ValidationError):
        TransactionConfirmRequest(confirmed=True, corrections={"amount": "-5"})
