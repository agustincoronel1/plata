"""Smoke test de validación real: OpenAI de verdad, con presupuesto y limpieza.

Es el único punto del proyecto que gasta llamadas pagas. Por eso:

- **No corre nunca por accidente**: exige `RUN_REAL_AI_TESTS=1` y una configuración real
  completa. Si falta algo, no hace ni una llamada y dice exactamente qué variable falta.
- **Tiene presupuesto duro**: `REAL_AI_MAX_CALLS` (default 12) cuenta cada request al
  proveedor (chat y embeddings). El tope cuenta **requests reales, sin reintentos
  automáticos**: por eso el smoke fuerza `REAL_AI_MAX_RETRIES=0` por default, así una
  llamada contada es exactamente una llamada facturada. Un escenario que no entra en lo que
  queda se marca SKIPPED, y eso hace que el script salga con código ≠ 0: quedarse sin
  presupuesto no es un éxito. El único SKIPPED aceptable es el del RAG cuando
  `AI_EMBEDDING_PROVIDER` no es `openai`.
- **No filtra nada**: se registran proveedor, modelo, duración, tokens y PASS/FAIL. Nunca
  la API key, los prompts, el texto financiero ni las respuestas del modelo.
- **Limpia lo que crea**: todo dato temporal se borra en un `finally`, aun si un escenario
  falla en el medio, y siempre a través de los servicios (el saldo se restaura solo).

Uso:

    RUN_REAL_AI_TESTS=1 python -m app.scripts.real_ai_smoke
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from app.ai.agent.graph import close_checkpointer, get_compiled_graph
from app.ai.agent.presentation import internal_leaks
from app.ai.agent.tools import TOOLS, is_write_tool
from app.ai.gateway import AIGateway, build_provider
from app.core.config import settings
from app.core.constants import DEMO_USER_ID
from app.core.database import SessionLocal, engine
from app.models import Transaction
from app.schemas.transaction import TransactionCreate
from app.services import ai_chat_service, ai_transaction_service, transaction_service
from app.services.ai_chat_service import _thread_id
from app.services.draft_store import DraftStatus, get_draft_store

# Identificador único de la corrida: marca todo lo que se cree para poder limpiarlo.
RUN_ID = f"stage5-smoke-{uuid.uuid4()}"

DEFAULT_MAX_CALLS = 12
# Cero reintentos automáticos: con retries del SDK, un request contado podría convertirse en
# varias llamadas facturadas y el contador dejaría de ser fiel a lo que se gasta.
DEFAULT_MAX_RETRIES = 0
PASS, FAIL, SKIPPED = "PASS", "FAIL", "SKIPPED"

# Único escenario que puede quedar SKIPPED sin que la corrida se considere fallida, y solo
# por configuración (AI_EMBEDDING_PROVIDER != openai). Cualquier otro SKIPPED es un error.
RAG_SCENARIO = "4_rag_embeddings_reales"


# --------------------------------------------------------------------------------------
# Presupuesto y contabilidad de llamadas reales
# --------------------------------------------------------------------------------------


class BudgetExceededError(RuntimeError):
    """Se intentó una llamada real por encima de `REAL_AI_MAX_CALLS`."""


@dataclass
class CallRecord:
    kind: str
    duration_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    sent_call_ids: list[str] = field(default_factory=list)
    # Solo los IDs opacos del razonamiento: nunca su contenido, ni cifrado ni en claro.
    reasoning_ids: list[str] = field(default_factory=list)
    sent_reasoning_ids: list[str] = field(default_factory=list)


class CallBudget:
    """Contador estricto. Nunca se pasa del límite: prefiere abortar el escenario."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.records: list[CallRecord] = []

    @property
    def used(self) -> int:
        return len(self.records)

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def reserve(self, cost: int) -> bool:
        return self.remaining >= cost

    def spend(self, kind: str) -> None:
        if self.remaining <= 0:
            raise BudgetExceededError(
                f"Presupuesto agotado ({self.limit} llamadas). No se hizo la llamada."
            )
        # El registro se completa al volver; reservar acá evita pasarse ante un error.
        self.records.append(CallRecord(kind=kind, duration_ms=0))

    def last(self) -> CallRecord:
        return self.records[-1]


