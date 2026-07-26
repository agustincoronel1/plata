"""Construcción y compilación del grafo del copiloto (LangGraph).

    classify_intent → plan_tools → execute_tools → generate_answer → verify_results
        → (si hay escritura) apply_write [interrumpido para aprobación] → END
        → (si no) END

El checkpointer persiste el estado por `conversation_id` (thread_id): habilita multi-turn y
la reanudación tras aprobar/rechazar. `interrupt_before=["apply_write"]` pausa antes de tocar
la base: nada se escribe sin aprobación humana. `recursion_limit` acota los pasos.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.ai.agent import nodes
from app.ai.agent.state import AgentState
from app.core.config import settings

RECURSION_LIMIT = 12

_postgres_cm: Any | None = None


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("classify_intent", nodes.classify_intent)
    graph.add_node("plan_tools", nodes.plan_tools)
    graph.add_node("execute_tools", nodes.execute_tools)
    graph.add_node("generate_answer", nodes.generate_answer)
    graph.add_node("verify_results", nodes.verify_results)
    graph.add_node("apply_write", nodes.apply_write)

    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "plan_tools")
    graph.add_edge("plan_tools", "execute_tools")
    graph.add_edge("execute_tools", "generate_answer")
    graph.add_edge("generate_answer", "verify_results")
    graph.add_conditional_edges(
        "verify_results",
        nodes.route_after_verify,
        {"apply_write": "apply_write", "__end__": END},
    )
    graph.add_edge("apply_write", END)
    return graph


@lru_cache(maxsize=1)
def get_compiled_graph():
    """Grafo compilado + checkpointer configurado, creado de forma perezosa."""
    checkpointer = _build_checkpointer()
    return build_graph().compile(checkpointer=checkpointer, interrupt_before=["apply_write"])


def _build_checkpointer():
    if settings.ai_checkpoint_store == "memory":
        return MemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver

    global _postgres_cm
    if _postgres_cm is None:
        conn_string = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        _postgres_cm = PostgresSaver.from_conn_string(conn_string)
    saver = _postgres_cm.__enter__()
    saver.setup()
    return saver


def close_checkpointer() -> None:
    global _postgres_cm
    if _postgres_cm is not None:
        _postgres_cm.__exit__(None, None, None)
        _postgres_cm = None
    get_compiled_graph.cache_clear()
