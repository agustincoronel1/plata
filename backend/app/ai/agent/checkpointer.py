"""Dueño del checkpointer de LangGraph: pool de conexiones, `setup()` y cierre.

QUÉ PERSISTE Y POR QUÉ IMPORTA
------------------------------
El estado conversacional del copiloto vive acá: el historial multi-turn y, sobre todo, la
**acción pendiente de aprobación**. Si eso se pierde, una persona que pidió registrar un
gasto y no llegó a aprobarlo se queda con una acción imposible de resolver. En Render el
proceso se reinicia y se duerme, y puede haber más de una instancia, así que el estado tiene
que estar en PostgreSQL: en memoria se perdería en cada reinicio y cada instancia vería una
conversación distinta.

POR QUÉ UN POOL Y NO UNA CONEXIÓN
----------------------------------
`PostgresSaver.from_conn_string()` abre UNA conexión y la deja abierta para siempre. Eso
trae dos problemas en producción: todas las peticiones concurrentes del copiloto se
serializan sobre ella, y si esa conexión se muere (timeout de inactividad de Supabase, corte
de red) el copiloto queda roto hasta reiniciar el proceso, porque nadie la reabre.

`PostgresSaver` acepta un `ConnectionPool` directamente y saca una conexión por operación,
así que el pool resuelve las dos cosas: concurrencia real y reconexión automática. No es una
conexión nueva por mensaje —el pool las reutiliza— y `psycopg_pool` ya venía instalado como
dependencia de `langgraph-checkpoint-postgres`, así que no suma nada al proyecto.

`autocommit=True` y `row_factory=dict_row` no son opcionales: `setup()` usa
`CREATE INDEX CONCURRENTLY`, que no puede correr dentro de una transacción, y lee sus filas
por nombre de columna.

CUÁNDO SE INICIALIZA
--------------------
En el arranque de la aplicación (lifespan), no en la primera petición. Así el DDL de
`setup()` y la aplicación de RLS ocurren una sola vez, con la app todavía sin tráfico, y no
en medio del primer mensaje de alguien.

Si falla, la aplicación **igual arranca**: Plata funciona sin IA (dashboard, movimientos,
compromisos, simulaciones) y tumbar todo porque el copiloto no puede checkpointear sería
peor. Lo que se hace es dejar el copiloto indisponible con un 503 explícito y un
`logger.error` visible. Nunca se cae a memoria en silencio: perder el estado sin avisar es
justo lo que rompe una aprobación pendiente.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.ai.exceptions import AIError
from app.core.config import settings

logger = logging.getLogger(__name__)

POSTGRES = "postgres"
MEMORY = "memory"
VALID_MODES = (POSTGRES, MEMORY)

# Clave del advisory lock que serializa `setup()` entre instancias. Es un número arbitrario
# pero FIJO: lo único que importa es que todas las instancias de Plata usen el mismo.
_SETUP_LOCK_KEY = 8_171_993_042_115

# Cuánto se espera, como mucho, a que otra instancia termine su `setup()`.
_SETUP_LOCK_TIMEOUT_SECONDS = 30.0
_SETUP_LOCK_POLL_SECONDS = 0.2

# Tamaño del pool. Chico a propósito: el copiloto es una fracción del tráfico y cada
# operación del checkpointer es corta. El plan gratuito de Supabase tiene pocas conexiones y
# la aplicación ya usa otro pool para SQLAlchemy.
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 4

# Cuánto se espera a que la base conteste antes de dar el arranque por fallido. Corto a
# propósito: si PostgreSQL no está, conviene enterarse en el log de arranque y dejar el
# copiloto en 503, no demorar el despliegue entero esperando a algo que no va a venir.
_CONNECT_TIMEOUT_SECONDS = 5
_POOL_OPEN_TIMEOUT_SECONDS = 5


class CheckpointerUnavailableError(AIError):
    """El copiloto no puede persistir su estado, así que no atiende.

    503 y no 500: no es un error de la petición sino del servidor, y se resuelve solo cuando
    PostgreSQL vuelve. El detalle real (host, credenciales, driver) queda en el log; al
    cliente le llega un texto que dice qué puede hacer mientras tanto.
    """

    status_code = 503
    default_detail = (
        "El copiloto no está disponible en este momento. Podés seguir usando las funciones "
        "manuales de Plata."
    )


def resolve_mode() -> str:
    """Modo configurado, validado. Un valor desconocido corta con un mensaje claro.

    Antes, cualquier valor que no fuera exactamente "memory" caía en PostgreSQL. Que un typo
    funcione de casualidad es tan malo como que rompa: nadie se entera de que la
    configuración que creía tener no es la que está corriendo.
    """
    mode = (settings.ai_checkpoint_store or "").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            f"AI_CHECKPOINT_STORE inválido: {settings.ai_checkpoint_store!r}. "
            f"Valores permitidos: {', '.join(VALID_MODES)}."
        )
    return mode


def _connection_string() -> str:
    """La misma URL que usa el resto de la aplicación, en el dialecto de psycopg.

    SQLAlchemy la escribe como `postgresql+psycopg://`; psycopg quiere `postgresql://`.
    """
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


class _Checkpointer:
    """Dueño del pool y del saver.

    Es una clase y no un puñado de variables globales para que los tests puedan cerrarlo y
    volver a crearlo sin depender de limpiar estado suelto por el módulo. La instancia
    compartida es `_shared`, y `reset()` la deja como recién importada.
    """

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._saver: Any | None = None
        self._memory_saver: Any | None = None
        # Protege la creación perezosa: dos peticiones concurrentes no pueden armar dos
        # pools. FastAPI atiende los endpoints sync en un threadpool, así que esto pasa.
        self._lock = threading.Lock()

    # --- API pública ---

    def get(self) -> Any:
        """Saver listo para usar. Lo crea si hace falta.

        En modo postgres, un fallo se traduce a `CheckpointerUnavailableError`: nunca se
        devuelve un saver en memoria como reemplazo.
        """
        mode = resolve_mode()
        if mode == MEMORY:
            return self._get_memory_saver()

        with self._lock:
            if self._saver is None:
                self._saver = self._build_postgres_saver()
            return self._saver

    def start(self) -> bool:
        """Inicializa el checkpointer en el arranque. True si quedó listo.

        No propaga el error: la aplicación tiene que poder arrancar sin copiloto. El fallo
        queda en el log y la primera petición al copiloto responde 503.
        """
        try:
            mode = resolve_mode()
        except ValueError:
            # Configuración inválida: esto SÍ es un error de despliegue y tiene que
            # cortar el arranque, no quedar escondido en un log.
            raise

        if mode == MEMORY:
            logger.warning(
                "Checkpointer del copiloto EN MEMORIA: el estado de las conversaciones y las "
                "acciones pendientes se pierden al reiniciar. Solo para desarrollo y tests."
            )
            return True

        try:
            self.get()
        except CheckpointerUnavailableError:
            logger.error(
                "El checkpointer de PostgreSQL no pudo inicializarse. La aplicación arranca "
                "igual, pero el copiloto va a responder 503 hasta que la base vuelva.",
                exc_info=True,
            )
            return False

        logger.info("Checkpointer del copiloto listo sobre PostgreSQL.")
        return True

    def close(self) -> None:
        """Libera el pool. Idempotente: llamarlo dos veces no rompe nada."""
        with self._lock:
            pool, self._pool = self._pool, None
            self._saver = None

        if pool is not None:
            try:
                pool.close()
                logger.info("Pool del checkpointer cerrado.")
            except Exception:
                logger.warning("Error al cerrar el pool del checkpointer.", exc_info=True)

    def reset(self) -> None:
        """Cierra y olvida todo, incluido el saver en memoria. Para tests."""
        self.close()
        self._memory_saver = None

    @property
    def pool(self) -> ConnectionPool | None:
        """El pool activo, o None. Para tests que comprueban el cierre."""
        return self._pool

    # --- Internos ---

    def _get_memory_saver(self) -> Any:
        from langgraph.checkpoint.memory import MemorySaver

        if self._memory_saver is None:
            self._memory_saver = MemorySaver()
        return self._memory_saver

    def _build_postgres_saver(self) -> Any:
        from langgraph.checkpoint.postgres import PostgresSaver

        try:
            pool = ConnectionPool(
                conninfo=_connection_string(),
                min_size=_POOL_MIN_SIZE,
                max_size=_POOL_MAX_SIZE,
                # `autocommit` porque `setup()` usa CREATE INDEX CONCURRENTLY, que no corre
                # dentro de una transacción. `dict_row` porque el saver lee por nombre de
                # columna. `prepare_threshold=0` evita sentencias preparadas, que se rompen
                # con los poolers en modo transacción (el de Supabase, sin ir más lejos).
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                    "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
                },
                # Sin esto, el pool entrega conexiones muertas después de un corte.
                check=ConnectionPool.check_connection,
                open=False,
            )
            pool.open(wait=True, timeout=_POOL_OPEN_TIMEOUT_SECONDS)
        except Exception as error:
            # El mensaje de psycopg puede traer host y usuario: no sale de acá.
            logger.error("No se pudo abrir el pool del checkpointer.", exc_info=True)
            raise CheckpointerUnavailableError from error

        try:
            saver = PostgresSaver(pool)
            _run_setup(pool, saver)
        except Exception as error:
            pool.close()
            logger.error("Falló la inicialización del checkpointer.", exc_info=True)
            raise CheckpointerUnavailableError from error

        self._pool = pool
        return saver


def _run_setup(pool: ConnectionPool, saver: Any) -> None:
    """Crea las tablas del checkpointer y les aplica RLS, una instancia por vez.

    `PostgresSaver.setup()` lee la última versión de `checkpoint_migrations` y después
    inserta las que faltan. Esa columna es `PRIMARY KEY`, así que dos instancias arrancando
    a la vez contra una base nueva leen la misma versión, aplican la misma migración y la
    segunda muere con una violación de unicidad. Un advisory lock lo serializa: la que llega
    segunda espera, y cuando entra ya no encuentra migraciones pendientes, así que no
    ejecuta ningún DDL.

    El lock se toma con `pg_try_advisory_lock` en un bucle en lugar de `pg_advisory_lock`
    a secas, y eso importa: la versión bloqueante deja una sentencia esperando: y
    `CREATE INDEX CONCURRENTLY` —que la otra instancia está corriendo— espera a que
    terminen las transacciones abiertas. Las dos se esperarían para siempre. Sondeando, cada
    intento vuelve enseguida y nadie bloquea a nadie.
    """
    with pool.connection() as conn:
        acquired = _acquire_setup_lock(conn)
        try:
            saver.setup()
            _apply_rls(conn)
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s)", (_SETUP_LOCK_KEY,))


def _acquire_setup_lock(conn: Any) -> bool:
    """Intenta tomar el lock hasta agotar el tiempo. True si lo consiguió.

    Si no lo consigue, se sigue igual: perder el lock significa que otra instancia ya hizo
    el trabajo, y `setup()` es idempotente. Quedarse sin copiloto por no haber conseguido un
    lock sería peor que ejecutar un `setup()` que no encuentra nada para hacer.
    """
    deadline = time.monotonic() + _SETUP_LOCK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (_SETUP_LOCK_KEY,))
        row = result.fetchone()
        if row and row["locked"]:
            return True
        time.sleep(_SETUP_LOCK_POLL_SECONDS)

    logger.warning(
        "No se pudo tomar el lock de inicialización del checkpointer en %s segundos; "
        "se continúa porque setup() es idempotente.",
        _SETUP_LOCK_TIMEOUT_SECONDS,
    )
    return False


def _apply_rls(conn: Any) -> None:
    """Aplica RLS a las tablas que acaba de crear LangGraph.

    Esas tablas las crea `setup()`, no Alembic, así que la migración de RLS del Bloque 1 no
    puede protegerlas si todavía no existían cuando corrió. La migración dejó
    `plata_secure_langgraph_tables()` justamente para esto, y es idempotente.

    Best-effort: si la función no está (base sin migrar) o el rol no puede hacer DDL, se
    registra y se sigue. El aislamiento entre cuentas del copiloto no depende de esto —el
    `thread_id` ya lleva el `user_id` adentro—, así que quedarse sin copiloto por no haber
    podido aplicar una segunda barrera sería desproporcionado.
    """
    try:
        conn.execute("SELECT public.plata_secure_langgraph_tables()")
    except Exception:
        logger.warning(
            "No se pudo aplicar RLS a las tablas del checkpointer. Revisá que la migración "
            "f2b3c4d5e6f7 esté aplicada y ejecutá "
            "'SELECT public.plata_secure_langgraph_tables();' a mano.",
            exc_info=True,
        )


# Instancia compartida por el proceso.
_shared = _Checkpointer()


def get_checkpointer() -> Any:
    return _shared.get()


def start_checkpointer() -> bool:
    return _shared.start()


def close_checkpointer_pool() -> None:
    _shared.close()


def reset_checkpointer() -> None:
    _shared.reset()


def active_pool() -> ConnectionPool | None:
    return _shared.pool