def _install_budget(budget: CallBudget) -> None:
    """Intercepta los tres puntos donde Plata habla con el proveedor real."""
    from app.ai.agent import brain as brain_module
    from app.ai.providers import openai as provider_module
    from app.ai.rag import embeddings as embeddings_module

    def _wrap_loader(loader):
        def loader_with_budget():
            client_cls, api_error, api_timeout = loader()

            class BudgetedResponses:
                def __init__(self, inner: Any) -> None:
                    self._inner = inner

                def parse(self, **kwargs: Any) -> Any:
                    budget.spend("responses.parse")
                    record = budget.last()
                    sent = [item for item in kwargs.get("input", []) if isinstance(item, dict)]
                    record.sent_call_ids = [
                        item["call_id"]
                        for item in sent
                        if item.get("type") == "function_call_output"
                    ]
                    record.sent_reasoning_ids = [
                        str(item.get("id")) for item in sent if item.get("type") == "reasoning"
                    ]
                    started = time.monotonic()
                    result = self._inner.parse(**kwargs)
                    record.duration_ms = int((time.monotonic() - started) * 1000)
                    usage = getattr(result, "usage", None)
                    record.input_tokens = getattr(usage, "input_tokens", None)
                    record.output_tokens = getattr(usage, "output_tokens", None)
                    record.tool_calls = _summarize_tool_calls(result)
                    record.reasoning_ids = _summarize_reasoning_ids(result)
                    return result

            class BudgetedClient:
                def __init__(self, **kwargs: Any) -> None:
                    inner = client_cls(**kwargs)
                    self.responses = BudgetedResponses(inner.responses)

            return BudgetedClient, api_error, api_timeout

        return loader_with_budget

    brain_module._load_openai_sdk = _wrap_loader(brain_module._load_openai_sdk)
    provider_module._load_sdk = _wrap_loader(provider_module._load_sdk)

    original_embed = embeddings_module.OpenAIEmbeddingProvider.embed

    def budgeted_embed(self: Any, text_value: str) -> list[float]:
        budget.spend("embeddings.create")
        record = budget.last()
        started = time.monotonic()
        vector = original_embed(self, text_value)
        record.duration_ms = int((time.monotonic() - started) * 1000)
        return vector

    embeddings_module.OpenAIEmbeddingProvider.embed = budgeted_embed


def _summarize_tool_calls(result: Any) -> list[dict[str, str]]:
    """Solo nombres y call_id: nunca argumentos ni texto de la respuesta."""
    summary: list[dict[str, str]] = []
    for item in getattr(result, "output", []) or []:
        kind = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if kind not in ("function_call", "tool_call"):
            continue
        name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else "")
        call_id = getattr(item, "call_id", None) or (
            item.get("call_id") if isinstance(item, dict) else ""
        )
        summary.append({"name": str(name), "call_id": str(call_id)})
    return summary


def _summarize_reasoning_ids(result: Any) -> list[str]:
    """Solo los IDs. El razonamiento nunca se registra ni se imprime."""
    ids: list[str] = []
    for item in getattr(result, "output", []) or []:
        kind = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if kind != "reasoning":
            continue
        item_id = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else None)
        if item_id:
            ids.append(str(item_id))
    return ids


# --------------------------------------------------------------------------------------
# Preflight: se corre entero ANTES de cualquier llamada
# --------------------------------------------------------------------------------------


@dataclass
class Preflight:
    errors: list[str] = field(default_factory=list)
    rag_skip_reason: str | None = None
    embedding_column_dimension: int | None = None
    alembic_revision: str | None = None


def _check_environment() -> Preflight:
    pre = Preflight()

    if os.getenv("RUN_REAL_AI_TESTS") != "1":
        pre.errors.append("RUN_REAL_AI_TESTS debe valer exactamente 1.")
    if settings.ai_provider != "openai":
        pre.errors.append("AI_PROVIDER debe ser 'openai'.")
    if not settings.ai_api_key:
        # Solo se comprueba la EXISTENCIA. El valor no se lee, ni se imprime, ni se loguea.
        pre.errors.append("AI_API_KEY no está configurada (definila en backend/.env).")
    if not settings.ai_model.strip():
        pre.errors.append("AI_MODEL está vacío.")
    elif settings.ai_model.startswith("mock-"):
        pre.errors.append("AI_MODEL apunta a un modelo mock; usá un modelo real.")
    if settings.ai_checkpoint_store != "postgres":
        pre.errors.append("AI_CHECKPOINT_STORE debe ser 'postgres' para validar persistencia.")

    if settings.ai_embedding_provider != "openai":
        pre.rag_skip_reason = "AI_EMBEDDING_PROVIDER no es 'openai'"
    elif not settings.ai_embedding_model.strip():
        pre.errors.append("AI_EMBEDDING_MODEL está vacío.")

    _check_database(pre)
    return pre


