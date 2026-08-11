"""Trazabilidad de las operaciones de IA mediante logging estructurado.

Registra metadatos útiles (versión y checksum del prompt, proveedor, modelo, latencia,
tokens, estado, confianza, conteos) SIN datos sensibles. Por defecto NO se loguea el
texto del usuario ni la salida completa (montos, descripciones). Solo si
`AI_LOG_CONTENT=true` —un modo local explícito, apagado en `.env.example`— se agrega el
texto de origen.

No hay plataforma externa de observabilidad: es un log de la aplicación.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from app.core.config import settings

logger = logging.getLogger("app.ai.trace")


def log_parse_trace(
    *,
    trace_id: str,
    draft_id: str,
    prompt_version: str,
    prompt_checksum: str,
    provider: str,
    model: str,
    latency_ms: int,
    status: str,
    confidence: Decimal | None,
    missing_fields_count: int,
    ambiguities_count: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    source_text: str | None = None,
) -> None:
    """Emite una línea de traza JSON de un `parse`. Nunca incluye montos ni descripciones."""
    record: dict[str, object] = {
        "trace_id": trace_id,
        "draft_id": draft_id,
        "task": "transaction_parser",
        "prompt_version": prompt_version,
        "prompt_checksum": prompt_checksum,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "status": status,
        "confidence": str(confidence) if confidence is not None else None,
        "missing_fields_count": missing_fields_count,
        "ambiguities_count": ambiguities_count,
    }

    # Modo local explícito: solo entonces se agrega el texto de origen. Nunca por defecto.
    if settings.ai_log_content and source_text is not None:
        record["source_text"] = source_text

    logger.info("ai_parse %s", json.dumps(record, ensure_ascii=False))


def log_chat_trace(
    *,
    trace_id: str,
    conversation_id: str,
    intent: str,
    tools_used: list[str],
    tool_durations_ms: list[int],
    evidence_count: int,
    approval_required: bool,
    verifier_ok: bool,
    steps: int,
    message: str | None = None,
) -> None:
    """Emite una traza JSON de un turno del copiloto. Sin texto ni montos por defecto.

    Registra intención, tools y sus duraciones, conteo de evidencia, estado del verificador
    y de aprobación, y pasos del grafo. Nunca prompts completos, respuestas crudas, montos,
    descripciones ni API keys.
    """
    record: dict[str, object] = {
        "trace_id": trace_id,
        "conversation_id": conversation_id,
        "task": "copilot_chat",
        "intent": intent,
        "tools_used": tools_used,
        "tool_durations_ms": tool_durations_ms,
        "evidence_count": evidence_count,
        "approval_required": approval_required,
        "verifier_ok": verifier_ok,
        "steps": steps,
    }
    if settings.ai_log_content and message is not None:
        record["message"] = message

    logger.info("ai_chat %s", json.dumps(record, ensure_ascii=False))


def log_fast_path_hit(*, intent: str, period: str | None = None) -> None:
    """Marca que un turno se resolvió sin IA, con la forma de la consulta y nada más.

    Contando estas líneas contra las de `ai_fast_path_miss` sale qué porcentaje de las
    consultas está evitando el modelo, que es lo único que hay que poder medir.

    No viaja el mensaje, ni montos, ni saldos, ni descripciones, ni la categoría
    consultada: con la intención alcanza para la métrica y no se filtra de qué gasta la
    persona. El `user_id` tampoco, igual que en el resto de las trazas.
    """
    suffix = f" period={period}" if period else ""
    logger.info("ai_fast_path_hit intent=%s%s", intent, suffix)


def log_fast_path_miss() -> None:
    """Marca que la consulta no era simple y sigue por el flujo completo del agente."""
    logger.info("ai_fast_path_miss")
