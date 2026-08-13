"""Nodos del grafo del copiloto. Cada nodo recibe (state, config) y devuelve estado parcial.

Las dependencias no serializables (Session, draft store, gateway, cerebro) viajan por
`config["configurable"]`, NUNCA por el estado (que se persiste en el checkpoint). Así el
estado guardado no contiene secretos ni objetos vivos.

El hilo conductor es la RUTA del turno (`AgentRoute`). Se decide al clasificar, se afina al
planificar y a partir de ahí manda: qué tools se ejecutan, cómo se arma la respuesta y qué
se le exige al verificador. Un turno conversacional no se valida con las reglas de una
respuesta determinística, y una falta de datos no es un error sino una repregunta.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.ai.agent import presentation, router, verifier
from app.ai.agent.brain import AgentBrain
from app.ai.agent.schemas import (
    FREE_TEXT_ROUTES,
    AgentIntent,
    AgentRoute,
    StructuredAnswer,
)
from app.ai.agent.tools import (
    INVENTED_AMOUNT_ERROR,
    ToolContext,
    amount_is_from_user,
    blocked_invented_amount_result,
    blocked_sensitive_tool_result,
    is_write_tool,
    run_tool,
)

# Intenciones cuya respuesta sale de lo que ya se resolvió antes, sin ejecutar tools.
_FROM_MEMORY = (AgentIntent.EXPLAIN_LAST_ANSWER,)


def _ctx(config: dict[str, Any]) -> ToolContext:
    return config["configurable"]["ctx"]


def _brain(config: dict[str, Any]) -> AgentBrain:
    return config["configurable"]["brain"]


def _memory(state: dict[str, Any]) -> dict[str, Any]:
    """Lo que el cerebro necesita saber de la conversación y no está en los mensajes."""
    return {
        "pending_request": state.get("pending_request"),
        "last_query": state.get("last_query"),
        "has_previous_answer": bool(state.get("last_structured_answer")),
    }


def classify_intent(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    brain = _brain(config)
    history = state.get("messages", [])
    memory = _memory(state)

    runner = getattr(brain, "run_agentic", None)
    if callable(runner):
        result = runner(state["input"], history, _ctx(config), memory)
        result["steps"] = state.get("steps", 0) + 1
        result["route"] = _agentic_route(result)
        return result

    result = brain.classify(state["input"], history, memory)
    intent = AgentIntent(result["intent"])
    return {
        "intent": intent.value,
        "intent_confidence": float(result["confidence"]),
        "planner_args": result.get("args", {}),
        "route": router.route_for(intent).value,
        "missing_fields": list(result.get("missing_fields") or []),
        "steps": state.get("steps", 0) + 1,
        "messages": [{"role": "user", "content": state["input"]}],
    }


def _agentic_route(result: dict[str, Any]) -> str:
    """Ruta de un turno que resolvió el loop agéntico del cerebro real.

    El modelo declara qué clase de texto escribió (`answer_kind`); acá se cruza con lo que
    efectivamente hizo. Si trajo datos y encima razonó sobre ellos, es un turno mixto: ese
    es el `LLM → tool → LLM` que antes no existía porque la plantilla siempre ganaba.
    """
    kind = result.get("answer_kind")
    used_tools = bool(result.get("tool_calls"))
    if kind == "clarification" or (result.get("missing_fields") and not used_tools):
        return AgentRoute.CLARIFICATION.value
    if not used_tools:
        return AgentRoute.CONVERSATIONAL.value
    if kind == "analysis":
        return AgentRoute.MIXED.value
    return router.route_for(AgentIntent(result.get("intent", AgentIntent.UNKNOWN))).value


def plan_tools(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if state.get("agentic_done"):
        return {"tool_calls": state.get("tool_calls", [])}

    intent = AgentIntent(state["intent"])
    if intent in _FROM_MEMORY:
        return {"tool_calls": [], "missing_fields": []}

    ctx = _ctx(config)
    result = router.plan(
        intent,
        state["input"],
        state.get("planner_args", {}),
        ctx.as_of,
        state.get("last_simulation"),
        state.get("pending_request"),
    )
    partial: dict[str, Any] = {
        "tool_calls": result.tool_calls,
        "missing_fields": result.missing_fields,
        "pending_request": result.slots,
    }
    if result.needs_clarification:
        # Falta un dato: el turno es una pregunta, no una consulta fallida.
        partial["route"] = AgentRoute.CLARIFICATION.value
    return partial


def execute_tools(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if state.get("agentic_done"):
        results = state.get("tool_results", [])
        return {
            "tool_results": results,
            "retrieved_evidence": state.get("retrieved_evidence", []),
            "pending_action": state.get("pending_action"),
            "approval_required": state.get("approval_required", False),
            **_ask_for_amount_if_blocked(results),
        }
    ctx = _ctx(config)
    results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    approval = False
    last_sim = state.get("last_simulation")
    sensitive_write_executed = False

    said = _user_texts(state)
    for call in state.get("tool_calls", []):
        writes = is_write_tool(call["name"])
        if sensitive_write_executed and writes:
            rec = blocked_sensitive_tool_result(call["name"], call["arguments"])
        elif not amount_is_from_user(call["name"], call["arguments"], said, results):
            # Un precio que nadie dijo no se calcula, ni siquiera si el plan lo trae listo.
            rec = blocked_invented_amount_result(call["name"], call["arguments"])
        else:
            if writes:
                sensitive_write_executed = True
            rec = run_tool(ctx, call["name"], call["arguments"])
        results.append(rec)
        if rec["name"] == "search_transactions" and rec["ok"]:
            evidence = rec["data"]["evidence"]
        if rec["name"] == "simulate_purchase_preview" and rec["ok"]:
            last_sim = {
                "amount": call["arguments"]["total_amount"],
                "installments": call["arguments"]["installments"],
            }
        if rec["name"] == "check_one_time_purchase" and rec["ok"]:
            # La compra de la que se venía hablando, aunque haya sido al contado: si después
            # preguntan "¿y si la pago en 9 cuotas?", el precio no se vuelve a pedir.
            last_sim = {**(last_sim or {}), "amount": call["arguments"]["amount"]}
        if (
            pending is None
            and rec.get("writes")
            and rec["ok"]
            and rec["data"]
            and rec["data"].get("is_confirmable")
        ):
            data = rec["data"]
            pending = {
                "action_id": str(uuid4()),
                "kind": data["kind"],
                "summary": data["summary"],
                "draft_id": data["draft_id"],
                "draft": data.get("fields") or {},
            }
            approval = True

    partial: dict[str, Any] = {
        "tool_results": results,
        "retrieved_evidence": evidence,
        "pending_action": pending,
        "approval_required": approval,
    }
    if last_sim:
        partial["last_simulation"] = last_sim
    # Qué se consultó, para poder entender "¿y el mes pasado?" en el turno siguiente. Solo
    # las lecturas: una escritura no es una consulta que se pueda repetir con otro filtro.
    if router.needs_user_data(AgentIntent(state["intent"])) and any(r["ok"] for r in results):
        partial["last_query"] = {
            "intent": state["intent"],
            "args": dict(state.get("planner_args", {})),
        }
    partial.update(_ask_for_amount_if_blocked(results))
    return partial


def _user_texts(state: dict[str, Any]) -> list[str]:
    """Lo que la persona escribió en este turno y en los anteriores del hilo."""
    history = [
        str(item.get("content", ""))
        for item in state.get("messages", [])[-12:]
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    return [state.get("input", ""), *history]


def _ask_for_amount_if_blocked(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Si se bloqueó un cálculo por un monto inventado, el turno pasa a pedirlo.

    Que la tool no se ejecute es la mitad del arreglo; la otra mitad es que la persona
    reciba la pregunta ("¿cuánto sale?") en vez de un mensaje de que algo salió mal.
    """
    blocked = any(r.get("error") == INVENTED_AMOUNT_ERROR for r in results)
    if not blocked or any(r.get("ok") for r in results):
        return {}
    return {"route": AgentRoute.CLARIFICATION.value, "missing_fields": ["amount"]}