def _check_database(pre: Preflight) -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
            pre.alembic_revision = conn.execute(
                text("select version_num from alembic_version")
            ).scalar_one_or_none()
            declared = conn.execute(
                text(
                    "select format_type(a.atttypid, a.atttypmod) "
                    "from pg_attribute a "
                    "join pg_class c on c.oid = a.attrelid "
                    "where c.relname = 'transaction_search_documents' "
                    "and a.attname = 'embedding'"
                )
            ).scalar_one_or_none()
    except Exception as exc:
        pre.errors.append(f"PostgreSQL no está disponible ({type(exc).__name__}).")
        return

    if pre.alembic_revision is None:
        pre.errors.append("No hay migraciones aplicadas (falta alembic_version).")
    elif pre.alembic_revision != _alembic_head():
        pre.errors.append("La base no está en la última migración: corré 'alembic upgrade head'.")

    if declared and declared.startswith("vector("):
        pre.embedding_column_dimension = int(declared[len("vector(") : -1])
    if pre.embedding_column_dimension is None:
        pre.errors.append("No se pudo leer la dimensión de la columna vectorial.")
    elif pre.embedding_column_dimension != settings.ai_embedding_dimension:
        pre.errors.append(
            "AI_EMBEDDING_DIMENSION "
            f"({settings.ai_embedding_dimension}) no coincide con la columna vectorial "
            f"({pre.embedding_column_dimension}). Migrá antes de indexar."
        )


def _alembic_head() -> str | None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    with suppress(Exception):
        return ScriptDirectory.from_config(config).get_current_head()
    return None


# --------------------------------------------------------------------------------------
# Escenarios
# --------------------------------------------------------------------------------------


@dataclass
class Run:
    budget: CallBudget
    session: Any
    as_of: date
    results: dict[str, str] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    created_transaction_ids: list[uuid.UUID] = field(default_factory=list)
    created_draft_ids: list[uuid.UUID] = field(default_factory=list)
    conversation_ids: list[uuid.UUID] = field(default_factory=list)
    # True solo si el RAG se salteó por AI_EMBEDDING_PROVIDER, nunca por presupuesto.
    rag_skipped_by_config: bool = False

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.results[name] = status
        if detail:
            self.details[name] = detail

    def gateway(self) -> AIGateway:
        return AIGateway(build_provider(settings))


def _scenario(run: Run, name: str, cost: int, fn) -> None:
    if not run.budget.reserve(cost):
        run.record(name, SKIPPED, f"presupuesto insuficiente (quedan {run.budget.remaining})")
        print(f"[{SKIPPED}] {name}: presupuesto insuficiente")
        return
    before = run.budget.used
    try:
        detail = fn(run)
        run.record(name, PASS, detail or "")
        print(f"[{PASS}] {name} · llamadas={run.budget.used - before}")
    except BudgetExceededError as exc:
        run.record(name, SKIPPED, str(exc))
        print(f"[{SKIPPED}] {name}: {exc}")
    except AssertionError as exc:
        run.record(name, FAIL, str(exc))
        print(f"[{FAIL}] {name}: {exc}")
    except Exception as exc:
        run.record(name, FAIL, f"{type(exc).__name__}: {exc}")
        print(f"[{FAIL}] {name}: {type(exc).__name__}: {exc}")


def scenario_parser(run: Run) -> str:
    """1. Structured output real del parser, sin persistir nada."""
    store = get_draft_store()
    before = _transaction_count(run.session)

    parsed = ai_transaction_service.parse_transaction(
        run.gateway(),
        store,
        "Gasté 12500 pesos ayer en transporte con débito",
        as_of=run.as_of,
        user_id=DEMO_USER_ID,
    )
    run.created_draft_ids.append(parsed.draft_id)
    tx = parsed.transaction

    assert tx is not None, "el parser no devolvió un movimiento"
    assert tx.type is not None and tx.type.value == "expense", "el tipo no es expense"
    assert isinstance(tx.amount, Decimal), "el monto no es Decimal"
    assert tx.amount == Decimal("12500.00"), f"monto inesperado: {tx.amount}"
    assert tx.occurred_on == run.as_of - timedelta(days=1), f"fecha inesperada: {tx.occurred_on}"
    assert tx.category, "falta la categoría"
    assert tx.payment_method, "falta el medio de pago"
    assert store.get(parsed.draft_id, user_id=DEMO_USER_ID).status is DraftStatus.PENDING, (
        "el draft no quedó pendiente"
    )
    assert _transaction_count(run.session) == before, "el parser persistió un movimiento"

    return f"amount={tx.amount} type=expense date_ok=True persisted=False"


