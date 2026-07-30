"""Redacción grounded: arma el texto SOLO con números que ya vienen en los tool results.

El modelo no inventa montos: la capa de presentación (`presentation.py`) lee los resultados
determinísticos de las tools, elige qué mostrar y lo redacta en español rioplatense. Si un
dato no está, no se afirma.

Este módulo es la fachada estable que usan el cerebro mock y los tests.
"""

from __future__ import annotations

from typing import Any

from app.ai.agent.presentation import build_answer, render
from app.ai.agent.schemas import AgentIntent, StructuredAnswer


def render_answer(intent: AgentIntent, context: dict[str, Any]) -> str:
    """Texto final para la persona, armado desde la respuesta estructurada."""
    return render(build_answer(intent, context))


def structured_answer(intent: AgentIntent, context: dict[str, Any]) -> StructuredAnswer:
    """Respuesta campo por campo (verdict, headline, explicación, recomendación, detalle)."""
    return build_answer(intent, context)
