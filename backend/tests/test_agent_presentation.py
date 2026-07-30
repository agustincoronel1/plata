"""Tests de la capa de presentación: formato es-AR, traducción y control de calidad.

Sin base y sin red: se le pasan tool results ya resueltos y se mira el texto que sale.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.ai.agent.brain import (
    asks_daily_budget,
    is_purchase_question,
    mentions_installments,
)
from app.ai.agent.presentation import (
    build_answer,
    day_month,
    full_date,
    internal_leaks,
    is_presentable,
    money,
    money_abs,
    public_tool_output,
    render,
    spoken_date,
)
from app.ai.agent.schemas import AgentIntent

SUMMARY = {
    "current_balance": "92000.00",
    "pending_commitments_amount": "37000.00",
    "protected_amount": "20000.00",
    "safety_buffer": "15000.00",
    "available_real": "20000.00",
    "spendable_total": "20000.00",
    "deficit_amount": "0.00",
    "days_until_income": 6,
    "daily_safe_to_spend": "3333.33",
    "next_income_date": "2026-08-01",
}


def _ctx(name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"tool_results": [{"name": name, "ok": True, "data": data}]}


# ---------- Formato ----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20000.00", "$20.000"),
        ("3333.33", "$3.333"),
        ("1200000.00", "$1.200.000"),
        (Decimal("92000"), "$92.000"),
        ("-3000.00", "-$3.000"),
        (0, "$0"),
        (None, "s/d"),
    ],
)
def test_money_usa_formato_es_ar(value: Any, expected: str) -> None:
    assert money(value) == expected


def test_money_abs_no_muestra_el_signo() -> None:
    assert money_abs("-3000.00") == "$3.000"


@pytest.mark.parametrize(
    ("value", "corto", "largo", "hablado"),
    [
        ("2026-07-29", "29/07", "29/07/2026", "29 de julio"),
        (date(2026, 8, 1), "01/08", "01/08/2026", "1 de agosto"),
    ],
)
def test_fechas_nunca_salen_en_iso(value: Any, corto: str, largo: str, hablado: str) -> None:
    assert day_month(value) == corto
    assert full_date(value) == largo
    assert spoken_date(value) == hablado


# ---------- Detección de fugas ----------


@pytest.mark.parametrize(
    "text",
    [
        "Tu spendable_total es 20000.",
        "El current_balance quedó bajo.",
        "La compra tiene breaks_reserves en true.",
        "El minimum margin es negativo.",
        "Tenés 3 risk months por delante.",
        "Te quedan $20000.00 disponibles.",
        "Te quedan $1,200,000.00 disponibles.",
        "¿Querés esto? ¿O lo otro?",
    ],
)
def test_texto_con_campos_internos_o_formato_crudo_no_es_presentable(text: str) -> None:
    assert internal_leaks(text)
    assert is_presentable(text) is False


def test_texto_humano_es_presentable() -> None:
    text = "Podés gastar hasta $20.000 hoy.\n\nTe quedan 6 días hasta cobrar."
    assert internal_leaks(text) == []
    assert is_presentable(text) is True


def test_respuesta_demasiado_larga_se_rechaza() -> None:
    assert "respuesta demasiado larga" in internal_leaks("Podés gastar. " * 100)


# ---------- Vista pública de una tool ----------


def test_public_tool_output_traduce_y_formatea() -> None:
    view = public_tool_output({"name": "get_financial_summary", "ok": True, "data": SUMMARY})

    assert view["disponible_para_gastar"] == "$20.000"
    assert view["limite_por_dia"] == "$3.333"
    assert view["fecha_en_que_cobras"] == "01/08/2026"
    assert "spendable_total" not in view
    assert "current_balance" not in view


def test_public_tool_output_de_compra_al_contado_no_tiene_cuotas() -> None:
    data = {
        "amount": "18000.00",
        "fits": False,
        "spendable_total": "15000.00",
        "remaining_after_purchase": "0.00",
        "over_budget_amount": "3000.00",
    }
    view = public_tool_output({"name": "check_one_time_purchase", "ok": True, "data": data})

    assert view["monto_de_la_compra"] == "$18.000"
    assert view["cuanto_te_pasarias"] == "$3.000"
    assert not any("cuota" in key for key in view)


# ---------- Armado por intención ----------


def test_disponible_pone_la_decision_primero_y_una_sola_recomendacion() -> None:
    answer = build_answer(AgentIntent.DASHBOARD_SUMMARY, _ctx("get_financial_summary", SUMMARY))

    assert answer.verdict == "yes"
    assert answer.headline == "Podés gastar hasta $20.000 hoy."
    assert answer.recommendation == (
        "Hasta que cobres, tu límite recomendado es de $3.333 por día."
    )
    assert render(answer).splitlines()[0] == answer.headline


def test_sin_fecha_de_ingreso_no_inventa_un_limite_diario() -> None:
    summary = SUMMARY | {"daily_safe_to_spend": None, "days_until_income": None}
    answer = build_answer(AgentIntent.DAILY_BUDGET, _ctx("get_financial_summary", summary))

    assert answer.verdict == "needs_input"
    assert "$" not in answer.headline


def test_disponible_en_cero_avisa_sin_jerga() -> None:
    summary = SUMMARY | {"spendable_total": "0.00", "deficit_amount": "5000.00"}
    answer = build_answer(AgentIntent.DASHBOARD_SUMMARY, _ctx("get_financial_summary", summary))

    assert answer.verdict == "no"
    assert "te faltan $5.000" in answer.explanation
    assert internal_leaks(render(answer)) == []


def test_compromisos_se_listan_con_fecha_corta() -> None:
    data = {
        "commitments": [
            {"name": "internet", "amount": "12000.00", "due_date": "2026-07-29"},
            {"name": "gimnasio", "amount": "10000.00", "due_date": "2026-07-30"},
        ],
        "total": "22000.00",
        "count": 2,
    }
    answer = build_answer(AgentIntent.LIST_COMMITMENTS, _ctx("list_pending_commitments", data))

    assert answer.headline == "Tenés $22.000 en pagos antes de cobrar:"
    assert [(d.label, d.value, d.when) for d in answer.details] == [
        ("Internet", "$12.000", "29/07"),
        ("Gimnasio", "$10.000", "30/07"),
    ]
    assert answer.recommendation is None


def test_simulacion_inviable_traduce_margen_y_meses_de_riesgo() -> None:
    data = {
        "installments": 9,
        "installment_amount": "100000.00",
        "first_installment_date": "2026-07-26",
        "is_viable": False,
        "risk_months_count": 3,
        "minimum_margin": "-50000.00",
        "start_next_month": {"first_installment_date": "2026-08-26", "improves_margin": True},
    }
    answer = build_answer(AgentIntent.SIMULATE_PURCHASE, _ctx("simulate_purchase_preview", data))

    assert answer.headline == "No te conviene comprarlo en 9 cuotas de $100.000."
    assert "en 3 meses" in answer.explanation
    assert "hasta $50.000 en el peor mes" in answer.explanation
    assert answer.recommendation.startswith("Recomendación: empezá a pagar el 26/08/2026")
    assert internal_leaks(render(answer)) == []


def test_sin_datos_devuelve_el_mensaje_seguro() -> None:
    answer = build_answer(AgentIntent.DASHBOARD_SUMMARY, {"tool_results": []})

    assert answer.verdict == "unavailable"
    assert "No pude resolver" in answer.headline


# ---------- Ruteo de compras (detectores del clasificador) ----------


@pytest.mark.parametrize(
    "message",
    [
        "puedo comprar una notebook de 900.000 en 9 cuotas",
        "puedo comprar una heladera financiada",
        "me conviene pagarlo en 12 meses",
        "y si empiezo a pagar el mes que viene",
        "quiero pagar el mes proximo",
    ],
)
def test_menciona_cuotas(message: str) -> None:
    assert mentions_installments(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "puedo gastar 18.000 en ropa hoy",
        "cual es mi situacion financiera",
        "cuanto puedo gastar hoy",
        "me conviene comprar unas zapatillas de 45.000 hoy",
    ],
)
def test_no_menciona_cuotas(message: str) -> None:
    assert mentions_installments(message) is False


def test_detecta_pregunta_de_compra_y_de_limite_diario() -> None:
    assert is_purchase_question("puedo gastar 18.000 en ropa hoy") is True
    assert is_purchase_question("que pagos tengo antes de cobrar") is False
    assert asks_daily_budget("cuanto puedo gastar por dia hasta cobrar") is True
    assert asks_daily_budget("cuanto puedo gastar hoy") is False