def scenario_read_and_multistep(run: Run) -> str:
    """2 y 3. Tool de lectura real y loop multi-ronda con function_call_output.

    Secuencia exigida, sin ambigüedad ni cuentas del modelo: `get_financial_summary` en la
    primera ronda, `list_pending_commitments` recién después de recibir el
    `function_call_output` de la primera, y respuesta en la última. Que el modelo pida las
    dos tools juntas en la primera ronda es FAIL: eso no prueba un loop multi-step.
    """
    first_call = run.budget.used
    response = ai_chat_service.chat(
        run.session,
        (
            "Hacelo en dos pasos, uno por vez. Paso 1: usá get_financial_summary y esperá "
            "el resultado. Paso 2: recién con ese resultado en mano, usá "
            "list_pending_commitments. No pidas las dos herramientas juntas. "
            "Al final contame cómo queda mi mes."
        ),
        user_id=DEMO_USER_ID,
        as_of=run.as_of,
    )
    run.conversation_ids.append(response.conversation_id)
    rounds = [r for r in run.budget.records[first_call:] if r.kind == "responses.parse"]

    assert len(rounds) >= 3, f"se esperaban 3 rondas (tool, tool, respuesta), hubo {len(rounds)}"
    assert len(rounds) <= settings.ai_agent_max_iterations + 1, "se superó el máximo de rondas"

    first_round = [call["name"] for call in rounds[0].tool_calls]
    second_round = [call["name"] for call in rounds[1].tool_calls]

    assert first_round == ["get_financial_summary"], (
        f"la primera ronda debía pedir solo get_financial_summary, pidió {first_round}"
    )
    assert "list_pending_commitments" not in first_round, (
        "las dos tools llegaron en la misma ronda: no hay loop multi-step"
    )
    assert "list_pending_commitments" in second_round, (
        f"la segunda ronda no pidió list_pending_commitments, pidió {second_round}"
    )
    assert not rounds[-1].tool_calls, "la última ronda pidió tools en vez de responder"

    every_call = [call for r in rounds for call in r.tool_calls]
    for call in every_call:
        assert call["name"] in TOOLS, f"tool fuera del allowlist: {call['name']}"
        assert not is_write_tool(call["name"]), f"se ejecutó una escritura: {call['name']}"

    # El call_id de la primera ronda tiene que volver como function_call_output en la segunda.
    returned = {call["call_id"] for call in rounds[0].tool_calls}
    resent = set(rounds[1].sent_call_ids)
    assert returned and returned <= resent, "no se preservaron los call_id entre rondas"
    # Si el modelo razona, ese razonamiento tiene que volver junto con el resto del contexto.
    if rounds[0].reasoning_ids:
        assert set(rounds[0].reasoning_ids) <= set(rounds[1].sent_reasoning_ids), (
            "no se reenvió el razonamiento a la ronda siguiente"
        )

    assert response.requires_approval is False, "una lectura pidió aprobación"
    assert response.pending_action is None, "una lectura dejó una acción pendiente"
    assert all(tool.ok for tool in response.tools_used), "una tool falló"
    assert _verifier_ok(response.conversation_id), "la respuesta final no pasó el verificador"
    assert settings.ai_api_key not in response.answer, "la respuesta filtró configuración interna"
    # Con un modelo real, la calidad de la respuesta no la garantiza el prompt: la capa de
    # presentación la reemplaza si expone campos internos o se va de largo.
    assert internal_leaks(response.answer) == [], (
        f"la respuesta expuso datos internos: {internal_leaks(response.answer)}"
    )

    names = [call["name"] for call in every_call]
    return f"rondas={len(rounds)} tools={names} call_id_preservado=True"


