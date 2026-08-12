"""Persistencia real del copiloto en PostgreSQL: reinicio, concurrencia, RLS y aislamiento.

Estos tests son la prueba del bloque y por eso corren contra PostgreSQL de verdad, no contra
mocks: lo que hay que demostrar es que el estado sobrevive a que el proceso se caiga, y eso
un mock no lo puede mostrar.

El "reinicio" se simula con `close_checkpointer()`, que cierra el pool y olvida el grafo
compilado. Después de eso no queda nada del checkpointer en memoria: si la conversación
sigue estando, es porque está en la base.

Un detalle de la infraestructura de tests que explica la forma de estos casos: la fixture
`db_session` abre una transacción externa que se revierte al final, pero el checkpointer usa
su PROPIO pool, fuera de esa transacción. Por eso las conversaciones que se crean acá se
limpian a mano al terminar.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.agent import checkpointer as cp
from app.ai.agent.graph import close_checkpointer, get_compiled_graph
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockAIProvider
from app.core.config import settings
from app.core.database import SessionLocal
from app.services import ai_chat_service
from app.services.draft_store_pg import PostgresDraftStore
from tests.conftest import OTHER_USER_ID, TEST_USER_ID, requires_postgres

pytestmark = requires_postgres

AS_OF = date(2026, 7, 24)
GASTO = "Gasté 25 lucas ayer en nafta con débito"


@pytest.fixture
def postgres_checkpointer() -> Iterator[None]:
    """Pone el checkpointer en modo postgres y lo deja como estaba al terminar.

    La suite entera corre en memoria (ver conftest); acá se pide PostgreSQL a propósito.
    """
    previous = settings.ai_checkpoint_store
    settings.ai_checkpoint_store = "postgres"
    close_checkpointer()
    try:
        yield
    finally:
        settings.ai_checkpoint_store = previous
        close_checkpointer()
        cp.reset_checkpointer()


@pytest.fixture
def limpiar_hilos() -> Iterator[list[str]]:
    """Borra de las tablas del checkpointer los hilos que creó el test.

    El checkpointer escribe con su propio pool, fuera de la transacción del test, así que no
    lo alcanza el rollback: si no se limpia, la base de desarrollo va juntando basura.
    """
    hilos: list[str] = []
    try:
        yield hilos
    finally:
        if hilos:
            with SessionLocal() as session:
                for tabla in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    session.execute(
                        text(f"DELETE FROM {tabla} WHERE thread_id = ANY(:hilos)"),
                        {"hilos": hilos},
                    )
                session.commit()


def _thread(user_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    return f"{user_id}:{conversation_id}"


def _chat(session: Session, mensaje: str, user_id: uuid.UUID, conversation_id=None):
    return ai_chat_service.chat(
        session,
        mensaje,
        conversation_id,
        user_id=user_id,
        as_of=AS_OF,
        draft_store=PostgresDraftStore(session=session),
        gateway=AIGateway(MockAIProvider()),
    )


# ---------- 5, 6, 7. El estado sobrevive al reinicio ----------


def test_una_accion_pendiente_sobrevive_al_reinicio(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    """Lo que rompe si esto falla: alguien pide registrar un gasto, Render reinicia y su
    aprobación queda colgada para siempre.
    """
    make_profile()
    primera = _chat(db_session, GASTO, TEST_USER_ID)
    limpiar_hilos.append(_thread(TEST_USER_ID, primera.conversation_id))
    assert primera.requires_approval is True

    # Reinicio: se cierra el pool y se olvida el grafo compilado.
    close_checkpointer()

    recuperada = ai_chat_service.get_conversation(primera.conversation_id, user_id=TEST_USER_ID)
    assert recuperada.messages, "la conversación no sobrevivió al reinicio"


def test_aprobar_despues_del_reinicio_ejecuta_la_accion_una_sola_vez(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    make_profile()
    primera = _chat(db_session, GASTO, TEST_USER_ID)
    limpiar_hilos.append(_thread(TEST_USER_ID, primera.conversation_id))
    action_id = primera.pending_action.action_id

    close_checkpointer()

    aprobada = ai_chat_service.resume(
        db_session,
        primera.conversation_id,
        action_id,
        user_id=TEST_USER_ID,
        approve=True,
        as_of=AS_OF,
        draft_store=PostgresDraftStore(session=db_session),
        gateway=AIGateway(MockAIProvider()),
    )

    assert aprobada.requires_approval is False
    assert "Registré" in aprobada.answer
    movimientos = db_session.execute(
        text("SELECT count(*) FROM transactions WHERE user_id = :u"), {"u": TEST_USER_ID}
    ).scalar_one()
    assert movimientos == 1


def test_aprobar_dos_veces_no_duplica_el_movimiento(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    """La pausa se consume al aprobar: el segundo intento no encuentra nada que reanudar."""
    make_profile()
    primera = _chat(db_session, GASTO, TEST_USER_ID)
    limpiar_hilos.append(_thread(TEST_USER_ID, primera.conversation_id))
    action_id = primera.pending_action.action_id

    def aprobar():
        return ai_chat_service.resume(
            db_session,
            primera.conversation_id,
            action_id,
            user_id=TEST_USER_ID,
            approve=True,
            as_of=AS_OF,
            draft_store=PostgresDraftStore(session=db_session),
            gateway=AIGateway(MockAIProvider()),
        )

    aprobar()
    close_checkpointer()
    with pytest.raises(ai_chat_service.PendingActionNotFoundError):
        aprobar()

    movimientos = db_session.execute(
        text("SELECT count(*) FROM transactions WHERE user_id = :u"), {"u": TEST_USER_ID}
    ).scalar_one()
    assert movimientos == 1


def test_rechazar_despues_del_reinicio_no_persiste_nada(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    make_profile()
    primera = _chat(db_session, GASTO, TEST_USER_ID)
    limpiar_hilos.append(_thread(TEST_USER_ID, primera.conversation_id))

    close_checkpointer()

    ai_chat_service.resume(
        db_session,
        primera.conversation_id,
        primera.pending_action.action_id,
        user_id=TEST_USER_ID,
        approve=False,
        as_of=AS_OF,
        draft_store=PostgresDraftStore(session=db_session),
        gateway=AIGateway(MockAIProvider()),
    )

    movimientos = db_session.execute(
        text("SELECT count(*) FROM transactions WHERE user_id = :u"), {"u": TEST_USER_ID}
    ).scalar_one()
    assert movimientos == 0


def test_una_conversacion_multiturno_continua_despues_del_reinicio(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    """El alta a medias de un compromiso se completa en el turno siguiente, tras reiniciar."""
    make_profile()
    primera = _chat(db_session, "Necesito pagar el alquiler el 5 de agosto", TEST_USER_ID)
    limpiar_hilos.append(_thread(TEST_USER_ID, primera.conversation_id))
    assert primera.requires_approval is False

    close_checkpointer()

    segunda = _chat(db_session, "Son 350 mil", TEST_USER_ID, primera.conversation_id)

    assert segunda.requires_approval is True
    assert segunda.pending_action.draft["amount"] == "350000"


# ---------- 11, 12, 13. Aislamiento entre cuentas ----------


def test_el_mismo_conversation_id_de_dos_usuarios_son_dos_hilos(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    """El `conversation_id` viaja por la URL: si fuera el hilo entero, conocerlo alcanzaría.

    Con el `user_id` adentro, el mismo identificador resuelve dos hilos distintos.
    """
    make_profile()
    compartido = uuid.uuid4()
    limpiar_hilos.extend([_thread(TEST_USER_ID, compartido), _thread(OTHER_USER_ID, compartido)])

    _chat(db_session, GASTO, TEST_USER_ID, compartido)

    de_b = ai_chat_service.get_conversation(compartido, user_id=OTHER_USER_ID)
    de_a = ai_chat_service.get_conversation(compartido, user_id=TEST_USER_ID)

    assert de_a.messages, "el dueño perdió su propia conversación"
    assert de_b.messages == [], "otra cuenta leyó la conversación ajena"


def test_el_usuario_b_no_puede_aprobar_la_accion_del_usuario_a(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    make_profile()
    primera = _chat(db_session, GASTO, TEST_USER_ID)
    limpiar_hilos.extend(
        [
            _thread(TEST_USER_ID, primera.conversation_id),
            _thread(OTHER_USER_ID, primera.conversation_id),
        ]
    )

    with pytest.raises(ai_chat_service.PendingActionNotFoundError):
        ai_chat_service.resume(
            db_session,
            primera.conversation_id,
            primera.pending_action.action_id,
            user_id=OTHER_USER_ID,
            approve=True,
            as_of=AS_OF,
            draft_store=PostgresDraftStore(session=db_session),
            gateway=AIGateway(MockAIProvider()),
        )

    # Y la acción del dueño sigue pendiente, sin haberse consumido.
    movimientos = db_session.execute(
        text("SELECT count(*) FROM transactions WHERE user_id = :u"), {"u": TEST_USER_ID}
    ).scalar_one()
    assert movimientos == 0


# ---------- 15, 16, 17. Esquema, idempotencia y concurrencia ----------


LANGGRAPH_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
)


@pytest.fixture
def base_limpia() -> Iterator[None]:
    """Borra las tablas del checkpointer para probar contra un esquema realmente nuevo.

    Sin esto, `setup()` no encuentra migraciones pendientes y no ejecuta NADA: un test de
    "se crean las tablas" o de "dos arranques concurrentes no rompen el esquema" pasaría sin
    haber ejercitado nunca el camino que dice probar.

    Es destructivo sobre el estado conversacional de la base de desarrollo, y está bien que
    lo sea: son datos de prueba y se regeneran solos. Nunca corre contra producción porque
    la suite se saltea entera sin PostgreSQL local.
    """
    with SessionLocal() as session:
        for tabla in LANGGRAPH_TABLES:
            session.execute(text(f"DROP TABLE IF EXISTS {tabla} CASCADE"))
        session.commit()
    yield
    # Deja el esquema en pie para los tests que vengan después.
    cp.reset_checkpointer()
    cp.get_checkpointer()
    cp.reset_checkpointer()


def test_las_tablas_de_langgraph_se_crean_en_una_base_limpia(
    postgres_checkpointer: None, base_limpia: None
) -> None:
    """Requisito 15, de verdad: partiendo de una base sin ninguna de las cuatro tablas."""
    with SessionLocal() as session:
        antes = [
            tabla
            for tabla in LANGGRAPH_TABLES
            if session.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{tabla}"}
            ).scalar_one()
            is not None
        ]
    assert antes == [], "la fixture no dejó la base limpia"

    cp.get_checkpointer()

    with SessionLocal() as session:
        faltantes = [
            tabla
            for tabla in LANGGRAPH_TABLES
            if session.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{tabla}"}
            ).scalar_one()
            is None
        ]

    assert faltantes == []


def test_en_una_base_limpia_las_tablas_nacen_con_rls(
    postgres_checkpointer: None, base_limpia: None
) -> None:
    """El caso que importa para una Supabase nueva: RLS desde el primer arranque."""
    cp.get_checkpointer()

    with SessionLocal() as session:
        sin_rls = [
            fila[0]
            for fila in session.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND NOT c.relrowsecurity "
                    "AND c.relname = ANY(:tablas)"
                ),
                {"tablas": list(LANGGRAPH_TABLES)},
            ).all()
        ]

    assert sin_rls == []


def test_setup_se_puede_correr_muchas_veces(postgres_checkpointer: None) -> None:
    """Idempotente: cada arranque de cada instancia lo ejecuta."""
    saver = cp.get_checkpointer()

    saver.setup()
    saver.setup()

    with SessionLocal() as session:
        versiones = session.execute(
            text("SELECT count(*), count(DISTINCT v) FROM checkpoint_migrations")
        ).one()
    # Ni filas duplicadas ni versiones repetidas.
    assert versiones[0] == versiones[1]


def test_dos_inicializaciones_concurrentes_no_rompen_el_esquema(
    postgres_checkpointer: None, base_limpia: None
) -> None:
    """El caso real: dos instancias de Render arrancando a la vez contra una base nueva.

    `checkpoint_migrations.v` es PRIMARY KEY y `setup()` lee-y-después-inserta, así que sin
    el advisory lock las dos leen la misma versión, aplican la misma migración y la segunda
    muere con clave duplicada.

    Va con `base_limpia` a propósito: con el esquema ya aplicado, `setup()` no encuentra
    migraciones pendientes y no ejecuta nada, así que el test pasaría sin haber ejercitado
    jamás la carrera que dice probar.
    """
    errores: list[Exception] = []
    barrera = threading.Barrier(2, timeout=30)

    def arrancar() -> None:
        propio = cp._Checkpointer()
        try:
            barrera.wait()
            propio.get()
        except Exception as error:  # noqa: BLE001 - se reporta al final
            errores.append(error)
        finally:
            propio.close()

    hilos = [threading.Thread(target=arrancar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)

    assert errores == [], f"la inicialización concurrente falló: {errores}"


# ---------- 18. RLS sobre las tablas del checkpointer ----------


def test_la_politica_del_checkpointer_filtra_por_el_usuario_del_thread_id(
    postgres_checkpointer: None,
) -> None:
    """La política compara el primer tramo del `thread_id` contra `auth.uid()`."""
    cp.get_checkpointer()

    with SessionLocal() as session:
        politica = session.execute(
            text(
                "SELECT qual FROM pg_policies WHERE schemaname='public' AND tablename='checkpoints'"
            )
        ).scalar_one_or_none()

    assert politica is not None, "checkpoints quedó sin política"
    assert "auth.uid()" in politica
    assert "split_part" in politica


# ---------- 19. Las conexiones se liberan al cerrar ----------


def test_cerrar_libera_el_pool(postgres_checkpointer: None) -> None:
    cp.get_checkpointer()
    pool = cp.active_pool()
    assert pool is not None

    close_checkpointer()

    assert pool.closed is True
    assert cp.active_pool() is None


def test_cerrar_dos_veces_no_rompe(postgres_checkpointer: None) -> None:
    cp.get_checkpointer()

    close_checkpointer()
    close_checkpointer()

    assert cp.active_pool() is None


def test_despues_de_cerrar_se_puede_volver_a_usar(postgres_checkpointer: None) -> None:
    """Es lo que hace que un reinicio simulado en tests no deje el copiloto inservible."""
    cp.get_checkpointer()
    close_checkpointer()

    assert cp.get_checkpointer() is not None
    assert cp.active_pool() is not None


def test_no_se_abre_un_pool_nuevo_por_cada_mensaje(
    postgres_checkpointer: None,
    db_session: Session,
    make_profile: Callable[..., dict],
    limpiar_hilos: list[str],
) -> None:
    """Un pool por proceso, reutilizado. Uno por mensaje agotaría las conexiones de Supabase."""
    make_profile()
    primera = _chat(db_session, "¿cuánto gasté este mes?", TEST_USER_ID)
    limpiar_hilos.append(_thread(TEST_USER_ID, primera.conversation_id))
    pool_inicial = cp.active_pool()

    for _ in range(3):
        _chat(db_session, "¿cuánto gasté este mes?", TEST_USER_ID, primera.conversation_id)

    assert cp.active_pool() is pool_inicial
    assert get_compiled_graph() is get_compiled_graph()
