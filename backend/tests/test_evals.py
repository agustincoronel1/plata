"""Los evaluadores offline corren con el mock y reportan métricas válidas (sin coste)."""

from app.ai.evaluators import (
    grounded_answers,
    hybrid_retrieval,
    intent_routing,
    prompt_injection,
    tool_selection,
    transaction_parser,
)


def test_transaction_parser_eval_pasa_con_mock() -> None:
    assert transaction_parser.run("mock") == 0


def test_intent_routing_eval_pasa() -> None:
    assert intent_routing.run() == 0


def test_tool_selection_eval_pasa() -> None:
    assert tool_selection.run() == 0


def test_prompt_injection_eval_pasa() -> None:
    assert prompt_injection.run() == 0


def test_hybrid_retrieval_eval_pasa() -> None:
    # Se auto-omite (exit 0) si no hay PostgreSQL; con base, exige métricas en verde.
    assert hybrid_retrieval.run() == 0


def test_grounded_answers_eval_pasa() -> None:
    assert grounded_answers.run() == 0