def scenario_rag(run: Run) -> str:
    """4. RAG con embeddings reales, aislando ingresos de gastos."""
    from app.ai.rag.retriever import HybridRetriever, SearchFilters, select_relevant, selected_total

    relevant = _create_temp_transaction(
        run,
        type_="expense",
        amount=Decimal("48000.00"),
        category="mascotas",
        description=f"consulta veterinaria del perro {RUN_ID}",
    )
    _create_temp_transaction(
        run,
        type_="expense",
        amount=Decimal("31000.00"),
        category="servicios",
        description=f"abono mensual de internet {RUN_ID}",
    )

    document = run.session.execute(
        text(
            "select vector_dims(embedding), embedding_model "
            "from transaction_search_documents where transaction_id = :tid"
        ),
        {"tid": str(relevant.id)},
    ).one_or_none()
    assert document is not None, "no se indexó el movimiento"
    assert document[0] == settings.ai_embedding_dimension, f"dimensión inesperada: {document[0]}"
    assert document[1] == settings.ai_embedding_model, "el documento no usó el modelo real"

    retriever = HybridRetriever(run.session)
    candidates = retriever.search(
        user_id=DEMO_USER_ID,
        query="gastos del veterinario del perro",
        top_k=5,
        filters=SearchFilters(tx_type="expense"),
    )
    selected = select_relevant(
        candidates,
        vector_max_distance=settings.ai_rag_vector_max_distance,
        limit=settings.ai_rag_max_evidence,
    )
    selected_ids = [c.transaction_id for c in selected]

    assert relevant.id in selected_ids, "la evidencia relevante no fue recuperada"
    assert all(c.tx_type == "expense" for c in selected), "se coló un movimiento de otro tipo"
    rejected = [c for c in candidates if c.transaction_id not in selected_ids]
    assert rejected or len(candidates) == len(selected_ids), "no se evaluó el descarte"

    totals = selected_total(run.session, DEMO_USER_ID, selected_ids, tx_type="expense")
    manual = sum(
        (
            run.session.get(Transaction, tid).amount
            for tid in selected_ids
            if run.session.get(Transaction, tid).type.value == "expense"
        ),
        Decimal("0"),
    )
    assert totals["total"] == manual, "la suma SQL no coincide con los IDs aceptados"
    as_income = selected_total(run.session, DEMO_USER_ID, selected_ids, tx_type="income")
    assert as_income["count"] == 0, "los ingresos se mezclaron con los gastos"

    return (
        f"dim={document[0]} recuperados={len(candidates)} aceptados={len(selected_ids)} "
        f"descartados={len(rejected)} total_ok=True"
    )


def scenario_write_checkpoint_approve(run: Run) -> str:
    """5 y 7. Draft antes de escribir, checkpoint que sobrevive, approve idempotente."""
    balance_before = _balance(run.session)
    count_before = _transaction_count(run.session)

    response = ai_chat_service.chat(
        run.session,
        f"Registrá que gasté 7400 pesos hoy en librería con débito, referencia {RUN_ID}",
        user_id=DEMO_USER_ID,
        as_of=run.as_of,
    )

    run.conversation_ids.append(response.conversation_id)
    assert response.requires_approval is True, "la escritura no pidió aprobación"
    assert response.pending_action is not None, "no se preparó una acción pendiente"
    assert response.pending_action.kind == "create_transaction", "kind inesperado"
    draft_id = uuid.UUID(str(_pending_draft_id(response.conversation_id)))
    run.created_draft_ids.append(draft_id)
    assert _balance(run.session) == balance_before, "el saldo cambió antes de aprobar"
    assert _transaction_count(run.session) == count_before, "se persistió antes de aprobar"

    # Checkpoint: se recrea grafo y checkpointer, y la pausa tiene que seguir ahí.
    conversation_id = response.conversation_id
    action_id = response.pending_action.action_id
    close_checkpointer()
    snapshot = get_compiled_graph().get_state({"configurable": {"thread_id": str(conversation_id)}})
    assert snapshot.values.get("pending_action"), "la acción pendiente no sobrevivió"
    assert "apply_write" in getattr(snapshot, "next", ()), "la pausa no sobrevivió"
    recovered = str(snapshot.values["pending_action"]["action_id"])
    assert recovered == str(action_id), "el action_id no sobrevivió al checkpoint"

    ids_before = _transaction_ids(run.session)
    approved = ai_chat_service.resume(
        run.session,
        conversation_id,
        action_id,
        user_id=DEMO_USER_ID,
        approve=True,
        as_of=run.as_of,
    )
    created = _transaction_ids(run.session) - ids_before
    run.created_transaction_ids.extend(created)

    assert approved.requires_approval is False, "sigue pidiendo aprobación tras aprobar"
    assert len(created) == 1, f"se crearon {len(created)} movimientos en vez de 1"
    assert _transaction_count(run.session) == count_before + 1, "cantidad de movimientos inesperada"
    assert _balance(run.session) != balance_before, "el saldo no se movió al aprobar"
    balance_after = _balance(run.session)
    assert get_draft_store().get(draft_id, user_id=DEMO_USER_ID).status is DraftStatus.CONFIRMED, (
        "el draft no quedó ok"
    )

    second = _expect_error(
        lambda: ai_chat_service.resume(
            run.session,
            conversation_id,
            action_id,
            user_id=DEMO_USER_ID,
            approve=True,
            as_of=run.as_of,
        )
    )
    assert second is not None, "el segundo approve no fue rechazado"
    assert _balance(run.session) == balance_after, "el segundo approve movió el saldo"
    assert _transaction_count(run.session) == count_before + 1, "el segundo approve duplicó filas"

    return (
        f"draft_antes_de_persistir=True checkpoint_sobrevive=True filas_creadas=1 "
        f"segundo_approve={type(second).__name__}"
    )


