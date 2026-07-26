"""Tests del verificador determinístico (sin base)."""

from app.ai.agent.verifier import known_amounts, verify

TOOL_RESULTS = [
    {"name": "get_financial_summary", "ok": True, "data": {"spendable_total": "148000.00"}}
]
EVIDENCE = [{"amount": "12000.00"}]


def test_known_amounts_junta_montos_de_tools_y_evidencia() -> None:
    known = known_amounts(TOOL_RESULTS, EVIDENCE)
    assert 148000 in known
    assert 12000 in known


def test_respuesta_grounded_pasa() -> None:
    ok, reasons = verify(
        answer="Hoy podés gastar $148.000 en total.",
        tool_results=TOOL_RESULTS,
        evidence=EVIDENCE,
        pending_action=None,
        approval_required=False,
    )
    assert ok is True
    assert reasons == []


def test_monto_inventado_se_rechaza() -> None:
    ok, reasons = verify(
        answer="Tenés $999.999 disponibles.",
        tool_results=TOOL_RESULTS,
        evidence=[],
        pending_action=None,
        approval_required=False,
    )
    assert ok is False
    assert any("sin respaldo" in r for r in reasons)


def test_escritura_sin_aprobacion_se_marca() -> None:
    ok, reasons = verify(
        answer="Listo.",
        tool_results=[],
        evidence=[],
        pending_action={"kind": "create_transaction"},
        approval_required=False,
    )
    assert ok is False
