"""Cerebro del copiloto: clasificación de intención y redacción grounded.

Abstracción de proveedor (igual que el parser): `AgentBrain` es la interfaz; `MockAgentBrain`
es determinístico y sin coste (default en dev/tests/evals); `OpenAIAgentBrain` es el real,
perezoso y desacoplado. El cerebro NO calcula montos: solo clasifica y arma texto a partir de
los resultados de las tools y la evidencia. Los números de la respuesta salen de ahí, no del
modelo.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.ai.agent.presentation import public_tool_output
from app.ai.agent.schemas import AgentIntent
from app.ai.exceptions import (
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIStructuredOutputError,
)
from app.ai.providers.mock import _normalize

_NUM = r"(\d+(?:[.\s]\d{3})*(?:,\d+)?|\d+)"

# El razonamiento vuelve cifrado y se reenvía tal cual; nunca se descifra ni se muestra.
REASONING_INCLUDE = ["reasoning.encrypted_content"]


def _to_decimal(raw: str) -> Decimal:
    cleaned = raw.replace(" ", "").replace(".", "").replace(",", ".")
    return Decimal(cleaned)


def extract_amount(normalized: str) -> Decimal | None:
    """Extrae un monto en ARS de texto normalizado (lucas=miles, palo=millones, mil)."""
    m = re.search(rf"{_NUM}\s*(lucas?|palos?|mil(?:lones)?|millon)", normalized)
    if m:
        value = _to_decimal(m.group(1))
        unit = m.group(2)
        if unit.startswith("luca"):
            return value * 1000
        if unit.startswith("palo") or unit.startswith("millon") or unit == "millones":
            return value * 1_000_000
        if unit.startswith("mil"):
            return value * 1000
    m = re.search(rf"{_NUM}", normalized)
    return _to_decimal(m.group(1)) if m else None


def extract_installments(normalized: str) -> int | None:
    m = re.search(r"(\d+)\s*cuotas?", normalized)
    return int(m.group(1)) if m else None


def looks_like_money(normalized: str) -> bool:
    return bool(
        re.search(r"\d[\d.\s,]*\s*(lucas?|palos?|mil(?:lones)?|millon)", normalized)
        or re.search(r"\bson\s+\d", normalized)
    )


# Financiación EXPLÍCITA. Si nada de esto aparece, la compra es al contado y no se simulan
# cuotas: hablar de "primera cuota" para un pago único es exactamente lo que hay que evitar.
_FINANCING_PATTERNS = (
    r"\bcuotas?\b",
    # financiado/financiación/financiar, pero NO "situación financiera".
    r"\bfinanci(?:ado|ada|ar|arlo|arla|acion|amiento)\b",
    r"\b\d+\s*meses\b",
    r"\ben\s+\d+\s*pagos\b",
    r"\b(pagar|pago|abonar|abono)\b.{0,20}\b(mes que viene|mes proximo|proximo mes)\b",
    r"\b(empiezo|empezar|arranco|arrancar)\b.{0,20}\b(mes que viene|mes proximo|proximo mes)\b",
)

_PURCHASE_ASK = re.compile(r"\b(puedo|podria|conviene|alcanza|deberia)\b")
_PURCHASE_VERB = re.compile(r"\b(gastar|comprar|compro|gasto|invertir|permitirme)\b")
_ASKS_HOW_MUCH = re.compile(r"\bcuanto\b")
_PER_DAY = re.compile(r"\bpor dia\b|\bdiario\b|\bdiaria\b|\bcada dia\b|\bal dia\b")
_CATEGORY = re.compile(r"\ben\s+([a-zñ]{3,})\b")
_CATEGORY_STOPWORDS = {"cuotas", "cuota", "pagos", "total", "meses", "efectivo", "mano"}


def mentions_installments(normalized: str) -> bool:
    """True solo si la persona menciona financiación de forma explícita."""
    return any(re.search(pattern, normalized) for pattern in _FINANCING_PATTERNS)


def is_purchase_question(normalized: str) -> bool:
    """'¿Puedo gastar…?', '¿Me conviene comprar…?': pregunta por una compra concreta."""
    return bool(_PURCHASE_ASK.search(normalized) and _PURCHASE_VERB.search(normalized))


def asks_daily_budget(normalized: str) -> bool:
    """'¿Cuánto puedo gastar por día hasta cobrar?'"""
    return bool(_PER_DAY.search(normalized)) and bool(
        _ASKS_HOW_MUCH.search(normalized) or _PURCHASE_VERB.search(normalized)
    )


def extract_category(normalized: str) -> str | None:
    for match in _CATEGORY.finditer(normalized):
        word = match.group(1)
        if word not in _CATEGORY_STOPWORDS:
            return word
    return None


class AgentBrain(Protocol):
    def classify(self, message: str, history: list[dict[str, Any]]) -> dict[str, Any]: ...
    def answer(self, intent: AgentIntent, context: dict[str, Any]) -> str: ...


class AgentPlanArgs(BaseModel):
    """Pistas del planner, con schema cerrado.

    El modo `strict` de la Responses API no admite objetos libres (`dict[str, Any]` se
    serializa como `additionalProperties: true` y la API lo rechaza). Los argumentos que el
    modelo puede sugerir son pocos y conocidos, así que viajan tipados. Las claves internas
    (`_tool_calls`, `missing_fields`) las arma el backend, nunca el modelo.
    """

    model_config = ConfigDict(extra="forbid")

    amount: str | None = None
    installments: int | None = None
    query: str | None = None
    category: str | None = None


class AgentPlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AgentIntent = AgentIntent.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    args: AgentPlanArgs = Field(default_factory=AgentPlanArgs)


class AgentFinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # El límite duro es corto a propósito: el estilo se pide en el prompt, pero el schema
    # es la barrera que el modelo no puede ignorar. Igual la respuesta que se muestra la
    # arma la capa de presentación cuando hay plantilla para la intención.
    answer: str = Field(min_length=1, max_length=700)


# Estilo del copiloto. Se repite en el planner y en el redactor porque cualquiera de los dos
# puede terminar hablándole a la persona. NO es la única protección: `presentation` valida y
# reemplaza el texto libre que exponga campos internos o se vaya de largo.
STYLE_RULES = (
    "Escribís en español rioplatense (podés, tenés, te conviene, te quedarían, hasta que "
    "cobres; nunca puedes ni tienes). Respondés en 4 a 6 líneas como máximo: primero la "
    "decisión, después el monto principal, después una explicación breve y, solo si aporta, "
    "UNA sola recomendación. Los montos van en formato argentino ($20.000, $1.200.000, sin "
    "decimales) y las fechas como 29/07 o 01/08/2026, nunca 2026-08-01. Está prohibido "
    "nombrar campos, tools o schemas internos (spendable_total, current_balance, is_viable, "
    "breaks_reserves, minimum_margin, risk_months, conclusion y similares): traducilos a "
    "lenguaje común. No repitas todos los datos de una herramienta, no ofrezcas una lista de "
    "opciones al final y no uses lenguaje técnico. Si la compra es al contado, no menciones "
    "cuotas ni primera cuota."
)

# Ruteo de compras: al contado y en cuotas son caminos distintos.
ROUTING_RULES = (
    "Para una compra al contado ('¿puedo gastar 18.000 en ropa hoy?') usá "
    "check_one_time_purchase. Usá simulate_purchase_preview SOLO si la persona menciona "
    "explícitamente cuotas, meses, financiación, primera cuota o pagar el mes que viene."
)


# Palabras clave por intención (se evalúan en orden de prioridad).
_INJECTION = ("ignora", "borra", "eliminá", "elimina", "instrucciones", "sos un asistente")
_TX_VERBS = ("gaste", "pague", "compre", "cobre", "gane", "transferi", "me entro")

# Verbos con los que se pide agendar algo a futuro. Todos en presente o imperativo, así que
# no se pisan con `_TX_VERBS`, que registran algo YA ocurrido.
_SCHEDULE_VERBS = (
    "agenda",
    "agendar",
    "agregar",
    "agrega",
    "anotar",
    "anota",
    "recordame",
    "recordar",
)


def _today() -> date:
    """Hoy en la zona de negocio, igual que el resto de la aplicación."""
    from app.core.timezone import app_today

    return app_today()


class MockAgentBrain:
    """Clasificador determinístico + redactor grounded, sin coste."""

    def classify(self, message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        n = _normalize(message)
        args: dict[str, Any] = {}

        if any(w in n for w in _INJECTION) and not any(v in n for v in _TX_VERBS):
            return {"intent": AgentIntent.UNKNOWN, "confidence": 0.2, "args": args}

        # Compara fechas reusando el contexto de una simulación previa (multi-turn).
        if (
            history
            and ("y si" in n or "empiezo" in n)
            and ("mes" in n or "proximo" in n or "viene" in n)
        ):
            return {"intent": AgentIntent.COMPARE_PURCHASE_DATES, "confidence": 0.7, "args": args}

        if asks_daily_budget(n):
            return {"intent": AgentIntent.DAILY_BUDGET, "confidence": 0.8, "args": args}

        installments = extract_installments(n)
        amount = extract_amount(n)
        financed = mentions_installments(n)

        if financed or "simular" in n or "simula" in n:
            args["installments"] = installments
            args["amount"] = str(amount) if amount is not None else None
            intent = AgentIntent.SIMULATE_PURCHASE
            if ("mes" in n and ("viene" in n or "proximo" in n or "empiezo" in n)) and history:
                intent = AgentIntent.COMPARE_PURCHASE_DATES
            return {"intent": intent, "confidence": 0.8, "args": args}

        # Compra al contado: hay monto y se pregunta por una compra, sin mencionar cuotas.
        if is_purchase_question(n) and amount is not None and not _ASKS_HOW_MUCH.search(n):
            args["amount"] = str(amount)
            category = extract_category(n)
            if category:
                args["category"] = category
            return {"intent": AgentIntent.ONE_TIME_PURCHASE, "confidence": 0.8, "args": args}

        if any(v in n for v in _TX_VERBS) and extract_amount(n) is not None:
            return {"intent": AgentIntent.CREATE_TRANSACTION, "confidence": 0.75, "args": args}

        if (
            "tengo que pagar" in n
            or "necesito pagar" in n
            or "pagar el" in n
            or "vence" in n
            or "compromiso" in n
            or "alquiler" in n
            or "aumenta" in n
            # Verbos de agendar. Sin esto, "agendá netflix para el 20" no se reconocía como
            # alta de compromiso y la conversación no llegaba a ninguna parte. No chocan con
            # `_TX_VERBS`, que son todos en pasado ("gasté", "pagué").
            or any(verb in n for verb in _SCHEDULE_VERBS)
        ):
            # Qué falta lo decide la MISMA extracción que después arma el borrador, en vez de
            # una lista de nombres aparte: antes acá se repetían los cuatro conceptos
            # hardcodeados del router, así que el clasificador podía decir "me falta el
            # nombre" de algo que el router sí sabía leer, y viceversa.
            #
            # El import es diferido porque `router` importa `extract_amount` de este módulo.
            from app.ai.agent.router import extract_commitment_fields

            campos = extract_commitment_fields(message, {}, _today(), None)
            args["amount"] = campos.get("amount")
            args["missing_fields"] = campos["missing_fields"]
            return {"intent": AgentIntent.CREATE_COMMITMENT, "confidence": 0.7, "args": args}

        if "pagos" in n or "compromisos" in n or "vencimientos" in n or "antes de cobrar" in n:
            return {"intent": AgentIntent.LIST_COMMITMENTS, "confidence": 0.8, "args": args}

        if "por que" in n or "explicame" in n or "explica" in n:
            return {"intent": AgentIntent.EXPLAIN_AVAILABLE_MONEY, "confidence": 0.7, "args": args}

        if (
            "cuanto gaste" in n
            or "buscar" in n
            or "parecidos" in n
            or "movimientos" in n
            or "gaste en" in n
            or "gastos" in n
        ):
            args["query"] = message
            return {"intent": AgentIntent.SEARCH_HISTORY, "confidence": 0.7, "args": args}

        if (
            "cuanto puedo gastar" in n
            or "disponible" in n
            or "situacion" in n
            or "resumen" in n
            or "como estoy" in n
        ):
            return {"intent": AgentIntent.DASHBOARD_SUMMARY, "confidence": 0.8, "args": args}

        return {"intent": AgentIntent.UNKNOWN, "confidence": 0.3, "args": args}

    def answer(self, intent: AgentIntent, context: dict[str, Any]) -> str:
        from app.ai.agent.answers import render_answer

        return render_answer(intent, context)


class OpenAIAgentBrain:  # pragma: no cover - requiere red y API key
    """Cerebro real. Perezoso: no toca el SDK ni la red sin API key."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._model = settings.ai_model
        self._api_key = settings.ai_api_key
        self._timeout = settings.ai_timeout_seconds
        self._max_retries = settings.ai_max_retries
        self._max_iterations = getattr(settings, "ai_agent_max_iterations", 4)

    def _fail_without_key(self) -> None:
        from app.ai.exceptions import AIProviderUnavailableError

        if not self._settings.ai_api_key:
            raise AIProviderUnavailableError(
                "El copiloto real no está configurado (falta la API key)."
            )

    def classify(self, message: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        self._fail_without_key()
        completion = self._call_model(
            input=[
                {
                    "role": "system",
                    "content": (
                        "Sos el planificador del copiloto financiero Vector. Elegí solo tools "
                        "del allowlist cuando hagan falta datos o una escritura. Si no hace "
                        "falta tool, devolvé una intención estructurada. No calcules saldos. "
                        + ROUTING_RULES
                    ),
                },
                *_history_tail(history),
                {"role": "user", "content": message},
            ],
            text_format=AgentPlanOutput,
            tools=_tool_specs(),
            tool_choice="auto",
            store=False,
            metadata={"task": "agent_classify"},
        )
        tool_calls = _extract_tool_calls(completion)
        if tool_calls:
            if len(tool_calls) > self._max_iterations:
                raise AIStructuredOutputError
            return {
                "intent": _intent_from_tool_calls(tool_calls),
                "confidence": 0.85,
                "args": {"_tool_calls": tool_calls},
            }

        parsed = getattr(completion, "output_parsed", None)
        if parsed is None:
            raise AIStructuredOutputError
        try:
            plan = AgentPlanOutput.model_validate(parsed)
        except ValidationError as exc:
            raise AIStructuredOutputError from exc
        return {
            "intent": plan.intent,
            "confidence": plan.confidence,
            "args": plan.args.model_dump(exclude_none=True),
        }

    def run_agentic(self, message: str, history: list[dict[str, Any]], ctx: Any) -> dict[str, Any]:
        """Loop real Responses API: function_call -> backend tool -> function_call_output."""
        self._fail_without_key()
        from app.ai.agent.tools import blocked_sensitive_tool_result, is_write_tool, run_tool

        items: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Sos el copiloto financiero Vector. Usá solo tools del allowlist. "
                    "No calcules saldos ni totales: pedí tools. Las escrituras solo "
                    "preparan drafts y requieren aprobación humana. "
                    + ROUTING_RULES
                    + " "
                    + STYLE_RULES
                ),
            },
            *_history_tail(history),
            {"role": "user", "content": message},
        ]
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        approval = False
        sensitive_write_executed = False

        for _round in range(self._max_iterations):
            completion = self._call_model(
                input=items,
                text_format=AgentFinalAnswer,
                tools=_tool_specs(),
                tool_choice="auto",
                store=False,
                # El contexto se administra a mano: pedimos el razonamiento cifrado para
                # poder reenviarlo en la ronda siguiente sin que el proveedor guarde nada.
                include=REASONING_INCLUDE,
                metadata={"task": "agent_chat"},
            )
            calls = _extract_tool_calls(completion, require_call_id=True)
            if not calls:
                parsed = getattr(completion, "output_parsed", None)
                if parsed is None:
                    raise AIStructuredOutputError
                answer = AgentFinalAnswer.model_validate(parsed).answer
                intent = _intent_from_tool_calls(tool_calls) if tool_calls else AgentIntent.UNKNOWN
                return {
                    "intent": intent,
                    "intent_confidence": 0.85 if tool_calls else 0.5,
                    "planner_args": {"_agentic": True},
                    "tool_calls": tool_calls,
                    "tool_results": tool_results,
                    "retrieved_evidence": evidence,
                    "pending_action": pending,
                    "approval_required": approval,
                    "final_answer": answer,
                    # El turno del asistente lo agrega generate_answer con el texto que
                    # realmente se muestra (la presentación puede reemplazar este borrador).
                    "messages": [{"role": "user", "content": message}],
                    "agentic_done": True,
                }

            output_items = [_safe_response_item(item) for item in getattr(completion, "output", [])]
            items.extend(item for item in output_items if item)
            for call in calls:
                writes = is_write_tool(call["name"])
                if sensitive_write_executed and writes:
                    rec = blocked_sensitive_tool_result(call["name"], call["arguments"])
                else:
                    if writes:
                        sensitive_write_executed = True
                    rec = run_tool(ctx, call["name"], call["arguments"])
                tool_calls.append(
                    {
                        "name": call["name"],
                        "call_id": call["call_id"],
                        "arguments": call["arguments"],
                    }
                )
                tool_results.append(rec)
                if rec["name"] == "search_transactions" and rec["ok"]:
                    evidence = rec["data"]["evidence"]
                if (
                    pending is None
                    and rec.get("writes")
                    and rec["ok"]
                    and rec["data"]
                    and rec["data"].get("is_confirmable")
                ):
                    from uuid import uuid4

                    data = rec["data"]
                    pending = {
                        "action_id": str(uuid4()),
                        "kind": data["kind"],
                        "summary": data["summary"],
                        "draft_id": data["draft_id"],
                        "draft": data.get("fields") or {},
                    }
                    approval = True
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(
                            _safe_tool_output(rec), default=str, ensure_ascii=False
                        ),
                    }
                )

        raise AIStructuredOutputError("La IA necesitó demasiadas rondas para responder.")

    def answer(self, intent: AgentIntent, context: dict[str, Any]) -> str:
        self._fail_without_key()
        completion = self._call_model(
            input=[
                {
                    "role": "system",
                    "content": (
                        "Redactá la respuesta del copiloto usando solo los datos provistos. "
                        "No inventes montos ni digas que guardaste una escritura si quedó "
                        "pendiente de aprobación. " + STYLE_RULES
                    ),
                },
                {
                    "role": "user",
                    # Los datos llegan ya seleccionados y formateados: el modelo redacta,
                    # no vuelca el JSON crudo de la tool.
                    "content": json.dumps(
                        {
                            "datos": [
                                public_tool_output(rec) for rec in context.get("tool_results", [])
                            ],
                            "movimientos": [
                                {"detalle": e.get("excerpt"), "monto": e.get("amount")}
                                for e in context.get("evidence", [])
                            ],
                            "accion_pendiente": (context.get("pending_action") or {}).get(
                                "summary"
                            ),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            text_format=AgentFinalAnswer,
            store=False,
            metadata={"task": "agent_answer"},
        )
        parsed = getattr(completion, "output_parsed", None)
        if parsed is None:
            raise AIStructuredOutputError
        try:
            return AgentFinalAnswer.model_validate(parsed).answer
        except ValidationError as exc:
            raise AIStructuredOutputError from exc

    def _call_model(self, **kwargs: Any) -> Any:
        client_cls, api_error, api_timeout = _load_openai_sdk()
        client = client_cls(
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        try:
            started = time.monotonic()
            result = client.responses.parse(model=self._model, **kwargs)
            result._plata_latency_ms = int((time.monotonic() - started) * 1000)
            return result
        except api_timeout as exc:
            raise AIProviderTimeoutError from exc
        except api_error as exc:
            raise AIProviderUnavailableError from exc
        except (AIProviderTimeoutError, AIProviderUnavailableError, AIStructuredOutputError):
            raise
        except Exception as exc:
            raise AIProviderUnavailableError from exc


def _history_tail(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    safe: list[dict[str, str]] = []
    for item in history[-6:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            safe.append({"role": role, "content": content[:1000]})
    return safe


def strict_parameters(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema de un modelo en el subconjunto `strict` de la Responses API.

    El schema crudo de Pydantic no sirve: strict exige `additionalProperties: false` y TODAS
    las propiedades en `required` en cada objeto, y Pydantic solo marca las que no tienen
    default (además de emitir `default: null` en los opcionales, que strict rechaza). El
    resultado sería un 400 `invalid_function_parameters` antes de ejecutar una sola tool.

    Se usa la transformación **oficial** del SDK instalado cuando está disponible, así el
    contrato lo define quien valida del otro lado. Si el SDK no está (Vector tiene que poder
    importarse sin él), cae a una implementación local equivalente. Los modelos de dominio
    quedan intactos: los argumentos se siguen validando con Pydantic al volver.
    """
    schema = _sdk_strict_schema(model)
    if schema is None:
        schema = _local_strict_schema(model.model_json_schema())
    return _drop_null_defaults(schema)


def _sdk_strict_schema(model: type[BaseModel]) -> dict[str, Any] | None:
    """Transformación oficial del SDK. `None` si no está instalado o no la soporta."""
    try:
        from openai import pydantic_function_tool
    except ImportError:
        return None
    try:
        # El helper devuelve el formato de Chat Completions; la Responses API usa el mismo
        # schema de parámetros, en un envoltorio plano.
        return pydantic_function_tool(model, name=model.__name__)["function"]["parameters"]
    except Exception:  # noqa: BLE001 - ante cualquier rareza del SDK, transformamos acá
        return None


def _local_strict_schema(schema: Any) -> Any:
    """Equivalente local: cierra objetos y completa `required` en todo el árbol.

    Recorre `$defs`, `properties`, `items`, `anyOf`, `allOf` y `oneOf` por recursión genérica.
    Los `$ref` se dejan como están: strict los admite mientras el `$defs` apuntado también
    esté normalizado, y este recorrido lo normaliza.
    """
    if isinstance(schema, list):
        return [_local_strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: _local_strict_schema(value) for key, value in schema.items()}
    if out.get("type") == "object" or "properties" in out:
        properties = out.get("properties") or {}
        out["properties"] = properties
        out["additionalProperties"] = False
        out["required"] = list(properties)
    return out


def _drop_null_defaults(schema: Any) -> Any:
    """Saca los `default: null`, que strict rechaza (el null ya viaja en el `anyOf`)."""
    if isinstance(schema, list):
        return [_drop_null_defaults(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: _drop_null_defaults(value)
        for key, value in schema.items()
        if not (key == "default" and value is None)
    }


def _tool_specs() -> list[dict[str, Any]]:
    from app.ai.agent.tools import TOOLS

    specs = []
    for name, tool in TOOLS.items():
        specs.append(
            {
                "type": "function",
                "name": name,
                "description": f"Vector tool autorizada: {name}.",
                "parameters": strict_parameters(tool.args_model),
                "strict": True,
            }
        )
    return specs


def _extract_tool_calls(completion: Any, *, require_call_id: bool = False) -> list[dict[str, Any]]:
    output = getattr(completion, "output", []) or []
    calls: list[dict[str, Any]] = []
    for item in output:
        kind = _get(item, "type")
        if kind not in ("function_call", "tool_call"):
            continue
        name = _get(item, "name")
        call_id = _get(item, "call_id") or _get(item, "id")
        raw_args = _get(item, "arguments") or {}
        if not isinstance(name, str):
            raise AIStructuredOutputError
        if require_call_id and not isinstance(call_id, str):
            raise AIStructuredOutputError
        call = {"name": name, "arguments": _validate_tool_arguments(name, raw_args)}
        if call_id:
            call["call_id"] = call_id
        calls.append(call)
    return calls


def _validate_tool_arguments(name: str, raw_args: Any) -> dict[str, Any]:
    from app.ai.agent.tools import TOOLS

    tool = TOOLS.get(name)
    if tool is None:
        raise AIStructuredOutputError
    try:
        if isinstance(raw_args, str):
            args = TypeAdapter(tool.args_model).validate_json(raw_args)
        else:
            args = tool.args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise AIStructuredOutputError from exc
    return args.model_dump(mode="json", exclude_none=True)


def _intent_from_tool_calls(calls: list[dict[str, Any]]) -> AgentIntent:
    write_names = {c["name"] for c in calls}
    if "create_transaction_draft" in write_names:
        return AgentIntent.CREATE_TRANSACTION
    if "create_commitment_draft" in write_names:
        return AgentIntent.CREATE_COMMITMENT
    if "simulate_purchase_preview" in write_names:
        return AgentIntent.SIMULATE_PURCHASE
    if "check_one_time_purchase" in write_names:
        return AgentIntent.ONE_TIME_PURCHASE
    if "search_transactions" in write_names:
        return AgentIntent.SEARCH_HISTORY
    if "list_pending_commitments" in write_names:
        return AgentIntent.LIST_COMMITMENTS
    if "get_financial_summary" in write_names:
        return AgentIntent.DASHBOARD_SUMMARY
    return AgentIntent.UNKNOWN


def _get(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _safe_response_item(item: Any) -> dict[str, Any] | None:
    kind = _get(item, "type")
    if kind == "reasoning":
        return _reasoning_item(item)
    if kind not in ("function_call", "tool_call"):
        return None
    name = _get(item, "name")
    call_id = _get(item, "call_id") or _get(item, "id")
    arguments = _get(item, "arguments") or "{}"
    if not isinstance(name, str) or not isinstance(call_id, str):
        raise AIStructuredOutputError
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def _reasoning_item(item: Any) -> dict[str, Any] | None:
    """Reenvía el item de razonamiento tal cual, para sostener el contexto entre rondas.

    Con `store=False` el proveedor no guarda nada, así que si el razonamiento no vuelve en
    el input siguiente, un modelo de razonamiento pierde el hilo (o el request es rechazado
    por referenciar un item que no mandamos). El contenido viaja **cifrado** y opaco: no se
    loguea, no entra al estado del grafo ni llega nunca al usuario.
    """
    dumped = _dump_item(item)
    reasoning: dict[str, Any] = {"type": "reasoning"}
    for key in ("id", "summary", "encrypted_content"):
        value = dumped.get(key)
        if value is not None:
            reasoning[key] = value
    if not isinstance(reasoning.get("id"), str):
        raise AIStructuredOutputError
    return reasoning


def _dump_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    return {key: _get(item, key) for key in ("id", "summary", "encrypted_content")}


def _safe_tool_output(rec: dict[str, Any]) -> dict[str, Any]:
    """Lo que ve el modelo de un tool result: la vista pública, no el JSON interno.

    Los montos ya vienen formateados en es-AR y las claves en lenguaje natural, así el
    modelo no puede copiar `spendable_total` ni `breaks_reserves` a la respuesta. Los datos
    crudos siguen en el estado del grafo para el verificador y la capa de presentación.
    """
    return {
        # El nombre viaja para que el modelo correlacione la llamada, no para mostrarlo.
        "name": rec["name"],
        "ok": rec["ok"],
        "error": rec.get("error"),
        "message": rec.get("message"),
        "datos": public_tool_output(rec),
    }


def _load_openai_sdk() -> tuple[Any, type[Exception], type[Exception]]:
    try:
        from openai import APITimeoutError, OpenAI, OpenAIError
    except ImportError as exc:
        raise AIProviderUnavailableError(
            "El proveedor de IA real no está instalado. Usá AI_PROVIDER=mock."
        ) from exc
    return OpenAI, OpenAIError, APITimeoutError


def build_brain(settings: Any) -> AgentBrain:
    if settings.ai_provider == "openai":
        if settings.ai_model.startswith("mock-"):
            raise AIProviderUnavailableError(
                "AI_MODEL debe ser un modelo real cuando AI_PROVIDER=openai."
            )
        return OpenAIAgentBrain(settings)
    return MockAgentBrain()