def scenario_reject(run: Run) -> str:
    """6. Rechazo: no se crea nada y la conversación puede seguir."""
    balance_before = _balance(run.session)
    count_before = _transaction_count(run.session)

    response = ai_chat_service.chat(
        run.session,
        f"Anotá un gasto de 5300 pesos hoy en kiosco con efectivo, referencia {RUN_ID}",
        user_id=DEMO_USER_ID,
        as_of=run.as_of,
    )
    run.conversation_ids.append(response.conversation_id)
    assert response.pending_action is not None, "no se preparó una acción pendiente"
    draft_id = uuid.UUID(str(_pending_draft_id(response.conversation_id)))
    run.created_draft_ids.append(draft_id)

    rejected = ai_chat_service.resume(
        run.session,
        response.conversation_id,
        response.pending_action.action_id,
        user_id=DEMO_USER_ID,
        approve=False,
        as_of=run.as_of,
    )

    assert rejected.requires_approval is False, "sigue pendiente tras rechazar"
    assert _transaction_count(run.session) == count_before, "el rechazo creó un movimiento"
    assert _balance(run.session) == balance_before, "el rechazo movió el saldo"
    final_status = get_draft_store().get(draft_id).status
    assert final_status is DraftStatus.REJECTED, "el draft no quedó rejected"

    snapshot = get_compiled_graph().get_state(
        {"configurable": {"thread_id": str(response.conversation_id)}}
    )
    assert not snapshot.values.get("pending_action"), "quedó una acción pendiente tras rechazar"

    return "movimientos=0 saldo_intacto=True draft=rejected conversacion_continuable=True"


# --------------------------------------------------------------------------------------
# Helpers de estado (nunca imprimen datos financieros)
# --------------------------------------------------------------------------------------


def _transaction_count(session: Any) -> int:
    return session.execute(
        text("select count(*) from transactions where user_id = :uid"), {"uid": str(DEMO_USER_ID)}
    ).scalar_one()


def _transaction_ids(session: Any) -> set[uuid.UUID]:
    return set(
        session.execute(select(Transaction.id).where(Transaction.user_id == DEMO_USER_ID))
        .scalars()
        .all()
    )


def _balance(session: Any) -> Decimal:
    from app.services.profile_service import get_profile

    session.expire_all()
    return get_profile(session, DEMO_USER_ID).current_balance


def _verifier_ok(conversation_id: uuid.UUID) -> bool:
    snapshot = get_compiled_graph().get_state({"configurable": {"thread_id": str(conversation_id)}})
    return bool(snapshot.values.get("verifier_ok", False))


def _pending_draft_id(conversation_id: uuid.UUID) -> Any:
    # El hilo del checkpointer incluye al dueño (ver ai_chat_service._thread_id).
    config = {"configurable": {"thread_id": _thread_id(DEMO_USER_ID, conversation_id)}}
    snapshot = get_compiled_graph().get_state(config)
    return snapshot.values["pending_action"]["draft_id"]


def _expect_error(fn) -> Exception | None:
    try:
        fn()
    except Exception as exc:
        return exc
    return None


def _create_temp_transaction(
    run: Run, *, type_: str, amount: Decimal, category: str, description: str
) -> Transaction:
    tx = transaction_service.create_transaction(
        run.session,
        DEMO_USER_ID,
        TransactionCreate(
            type=type_,
            amount=amount,
            category=category,
            description=description,
            occurred_on=run.as_of,
            payment_method="debito",
        ),
    )
    run.created_transaction_ids.append(tx.id)
    return tx


