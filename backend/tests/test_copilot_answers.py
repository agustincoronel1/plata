"""Calidad de las respuestas del copiloto: cortas, en castellano y sin campos internos.

Escenario fijo (as_of 26/07/2026) elegido para que los números den redondos y las
respuestas se puedan comparar literalmente:

    saldo 92.000 - compromisos 37.000 - protegido 20.000 - colchón 15.000 = 20.000

Las seis consultas del producto se ejercitan de punta a punta contra el motor financiero
real (mock solo del proveedor de IA). Los tests no verifican los cálculos —eso ya lo hace
`test_financial_engine`— sino cómo se le cuentan a la persona.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.ai.agent.presentation import internal_leaks
from app.ai.agent.schemas import ChatResponse
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.schemas.commitment import CommitmentCreate
from app.services import ai_chat_service, commitment_service
from app.services.draft_store import InMemoryDraftStore
from tests.conftest import TEST_USER_ID, requires_postgres

pytestmark = requires_postgres

AS_OF = date(2026, 7, 26)
NEXT_INCOME = date(2026, 8, 1)

# Nombres internos que jamás pueden aparecer en una respuesta.
FORBIDDEN = (
    "spendable_total",
    "current_balance",
    "available_real",
    "daily_safe_to_spend",
    "breaks_reserves",
    "fits_within_reserves",
    "is_viable",
    "minimum_margin",
    "risk_months",
    "conclusion",
    "get_financial_summary",
    "check_one_time_purchase",
    "simulate_purchase_preview",
    "list_pending_commitments",
)

COMMITMENTS = (
    ("Internet", "12000", date(2026, 7, 29)),
    ("Gimnasio", "10000", date(2026, 7, 30)),
    ("Tarjeta", "15000", date(2026, 7, 31)),
)


@pytest.fixture
def scenario(
    db_session: Session, make_profile: Callable[..., dict]
) -> Callable[..., Callable[[str], ChatResponse]]:
    """Devuelve un `ask(mensaje)` sobre un perfil con el saldo que pida cada test."""

    def _setup(balance: str = "92000.00") -> Callable[[str], ChatResponse]:
        make_profile(
            current_balance=balance,
            next_income_amount="500000.00",
            next_income_date=NEXT_INCOME.isoformat(),
            protected_amount="20000.00",
            safety_buffer="15000.00",
        )
        for name, amount, due in COMMITMENTS:
            commitment_service.create_commitment(
                db_session,
                TEST_USER_ID,
                CommitmentCreate(
                    name=name, amount=Decimal(amount), due_date=due, category="servicios"
                ),
            )

        def _ask(message: str) -> ChatResponse:
            return ai_chat_service.chat(
                db_session,
                message,
                as_of=AS_OF,
                draft_store=InMemoryDraftStore(),
                gateway=AIGateway(MockAIProvider()),
                user_id=TEST_USER_ID,
            )

        return _ask

    return _setup


def assert_human(response: ChatResponse) -> None:
    """Toda respuesta: sin campos internos, sin ISO, breve y con una sola sugerencia."""
    answer = response.answer
    assert internal_leaks(answer) == [], answer
    for word in FORBIDDEN:
        assert word not in answer, f"{word} filtrado en: {answer}"
    assert "2026-" not in answer, answer
    # Nada de "20000.00" ni "1,200,000.00": los montos van en formato es-AR.
    assert re.search(r"\.\d{2}(?!\d)", answer) is None, answer
    assert re.search(r"\d,\d{3}", answer) is None, answer
    assert answer.count("\n\n") <= 3, answer
    assert len([line for line in answer.splitlines() if line.strip()]) <= 8, answer
    # A lo sumo UNA sugerencia siguiente, y siempre la que armó la capa de presentación.
    assert answer.count("Recomendación") <= 1, answer
    structured = response.structured_answer
    assert structured is not None
    if structured.recommendation:
        assert answer.endswith(structured.recommendation), answer


def test_cuanto_puedo_gastar_hoy(scenario) -> None:
    response = scenario()("¿Cuánto puedo gastar hoy?")

    assert response.intent.value == "dashboard_summary"
    assert response.answer.startswith("Podés gastar hasta $20.000 hoy.")
    assert "tu límite recomendado es de $3.333 por día" in response.answer
    assert response.structured_answer.verdict == "yes"
    assert_human(response)


def test_cuanto_puedo_gastar_por_dia(scenario) -> None:
    response = scenario()("¿Cuánto puedo gastar por día hasta cobrar?")

    assert response.intent.value == "daily_budget"
    assert response.answer.startswith(
        "Podés gastar aproximadamente $3.333 por día hasta el 1 de agosto."
    )
    assert "Tenés $20.000 disponibles y faltan 6 días para tu próximo ingreso." in response.answer
    assert_human(response)


def test_pagos_antes_de_cobrar(scenario) -> None:
    response = scenario()("¿Qué pagos tengo antes de cobrar?")

    assert response.intent.value == "list_commitments"
    assert response.answer.startswith("Tenés $37.000 en pagos antes de cobrar:")
    assert "- Internet: $12.000 — 29/07" in response.answer
    assert "- Gimnasio: $10.000 — 30/07" in response.answer
    assert "- Tarjeta: $15.000 — 31/07" in response.answer
    assert [d.when for d in response.structured_answer.details] == ["29/07", "30/07", "31/07"]
    assert_human(response)


def test_compra_al_contado_que_no_conviene(scenario) -> None:
    # Saldo 87.000 → disponible seguro 15.000, así la compra de 18.000 se pasa por 3.000.
    response = scenario("87000.00")("¿Puedo gastar 18.000 en ropa hoy?")

    assert response.intent.value == "one_time_purchase"
    assert [t.name for t in response.tools_used] == ["check_one_time_purchase"]
    assert response.answer.startswith("No te conviene gastar $18.000 hoy.")
    assert "Tu límite seguro es de $15.000." in response.answer
    assert "quedarías $3.000 por debajo de tu colchón de seguridad" in response.answer
    assert "Recomendación: gastá hasta $15.000 o esperá al próximo ingreso." in response.answer
    assert response.structured_answer.verdict == "no"
    assert_human(response)


def test_compra_al_contado_que_si_conviene(scenario) -> None:
    response = scenario()("¿Puedo gastar 8.000 en ropa hoy?")

    assert response.intent.value == "one_time_purchase"
    assert response.answer.startswith("Sí, podés gastar $8.000 hoy.")
    assert "te quedarían $12.000 disponibles hasta cobrar" in response.answer
    assert response.structured_answer.verdict == "yes"
    assert_human(response)


@pytest.mark.parametrize(
    "message",
    ["¿Puedo gastar 18.000 en ropa hoy?", "¿Puedo gastar 8.000 en ropa hoy?"],
)
def test_compra_al_contado_nunca_habla_de_cuotas(scenario, message: str) -> None:
    response = scenario()(message)

    assert "simulate_purchase_preview" not in [t.name for t in response.tools_used]
    lowered = response.answer.lower()
    for word in ("cuota", "cuotas", "primera cuota", "financi", "meses", "risk_months"):
        assert word not in lowered, f"'{word}' en una compra al contado: {response.answer}"


def test_compra_en_cuotas_usa_el_simulador(scenario) -> None:
    response = scenario()("¿Puedo comprar una notebook de 900.000 en 9 cuotas?")

    assert response.intent.value == "simulate_purchase"
    assert [t.name for t in response.tools_used] == ["simulate_purchase_preview"]
    assert "9 cuotas de $100.000" in response.answer
    assert response.structured_answer.verdict in ("yes", "no")
    assert_human(response)


def test_como_lo_resolvi_es_humano(scenario) -> None:
    response = scenario()("¿Cuánto puedo gastar hoy?")

    how = response.structured_answer.how_i_solved_it
    assert how == (
        "Tomé tu saldo de $92.000 y resté $37.000 de compromisos, $20.000 protegidos y "
        "$15.000 de colchón. El resultado es un disponible seguro de $20.000."
    )
    assert internal_leaks(how) == []
    assert "{" not in how and "get_financial_summary" not in how


def test_las_respuestas_no_inventan_numeros(scenario) -> None:
    """Todo monto mostrado sale de los tool results: el verificador ya corre en el grafo."""
    ask = scenario()
    for message in (
        "¿Cuánto puedo gastar hoy?",
        "¿Cuánto puedo gastar por día hasta cobrar?",
        "¿Qué pagos tengo antes de cobrar?",
        "¿Puedo gastar 8.000 en ropa hoy?",
        "¿Puedo comprar una notebook de 900.000 en 9 cuotas?",
    ):
        response = ask(message)
        assert "No pude verificar" not in response.answer, message
        assert "No pude resolver" not in response.answer, message
