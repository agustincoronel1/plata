"""Row Level Security: el aislamiento también se sostiene sin pasar por el backend.

Estos tests no usan la API. Hablan con PostgreSQL directamente haciendo `SET ROLE
authenticated` y definiendo `request.jwt.claims`, que es exactamente lo que hace PostgREST
cuando alguien le pega a la base de Supabase con la publishable key. O sea: simulan al
atacante que se saltea FastAPI.

Sin RLS, ese camino devuelve los datos de todo el mundo. Lo que se comprueba acá es que
devuelve solo los propios, que las escrituras cruzadas no entran, y que las tablas que
administra el backend no se pueden escribir desde afuera (si `ai_daily_usage` fuera
escribible, cualquiera se daría cuota infinita de IA poniendo `used = 0`).

El backend NO está sujeto a estas políticas y eso es deliberado: se conecta con el rol dueño
de las tablas, que no está alcanzado por RLS mientras no se fuerce. Forzarlo lo dejaría sin
ver una sola fila. El aislamiento del backend es el del repositorio y tiene sus propios
tests (test_multiuser_isolation.py). Estas políticas son la segunda barrera, la que protege
el acceso directo.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.database import engine
from tests.conftest import requires_postgres

pytestmark = requires_postgres

USER_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

# Tablas de dominio: la persona puede leer y escribir lo suyo.
OWNED_TABLES = ("user_profiles", "transactions", "commitments", "purchase_simulations")

# Tablas que mantiene el backend: lectura de lo propio, escritura de nadie.
BACKEND_MANAGED_TABLES = ("ai_drafts", "ai_daily_usage", "transaction_search_documents")


@pytest.fixture
def rls_session() -> Iterator[Session]:
    """Sesión con datos de dos usuarios, revertida al terminar.

    Los datos se siembran como dueño de las tablas (sin RLS de por medio) y recién después
    se baja a `authenticated`, que es el rol que se quiere probar.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    for user_id, name, balance in ((USER_A, "A", 100), (USER_B, "B", 200)):
        session.execute(
            text(
                "INSERT INTO user_profiles "
                "(id, name, currency, current_balance, next_income_amount, "
                " protected_amount, safety_buffer) "
                "VALUES (:id, :name, 'ARS', :balance, 0, 0, 0)"
            ),
            {"id": user_id, "name": name, "balance": balance},
        )
        session.execute(
            text(
                "INSERT INTO transactions "
                "(id, user_id, type, amount, currency, category, occurred_on) "
                "VALUES (:id, :user_id, 'expense', 10, 'ARS', 'comida', current_date)"
            ),
            {"id": uuid.uuid4(), "user_id": user_id},
        )
        session.execute(
            text(
                "INSERT INTO ai_daily_usage (user_id, usage_day, kind, used) "
                "VALUES (:user_id, current_date, 'ai_query', 5)"
            ),
            {"user_id": user_id},
        )
    session.flush()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _become(session: Session, user_id: uuid.UUID) -> None:
    """Pasa a ser `authenticated` con el `sub` de ese usuario, como haría PostgREST."""
    session.execute(text("SET LOCAL ROLE authenticated"))
    session.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": json.dumps({"sub": str(user_id)})},
    )


def _become_owner(session: Session) -> None:
    """Vuelve al rol dueño para comprobar el estado real, sin el filtro de RLS."""
    session.execute(text("RESET ROLE"))


# ---------- La política existe y está bien formada ----------


def test_rls_activo_en_todas_las_tablas_con_datos_de_usuarios(rls_session: Session) -> None:
    rows = rls_session.execute(
        text(
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
        )
    ).scalars()
    con_rls = set(rows)

    for table in OWNED_TABLES + BACKEND_MANAGED_TABLES + ("rate_limit_counters",):
        assert table in con_rls, f"{table} quedó sin RLS"


def test_las_politicas_de_dominio_tienen_using_y_with_check(rls_session: Session) -> None:
    """USING decide qué filas se ven; WITH CHECK, qué filas se pueden dejar escritas.

    Sin WITH CHECK en UPDATE, alguien podría tomar una fila propia y reasignarla a otro
    `user_id`: crear datos a nombre ajeno sin haber leído nada ajeno.
    """
    rows = rls_session.execute(
        text(
            "SELECT tablename, cmd, qual IS NOT NULL, with_check IS NOT NULL "
            "FROM pg_policies WHERE schemaname = 'public'"
        )
    ).all()
    policies = {(table, cmd): (has_using, has_check) for table, cmd, has_using, has_check in rows}

    for table in OWNED_TABLES:
        assert policies[(table, "SELECT")] == (True, False)
        assert policies[(table, "INSERT")] == (False, True)
        # La única con las dos mitades: filtra lo que toca y valida lo que deja.
        assert policies[(table, "UPDATE")] == (True, True)
        assert policies[(table, "DELETE")] == (True, False)


def test_las_tablas_del_backend_no_tienen_politicas_de_escritura(rls_session: Session) -> None:
    """Solo SELECT. Sin política de escritura, RLS deniega por defecto.

    Es más seguro que tener una política: una de UPDATE sobre `ai_daily_usage` dejaría poner
    `used = 0` y darse cuota infinita de IA.
    """
    for table in BACKEND_MANAGED_TABLES:
        cmds = set(
            rls_session.execute(
                text("SELECT cmd FROM pg_policies WHERE schemaname='public' AND tablename=:t"),
                {"t": table},
            ).scalars()
        )
        assert cmds == {"SELECT"}, f"{table} tiene políticas de escritura: {cmds}"


# ---------- 1. A no puede LEER datos de B ----------