def _cleanup(run: Run) -> str:
    """Borra TODO lo creado en esta corrida, y solo eso.

    Cuatro rastros distintos, cada uno acotado a IDs que registró la corrida (nunca a un
    `like` amplio ni a un borrado por fecha, que pisaría datos previos del usuario):

    1. Movimientos: por `transaction_service`, para que el saldo se revierta solo.
    2. Documentos de búsqueda: caen por `ON DELETE CASCADE`; se fuerza igual el borrado por
       si un movimiento no se pudo eliminar.
    3. Drafts: se eliminan en **cualquier** estado (pending, confirmed o rejected), porque
       todos fueron creados por la corrida.
    4. Checkpoints de LangGraph: las conversaciones del RUN_ID, en las tres tablas.
    """
    removed, failed = 0, 0
    for transaction_id in run.created_transaction_ids:
        try:
            transaction_service.delete_transaction(run.session, DEMO_USER_ID, transaction_id)
            removed += 1
        except Exception:
            failed += 1

    docs = _delete_search_documents(run)
    drafts = _delete_drafts(run)
    checkpoints = _delete_checkpoints(run)

    leftovers = run.session.execute(
        text("select count(*) from transactions where description like :pattern"),
        {"pattern": f"%{RUN_ID}%"},
    ).scalar_one()
    orphan_docs = run.session.execute(
        text("select count(*) from transaction_search_documents where searchable_text like :p"),
        {"p": f"%{RUN_ID}%"},
    ).scalar_one()

    return (
        f"movimientos_borrados={removed} fallidos={failed} documentos_borrados={docs} "
        f"drafts_borrados={drafts} checkpoints_borrados={checkpoints} "
        f"restos={leftovers} documentos_huerfanos={orphan_docs}"
    )


def _delete_search_documents(run: Run) -> int:
    """Los documentos de los movimientos de la corrida, por transaction_id exacto."""
    if not run.created_transaction_ids:
        return 0
    try:
        deleted = run.session.execute(
            text(
                "delete from transaction_search_documents "
                "where transaction_id = any(cast(:ids as uuid[]))"
            ),
            {"ids": [str(tid) for tid in run.created_transaction_ids]},
        ).rowcount
        run.session.commit()
        return int(deleted or 0)
    except Exception:
        run.session.rollback()
        return 0


def _delete_drafts(run: Run) -> int:
    """Drafts de la corrida en cualquier estado. Solo por ID: nunca por filtro amplio."""
    if not run.created_draft_ids:
        return 0
    try:
        deleted = run.session.execute(
            text("delete from ai_drafts where id = any(cast(:ids as uuid[]))"),
            {"ids": [str(draft_id) for draft_id in run.created_draft_ids]},
        ).rowcount
        run.session.commit()
        return int(deleted or 0)
    except Exception:
        run.session.rollback()
        return 0


def _delete_checkpoints(run: Run) -> int:
    """Conversaciones de la corrida en las tablas del checkpointer de LangGraph."""
    if not run.conversation_ids:
        return 0
    threads = [str(cid) for cid in run.conversation_ids]
    deleted = 0
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        try:
            deleted += int(
                run.session.execute(
                    text(f"delete from {table} where thread_id = any(:threads)"),
                    {"threads": threads},
                ).rowcount
                or 0
            )
            run.session.commit()
        except Exception:
            run.session.rollback()
    return deleted


