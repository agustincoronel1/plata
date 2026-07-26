"""Verificador determinístico: la respuesta no puede afirmar números sin respaldo.

Reglas (antes de mostrar la respuesta como válida):
- Todo monto mencionado en la respuesta debe existir en los tool results o la evidencia.
- Una escritura pendiente exige que approval_required sea verdadero.
- No se ejecutan escrituras sin aprobación (garantizado por construcción del grafo).

Si falla, la respuesta se reemplaza por un mensaje seguro y se registra el motivo. El modelo
verificador (segunda capa) es opcional; estas reglas mandan.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

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


def known_amounts(tool_results: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> set[int]:
    acc: set[int] = set()
    for result in tool_results:
        _collect_numbers(result.get("data"), acc)
    for ev in evidence:
        _collect_numbers(ev.get("amount"), acc)
    return acc


def verify(
    *,
    answer: str,
    tool_results: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    pending_action: dict[str, Any] | None,
    approval_required: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    known = known_amounts(tool_results, evidence)
    for match in _MONEY_IN_TEXT.findall(answer):
        pesos = _to_int_pesos(match)
        if pesos is not None and pesos not in known:
            reasons.append(f"monto sin respaldo: ${match}")

    if pending_action and not approval_required:
        reasons.append("escritura pendiente sin marca de aprobación")

    return (len(reasons) == 0, reasons)
