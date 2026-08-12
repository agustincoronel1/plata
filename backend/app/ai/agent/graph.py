"""Construcción y compilación del grafo del copiloto (LangGraph).

    classify_intent → plan_tools → execute_tools → generate_answer → verify_results
        → (si hay escritura) apply_write [interrumpido para aprobación] → END
        → (si no) END

El checkpointer persiste el estado por `conversation_id` (thread_id): habilita multi-turn y
la reanudación tras aprobar/rechazar. `interrupt_before=["apply_write"]` pausa antes de tocar
la base: nada se escribe sin aprobación humana. `recursion_limit` acota los pasos.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.ai.agent import nodes
from app.ai.agent.checkpointer import (
    close_checkpointer_pool,
    get_checkpointer,
)
from app.ai.agent.state import AgentState

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 12


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
    """Grafo compilado con el checkpointer configurado.

    El grafo en sí no tiene estado y compilarlo es barato, pero se cachea porque el
    checkpointer queda pegado a la instancia compilada: recompilar en cada petición
    obligaría a resolver el saver una y otra vez.

    Quién es ese saver y cómo se conecta lo decide `app.ai.agent.checkpointer`; acá solo se
    lo pide. En modo postgres, si la base no está disponible, `get_checkpointer()` lanza
    `CheckpointerUnavailableError` (503) en lugar de devolver uno en memoria.
    """
    return build_graph().compile(checkpointer=get_checkpointer(), interrupt_before=["apply_write"])


def close_checkpointer() -> None:
    """Cierra el checkpointer y olvida el grafo compilado.

    Lo llama el shutdown de la aplicación y los tests que necesitan simular un reinicio del
    proceso. Hay que limpiar las dos cosas: el pool, para no dejar conexiones colgadas, y el
    grafo cacheado, que si no seguiría apuntando al saver viejo.
    """
    close_checkpointer_pool()
    get_compiled_graph.cache_clear()