# --------------------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    """Entero de configuración: entorno del proceso, si no `backend/.env`, si no el default.

    `pydantic-settings` lee `.env` hacia `Settings`, pero **no** lo exporta a `os.environ`:
    sin este respaldo, poner `REAL_AI_MAX_CALLS` en `backend/.env` no tendría ningún efecto
    y el archivo estaría mintiendo. El entorno del proceso siempre gana, así que un override
    desde PowerShell pisa lo del archivo.

    Ojo: esto vale solo para los enteros de presupuesto. `RUN_REAL_AI_TESTS` se lee siempre
    con `os.getenv` a propósito, para que la autorización de gastar plata tenga que darse
    explícitamente en la terminal y nunca pueda quedar activada dentro de un archivo.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raw = _dotenv_values().get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _dotenv_values() -> dict[str, str]:
    """Pares nombre=valor de `backend/.env`. No imprime ni loguea nada de lo que lee."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / ".env"
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def main() -> int:
    pre = _check_environment()
    if pre.errors:
        print(f"\nNo se ejecutó ninguna llamada real. Falta configuración ({len(pre.errors)}):")
        for problem in pre.errors:
            print(f"  - {problem}")
        print("\nCorregí eso y volvé a correr:")
        print("  RUN_REAL_AI_TESTS=1 python -m app.scripts.real_ai_smoke")
        return 2

    max_calls = _env_int("REAL_AI_MAX_CALLS", DEFAULT_MAX_CALLS)
    timeout_seconds = _env_int("REAL_AI_TIMEOUT_SECONDS", settings.ai_timeout_seconds)
    max_retries = _env_int("REAL_AI_MAX_RETRIES", DEFAULT_MAX_RETRIES)
    settings.ai_timeout_seconds = timeout_seconds
    settings.ai_max_retries = max_retries

    budget = CallBudget(max_calls)
    _install_budget(budget)

    print(f"run_id={RUN_ID}")
    print(f"provider={settings.ai_provider} model={settings.ai_model}")
    print(
        f"embedding_provider={settings.ai_embedding_provider} "
        f"embedding_model={settings.ai_embedding_model} dim={settings.ai_embedding_dimension}"
    )
    print(f"alembic={pre.alembic_revision} max_calls={max_calls} timeout={timeout_seconds}s")
    print(f"max_retries={max_retries}  # el tope cuenta requests reales, sin reintentos")
    print("api_key_present=True  # el valor nunca se lee ni se imprime\n")

    started = time.monotonic()
    with SessionLocal() as session:
        run = Run(budget=budget, session=session, as_of=date.today())
        try:
            _scenario(run, "1_parser_structured_output", 1, scenario_parser)
            _scenario(run, "2y3_tool_lectura_y_multistep", 3, scenario_read_and_multistep)
            if pre.rag_skip_reason:
                run.rag_skipped_by_config = True
                run.record(RAG_SCENARIO, SKIPPED, pre.rag_skip_reason)
                print(f"[{SKIPPED}] {RAG_SCENARIO}: {pre.rag_skip_reason}")
            else:
                _scenario(run, RAG_SCENARIO, 3, scenario_rag)
            _scenario(run, "5y7_escritura_checkpoint_approve", 3, scenario_write_checkpoint_approve)
            _scenario(run, "6_rechazo", 2, scenario_reject)
        finally:
            cleanup_detail = _cleanup(run)
            print(f"\ncleanup: {cleanup_detail}")
    elapsed = int(time.monotonic() - started)

    with suppress(Exception):
        close_checkpointer()

    print("\n=== Resumen ===")
    for name, status in run.results.items():
        detail = run.details.get(name, "")
        print(f"  {status:8} {name}{(' · ' + detail) if detail else ''}")

    tokens_in = sum(r.input_tokens or 0 for r in budget.records)
    tokens_out = sum(r.output_tokens or 0 for r in budget.records)
    print(f"\nllamadas_reales={budget.used}/{budget.limit} duracion={elapsed}s")
    print(f"tokens_in={tokens_in or 'n/d'} tokens_out={tokens_out or 'n/d'}")
    print(f"cleanup: {cleanup_detail}")

    return _exit_code(run, budget)


def _exit_code(run: Run, budget: CallBudget) -> int:
    """0 solo si todo lo obligatorio pasó.

    Un escenario salteado por falta de presupuesto no es un éxito: significa que la corrida
    no validó lo que debía. El único SKIPPED aceptable es el del RAG cuando el proveedor de
    embeddings no es real (ahí no hay nada que validar de verdad).
    """
    failures = [name for name, status in run.results.items() if status == FAIL]
    unjustified_skips = [
        name for name, status in run.results.items() if status == SKIPPED and name != RAG_SCENARIO
    ]
    if run.results.get(RAG_SCENARIO) == SKIPPED and not run.rag_skipped_by_config:
        unjustified_skips.append(RAG_SCENARIO)

    if failures:
        print(f"\nFAIL: {failures}")
    if unjustified_skips:
        print(
            f"\nFAIL: escenarios obligatorios sin ejecutar {unjustified_skips}. "
            f"Subí REAL_AI_MAX_CALLS (usadas {budget.used}/{budget.limit}) y repetí."
        )
    return 1 if (failures or unjustified_skips) else 0


if __name__ == "__main__":
    raise SystemExit(main())
