"""Qué checkpointer se elige y qué pasa cuando no se puede.

La regla que fijan estos tests es una sola: **producción persiste en PostgreSQL y nunca cae
a memoria en silencio**. Perder el estado sin avisar es lo que deja a alguien con una acción
pendiente de aprobación imposible de resolver, y es un fallo que no se nota hasta que un
usuario lo sufre.

Acá no hace falta base: se prueba la decisión de configuración y el manejo del error. La
persistencia real contra PostgreSQL vive en `test_checkpointer_postgres.py`.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import ValidationError

from app.ai.agent import checkpointer as cp
from app.core.config import Settings, settings


@pytest.fixture(autouse=True)
def _restore_mode():
    """Deja el modo como estaba: la suite entera corre en memoria (ver conftest)."""
    previous = settings.ai_checkpoint_store
    yield
    settings.ai_checkpoint_store = previous
    cp.reset_checkpointer()


# ---------- 1 y 2. Producción usa PostgresSaver y no cae a memoria ----------


def test_el_valor_por_defecto_es_postgres() -> None:
    """Olvidarse de definir la variable no puede dejar producción sin persistencia."""
    assert Settings.model_fields["ai_checkpoint_store"].default == "postgres"


def test_en_modo_postgres_nunca_se_devuelve_un_saver_en_memoria(monkeypatch) -> None:
    """Si PostgreSQL no está, se corta con 503; no se reemplaza por MemorySaver.

    Es el corazón del bloque: un fallback silencioso a memoria haría que el copiloto
    "funcione" mientras pierde cada conversación al reiniciar.
    """
    settings.ai_checkpoint_store = "postgres"
    cp.reset_checkpointer()
    monkeypatch.setattr(
        cp.settings, "database_url", "postgresql+psycopg://nadie:nada@127.0.0.1:1/no_existe"
    )

    with pytest.raises(cp.CheckpointerUnavailableError):
        cp.get_checkpointer()


def test_el_error_del_checkpointer_es_503_y_no_filtra_la_conexion() -> None:
    """El detalle que ve el cliente no puede traer host, usuario ni contraseña."""
    error = cp.CheckpointerUnavailableError()

    assert error.status_code == 503
    assert "postgresql" not in error.detail.lower()
    assert "password" not in error.detail.lower()
    # Y dice qué se puede hacer mientras tanto.
    assert "manuales" in error.detail


def test_arrancar_sin_base_no_rompe_la_aplicacion(monkeypatch) -> None:
    """El arranque informa el fallo y sigue: Vector sirve sin copiloto."""
    settings.ai_checkpoint_store = "postgres"
    cp.reset_checkpointer()
    monkeypatch.setattr(
        cp.settings, "database_url", "postgresql+psycopg://nadie:nada@127.0.0.1:1/no_existe"
    )

    assert cp.start_checkpointer() is False


# ---------- 3. Una configuración inválida falla claramente ----------


@pytest.mark.parametrize("invalido", ["postgress", "sqlite", "redis", "", "  "])
def test_un_modo_desconocido_corta_el_arranque(invalido: str) -> None:
    """Antes, cualquier cosa que no fuera "memory" caía en postgres de casualidad."""
    with pytest.raises(ValidationError) as error:
        Settings(ai_checkpoint_store=invalido)

    assert "AI_CHECKPOINT_STORE" in str(error.value)


def test_el_mensaje_del_error_dice_los_valores_permitidos() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(ai_checkpoint_store="mongo")

    mensaje = str(error.value)
    assert "postgres" in mensaje
    assert "memory" in mensaje


def test_resolve_mode_tambien_valida_una_asignacion_posterior() -> None:
    """Asignar el atributo a mano (como hacen los tests) no saltea la validación."""
    settings.ai_checkpoint_store = "cualquier_cosa"

    with pytest.raises(ValueError, match="AI_CHECKPOINT_STORE"):
        cp.resolve_mode()


@pytest.mark.parametrize(
    ("entrada", "esperado"), [("POSTGRES", "postgres"), (" Memory ", "memory")]
)
def test_el_modo_se_normaliza(entrada: str, esperado: str) -> None:
    assert Settings(ai_checkpoint_store=entrada).ai_checkpoint_store == esperado


# ---------- 4. La memoria es opt-in ----------


def test_memory_solo_se_activa_pidiendolo_explicitamente() -> None:
    settings.ai_checkpoint_store = "memory"
    cp.reset_checkpointer()

    assert isinstance(cp.get_checkpointer(), MemorySaver)


def test_el_saver_en_memoria_se_reutiliza_dentro_del_proceso() -> None:
    """Si se creara uno nuevo por petición, ni siquiera el multi-turn andaría en dev."""
    settings.ai_checkpoint_store = "memory"
    cp.reset_checkpointer()

    assert cp.get_checkpointer() is cp.get_checkpointer()


def test_en_modo_memoria_no_se_abre_ningun_pool() -> None:
    settings.ai_checkpoint_store = "memory"
    cp.reset_checkpointer()

    cp.get_checkpointer()

    assert cp.active_pool() is None