def generate_answer(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Arma la respuesta según la ruta del turno.

    Tres caminos, y ninguno termina en un mensaje de error por no encajar:

    - **Aclaración**: falta un dato. Se pregunta, en criollo y en una línea.
    - **Charla**: el texto lo escribe el modelo. No hay plantilla que reemplazarlo porque no
      hay datos que presentar; lo único que no puede hacer es afirmar cifras.
    - **Datos**: manda la plantilla determinística. El texto del modelo solo gana cuando
      aporta análisis Y todas sus cifras están respaldadas; si falla, se cae a la plantilla,
      que siempre tiene los números correctos.
    """
    intent = AgentIntent(state["intent"])
    route = AgentRoute(state.get("route") or AgentRoute.DETERMINISTIC.value)

    if route is AgentRoute.CLARIFICATION:
        structured = presentation.build_clarification(
            intent,
            state.get("missing_fields", []),
            (state.get("pending_request") or {}).get("fields"),
        )
        # Si el modelo ya escribió la repregunta en su propio loop, esa es la que suena
        # natural; si no, la arma la presentación. En ningún caso se llama al modelo de
        # nuevo solo para preguntar un dato.
        written = state.get("final_answer", "") if state.get("agentic_done") else ""
        answer = (
            written
            if written and _acceptable(state, written, route)
            else presentation.render(structured)
        )
        return _answered(answer, structured)

    if route is AgentRoute.UNSUPPORTED:
        # Fuera de alcance: se dice y listo. No es un fallo del copiloto ni algo que el
        # formulario manual resuelva, así que no se ofrece como si lo fuera.
        text = presentation.UNSUPPORTED_MESSAGE
        return _answered(text, StructuredAnswer(verdict="info", headline=text))

    if route is AgentRoute.CONVERSATIONAL:
        free_text = _free_text(state, config, route)
        if free_text and _acceptable(state, free_text, route):
            return _answered(free_text, StructuredAnswer(verdict="info", headline=free_text))
        recovery = presentation.CONVERSATION_RECOVERY
        return _answered(recovery, StructuredAnswer(verdict="info", headline=recovery))

    structured = presentation.build_answer(intent, _answer_context(state))

    # El texto libre solo desplaza a la plantilla si el modelo dice que razonó sobre los
    # datos (turno mixto) y el resultado está limpio y respaldado. Es el `LLM → tool → LLM`.
    if route is AgentRoute.MIXED:
        written = state.get("final_answer", "")
        if written and _acceptable(state, written, route):
            return _answered(written, structured.model_copy(update={"headline": written}))

    if structured.verdict != "unavailable":
        return _answered(presentation.render(structured), structured)

    # Sin plantilla que aplicar, recién acá se le pide texto al modelo.
    free_text = _free_text(state, config, route)
    if free_text and _acceptable(state, free_text, route):
        return _answered(free_text, StructuredAnswer(verdict="info", headline=free_text))

    return _answered(structured.headline, structured)


def _answer_context(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_results": state.get("tool_results", []),
        "evidence": state.get("retrieved_evidence", []),
        "pending_action": state.get("pending_action"),
        "planner_args": state.get("planner_args", {}),
        "pending_request": state.get("pending_request"),
        "missing_fields": state.get("missing_fields", []),
        "last_structured_answer": state.get("last_structured_answer"),
    }


def _free_text(state: dict[str, Any], config: dict[str, Any], route: AgentRoute) -> str:
    """El texto que escribió el modelo para este turno, si hay alguno.

    En el loop agéntico ya viene resuelto. Si no, se le pide al cerebro: conversando cuando
    la ruta es de charla o repregunta, y redactando sobre los datos cuando no.
    """
    if state.get("agentic_done"):
        return state.get("final_answer", "")

    brain = _brain(config)
    if route in FREE_TEXT_ROUTES:
        converse = getattr(brain, "converse", None)
        if not callable(converse):
            return ""
        return converse(state["input"], state.get("messages", []), _memory(state))

    return brain.answer(AgentIntent(state["intent"]), _answer_context(state))


def _acceptable(state: dict[str, Any], text: str, route: AgentRoute) -> bool:
    """Si ese texto se le puede mostrar a la persona: sin fugas y sin cifras inventadas."""
    ok, _ = verifier.verify(
        answer=text,
        tool_results=state.get("tool_results", []),
        evidence=state.get("retrieved_evidence", []),
        pending_action=state.get("pending_action"),
        approval_required=state.get("approval_required", False),
        route=route,
        previously_verified=state.get("last_answer_amounts", []),
    )
    return ok


def _answered(answer: str, structured: StructuredAnswer) -> dict[str, Any]:
    """La respuesta candidata del turno.

    El mensaje del asistente NO se agrega acá sino después de verificar: al historial (que
    se persiste y se devuelve por `GET /ai/conversations`) tiene que entrar exactamente lo
    que la persona vio, no un texto que el verificador terminó descartando.
    """
    return {
        "final_answer": answer,
        "structured_answer": structured.model_dump(mode="json"),
    }


def verify_results(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Última barrera antes de la pantalla. Si algo no cierra, se recupera por ruta.

    Que una respuesta no verifique ya no es el final de la conversación: en un turno de
    datos se cae a la plantilla determinística (que tiene los números correctos) y en uno
    conversacional se ofrece mirar los datos reales. El mensaje de error queda para lo que
    de verdad es un error.
    """
    route = AgentRoute(state.get("route") or AgentRoute.DETERMINISTIC.value)
    answer = state.get("final_answer", "")
    ok, reasons = verifier.verify(
        answer=answer,
        tool_results=state.get("tool_results", []),
        evidence=state.get("retrieved_evidence", []),
        pending_action=state.get("pending_action"),
        approval_required=state.get("approval_required", False),
        route=route,
        previously_verified=state.get("last_answer_amounts", []),
    )
    if ok:
        return {
            "verifier_ok": True,
            # Lo que se mostró y se verificó queda disponible para los turnos siguientes.
            "last_answer_amounts": verifier.amounts_in(answer),
            "last_structured_answer": state.get("structured_answer"),
            "messages": [{"role": "assistant", "content": answer}],
        }

    safe, structured, recovered = _recovery(state, route)
    return {
        "verifier_ok": False,
        "errors": reasons,
        "route": recovered.value,
        "final_answer": safe,
        "structured_answer": structured.model_dump(mode="json"),
        "messages": [{"role": "assistant", "content": safe}],
        "last_structured_answer": structured.model_dump(mode="json"),
        "pending_action": None,
        "approval_required": False,
        "pending_request": None,
    }


def _recovery(state: dict[str, Any], route: AgentRoute) -> tuple[str, StructuredAnswer, AgentRoute]:
    """Con qué se reemplaza una respuesta que no pasó el control.

    Nunca es el final de la conversación salvo que de verdad no haya con qué contestar.
    """
    if route in FREE_TEXT_ROUTES:
        text = presentation.CONVERSATION_RECOVERY
        return text, StructuredAnswer(verdict="info", headline=text), AgentRoute.CONVERSATIONAL

    # Turno de datos: la plantilla determinística es la fuente correcta, así que se intenta
    # antes que cualquier mensaje de disculpa.
    structured = presentation.build_answer(AgentIntent(state["intent"]), _answer_context(state))
    if structured.verdict != "unavailable":
        return presentation.render(structured), structured, route

    text = "No pude verificar la respuesta, así que no la muestro. Probá el formulario manual."
    return text, StructuredAnswer(verdict="unavailable", headline=text), AgentRoute.ERROR


def apply_write(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Se ejecuta SOLO tras aprobación (reanudación desde el checkpoint)."""
    ctx = _ctx(config)
    pending = state.get("pending_action")
    if not pending:
        return {"approval_required": False}

    from app.services.ai_chat_service import apply_pending_action

    if state.get("approved"):
        answer = apply_pending_action(ctx, pending)
    else:
        apply_pending_action(ctx, pending, reject=True)
        answer = "Listo, no registré nada. El movimiento quedó descartado."

    return {
        "final_answer": answer,
        "structured_answer": StructuredAnswer(verdict="info", headline=answer).model_dump(
            mode="json"
        ),
        "approval_required": False,
        "pending_action": None,
        "pending_request": None,
        "messages": [{"role": "assistant", "content": answer}],
    }


def route_after_verify(state: dict[str, Any]) -> str:
    return "apply_write" if state.get("approval_required") else "__end__"
