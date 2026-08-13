"""Verificador determinístico: la respuesta no puede afirmar números sin respaldo.

La regla de fondo no cambió: **un monto que la persona lee tiene que venir de sus datos.**
Lo que cambió es a qué se le exige qué, porque no todos los turnos son iguales:

- Un turno con datos (`deterministic`, `simulation`, `mixed`, `action`) afirma cifras de la
  persona: cada monto del texto tiene que estar en los tool results o en la evidencia.
- Un turno conversacional no mira ningún dato, así que no puede haber NINGÚN monto: sin
  algo detrás, "$850.000" se lee como el saldo de quien pregunta. Es la misma regla llevada
  al extremo correcto, no una excepción.
- Los montos de la ÚLTIMA respuesta siguen valiendo un turno más. Sin esto, "¿por qué me
  dijiste que no me convenía?" quedaba bloqueado por repetir un número que el propio
  copiloto calculó y verificó un minuto antes. Es una ventana de un turno a propósito: una
  allowlist con todo lo dicho en la conversación terminaría avalando una afirmación nueva
  con un número viejo que no tiene nada que ver.

Además, ninguna respuesta puede exponer campos internos ni jerga (`presentation`), y una
escritura pendiente exige la marca de aprobación.

Si algo falla, quien llama decide cómo recuperarse (plantilla determinística o mensaje
conversacional); acá solo se dice qué está mal. El modelo verificador (segunda capa) es
opcional; estas reglas mandan.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.ai.agent.presentation import internal_leaks
from app.ai.agent.schemas import GROUNDED_ROUTES, AgentRoute

_MONEY_IN_TEXT = re.compile(r"\$\s?(\d[\d.,]*)")


def _to_int_pesos(raw: str) -> int | None:
    """Convierte un monto escrito ($25.000 o $25000.00) a pesos enteros.

    Quita separadores de miles (punto/coma seguidos de exactamente 3 dígitos) y luego
    interpreta lo que quede (incluido un decimal .NN) como Decimal.
    """
    without_thousands = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", raw.strip())
    try:
        return int(Decimal(without_thousands))
    except (ArithmeticError, ValueError):
        return None


def _collect_numbers(value: Any, acc: set[int]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, Decimal)):
        acc.add(int(value))
    elif isinstance(value, str):
        # Los montos vienen como Decimal serializado ("440000.00") o entero ("8"): tomamos
        # los pesos enteros. NO tratamos separadores de miles acá (no aparecen en los datos).
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            acc.add(int(Decimal(value)))
    elif isinstance(value, dict):
        for item in value.values():
            _collect_numbers(item, acc)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_numbers(item, acc)


def known_amounts(
    tool_results: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    previously_verified: list[int] | None = None,
) -> set[int]:
    acc: set[int] = set()
    for result in tool_results:
        _collect_numbers(result.get("data"), acc)
    for ev in evidence:
        _collect_numbers(ev.get("amount"), acc)
    # Los montos de la última respuesta mostrada (y solo esos): salieron de una tool y se
    # verificaron en ese turno, así que un seguimiento puede repetirlos.
    acc |= {int(value) for value in previously_verified or []}
    # Un margen negativo se cuenta en positivo ("quedarías $3.000 por debajo"): el valor
    # absoluto de un número respaldado sigue estando respaldado.
    return acc | {abs(value) for value in acc}


def amounts_in(answer: str) -> list[int]:
    """Los montos que el texto le muestra a la persona, en pesos enteros."""
    found = [_to_int_pesos(match) for match in _MONEY_IN_TEXT.findall(answer)]
    return [value for value in found if value is not None]


def verify(
    *,
    answer: str,
    tool_results: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    pending_action: dict[str, Any] | None,
    approval_required: bool,
    route: AgentRoute = AgentRoute.DETERMINISTIC,
    previously_verified: list[int] | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    conversational = route not in GROUNDED_ROUTES

    known = known_amounts(tool_results, evidence, previously_verified)
    for pesos in amounts_in(answer):
        if pesos not in known:
            reasons.append(f"monto sin respaldo: ${pesos}")

    reasons.extend(internal_leaks(answer, conversational=conversational))

    if pending_action and not approval_required:
        reasons.append("escritura pendiente sin marca de aprobación")

    return (len(reasons) == 0, reasons)