def test_a_solo_ve_su_perfil(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    nombres = list(rls_session.execute(text("SELECT name FROM user_profiles")).scalars())

    assert nombres == ["A"]


def test_a_solo_ve_sus_movimientos(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    duenos = set(rls_session.execute(text("SELECT user_id FROM transactions")).scalars())

    assert duenos == {USER_A}


def test_a_no_ve_la_cuota_de_ia_de_b(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    duenos = set(rls_session.execute(text("SELECT user_id FROM ai_daily_usage")).scalars())

    assert duenos == {USER_A}


def test_sin_sesion_no_se_ve_absolutamente_nada(rls_session: Session) -> None:
    """`authenticated` sin claim `sub`: `auth.uid()` es NULL y ninguna fila matchea."""
    rls_session.execute(text("SET LOCAL ROLE authenticated"))
    rls_session.execute(text("SELECT set_config('request.jwt.claims', '', true)"))

    total = rls_session.execute(text("SELECT count(*) FROM user_profiles")).scalar_one()

    assert total == 0


# ---------- 2. A no puede EDITAR datos de B ----------


def test_a_no_puede_editar_el_perfil_de_b(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    resultado = rls_session.execute(
        text("UPDATE user_profiles SET name = 'ROBADO' WHERE id = :id"), {"id": USER_B}
    )

    assert resultado.rowcount == 0
    _become_owner(rls_session)
    nombre = rls_session.execute(
        text("SELECT name FROM user_profiles WHERE id = :id"), {"id": USER_B}
    ).scalar_one()
    assert nombre == "B"


def test_a_no_puede_reasignarse_un_movimiento_de_b(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    resultado = rls_session.execute(
        text("UPDATE transactions SET amount = 99999 WHERE user_id = :id"), {"id": USER_B}
    )

    assert resultado.rowcount == 0


def test_a_no_puede_regalarle_un_movimiento_propio_a_b(rls_session: Session) -> None:
    """El WITH CHECK del UPDATE: no se puede sacar una fila propia del alcance propio."""
    _become(rls_session, USER_A)

    with pytest.raises(ProgrammingError):
        rls_session.execute(
            text("UPDATE transactions SET user_id = :b WHERE user_id = :a"),
            {"a": USER_A, "b": USER_B},
        )


def test_a_no_puede_resetear_su_cuota_de_ia(rls_session: Session) -> None:
    """La razón por la que `ai_daily_usage` no lleva política de escritura."""
    _become(rls_session, USER_A)

    with pytest.raises(ProgrammingError):
        rls_session.execute(
            text("UPDATE ai_daily_usage SET used = 0 WHERE user_id = :id"), {"id": USER_A}
        )


# ---------- 3. A no puede BORRAR datos de B ----------


def test_a_no_puede_borrar_movimientos_de_b(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    resultado = rls_session.execute(
        text("DELETE FROM transactions WHERE user_id = :id"), {"id": USER_B}
    )

    assert resultado.rowcount == 0
    _become_owner(rls_session)
    quedan = rls_session.execute(
        text("SELECT count(*) FROM transactions WHERE user_id = :id"), {"id": USER_B}
    ).scalar_one()
    assert quedan == 1


def test_a_no_puede_borrar_el_perfil_de_b(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    resultado = rls_session.execute(
        text("DELETE FROM user_profiles WHERE id = :id"), {"id": USER_B}
    )

    assert resultado.rowcount == 0


# ---------- 4. A no puede CREAR datos a nombre de B ----------


def test_a_no_puede_crear_un_movimiento_a_nombre_de_b(rls_session: Session) -> None:
    """El WITH CHECK del INSERT. Es la política que impide fabricar datos ajenos."""
    _become(rls_session, USER_A)

    with pytest.raises(ProgrammingError):
        rls_session.execute(
            text(
                "INSERT INTO transactions "
                "(id, user_id, type, amount, currency, category, occurred_on) "
                "VALUES (:id, :user_id, 'expense', 1, 'ARS', 'comida', current_date)"
            ),
            {"id": uuid.uuid4(), "user_id": USER_B},
        )


def test_a_no_puede_crear_un_compromiso_a_nombre_de_b(rls_session: Session) -> None:
    _become(rls_session, USER_A)

    with pytest.raises(ProgrammingError):
        rls_session.execute(
            text(
                "INSERT INTO commitments (id, user_id, name, amount, due_date, category, status) "
                "VALUES (:id, :user_id, 'Luz', 100, current_date, 'servicios', 'pending')"
            ),
            {"id": uuid.uuid4(), "user_id": USER_B},
        )


def test_a_si_puede_crear_un_movimiento_propio(rls_session: Session) -> None:
    """El contraste que hace válido a todo lo anterior: la política no bloquea lo legítimo."""
    _become(rls_session, USER_A)

    rls_session.execute(
        text(
            "INSERT INTO transactions "
            "(id, user_id, type, amount, currency, category, occurred_on) "
            "VALUES (:id, :user_id, 'expense', 1, 'ARS', 'comida', current_date)"
        ),
        {"id": uuid.uuid4(), "user_id": USER_A},
    )

    total = rls_session.execute(text("SELECT count(*) FROM transactions")).scalar_one()
    assert total == 2


# ---------- Tablas internas ----------


def test_los_contadores_de_rate_limit_no_son_accesibles(rls_session: Session) -> None:
    """Infraestructura del backend: nadie de afuera la lee ni la escribe.

    Si se pudieran borrar filas, se borraría el propio límite.
    """
    _become(rls_session, USER_A)

    with pytest.raises(ProgrammingError):
        rls_session.execute(text("SELECT count(*) FROM rate_limit_counters"))
