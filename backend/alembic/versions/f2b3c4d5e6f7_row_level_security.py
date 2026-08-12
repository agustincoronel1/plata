"""Row Level Security sobre todas las tablas con datos de usuarios

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-12 10:30:00.000000

Segunda barrera de aislamiento, independiente del backend.

QUÉ PROBLEMA RESUELVE
---------------------
El aislamiento entre cuentas ya lo garantiza el repositorio: cada consulta filtra por el
`user_id` que sale del JWT verificado. Pero eso solo protege lo que pasa POR el backend. Si
la base es la de Supabase, PostgREST publica el schema `public` y cualquiera con la
publishable key —que viaja en el bundle del frontend, o sea que es pública— puede pedirle
las tablas directamente, sin pasar por FastAPI. Sin RLS, eso son los datos financieros de
todos los usuarios. Esta migración cierra esa puerta.

POR QUÉ NO SE USA `FORCE ROW LEVEL SECURITY`
--------------------------------------------
El backend se conecta con el rol dueño de las tablas, y el dueño no está sujeto a RLS salvo
que se fuerce. Eso es deliberado y es lo que hace que esto sea seguro de aplicar: forzar RLS
dejaría al backend sin ver una sola fila (nunca define `request.jwt.claims`) y rompería la
aplicación entera. La división queda explícita:

- Dentro del backend: aísla el repositorio, con `user_id` en cada consulta. Ya está y tiene
  tests.
- Fuera del backend (PostgREST, anon key, cualquier cliente directo): aísla RLS.

QUÉ PUEDE ESCRIBIR UN USUARIO DIRECTAMENTE
-------------------------------------------
No todas las tablas llevan las cuatro políticas, y la diferencia es intencional:

- Tablas de dominio (perfil, movimientos, compromisos, simulaciones): SELECT, INSERT,
  UPDATE y DELETE sobre lo propio, con `USING` y `WITH CHECK` en ambos sentidos.
- Tablas que administra el backend (`ai_drafts`, `ai_daily_usage`,
  `transaction_search_documents`): solo SELECT de lo propio. NO llevan políticas de
  escritura, y además se les revoca el privilegio. Una política de UPDATE sobre
  `ai_daily_usage` le permitiría a cualquiera poner `used = 0` y darse cuota infinita de IA;
  una sobre `ai_drafts` dejaría saltarse la confirmación atómica de un borrador. "Deny by
  default" acá es más seguro que una política, no menos.
- `rate_limit_counters`: RLS activo y ninguna política. Es infraestructura del backend y su
  sujeto está hasheado, así que no hay `auth.uid()` con el que comparar (ver más abajo).

PORTABILIDAD
------------
Las políticas se escriben una sola vez y corren igual en el PostgreSQL local y en Supabase:

- `auth.uid()` se crea SOLO si no existe. En Supabase ya existe y no se toca; en local se
  crea un equivalente con la misma definición que usa Supabase, que lee el `sub` de
  `request.jwt.claims`. Eso además hace las políticas testeables sin Supabase.
- Los roles `anon` y `authenticated` se crean solo si faltan (NOLOGIN: no son cuentas).

Idempotente: cada política se dropea antes de crearse, todo va con `IF EXISTS` / `IF NOT
EXISTS` y puede reaplicarse sin efecto. No borra ni modifica ningún dato.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tablas de dominio: la persona es dueña de sus filas y puede hacer las cuatro operaciones.
# El segundo elemento es la columna que dice de quién es la fila (en `user_profiles` el
# perfil ES el usuario, así que la columna es la clave primaria).
OWNED_TABLES: tuple[tuple[str, str], ...] = (
    ("user_profiles", "id"),
    ("transactions", "user_id"),
    ("commitments", "user_id"),
    ("purchase_simulations", "user_id"),
)

# Tablas que mantiene el backend. Lectura de lo propio; escritura, solo el backend.
BACKEND_MANAGED_TABLES: tuple[tuple[str, str], ...] = (
    ("ai_drafts", "user_id"),
    ("ai_daily_usage", "user_id"),
    ("transaction_search_documents", "user_id"),
)

# Infraestructura interna: RLS activo y sin ninguna política, o sea denegado para todo rol
# que no sea el dueño de la tabla.
INTERNAL_TABLES: tuple[str, ...] = ("rate_limit_counters",)

# Tablas del checkpointer de LangGraph. Las crea LangGraph en tiempo de ejecución, no
# Alembic, así que puede que todavía no existan cuando esto corra. Por eso se aseguran
# desde una función que se puede volver a llamar (ver `plata_secure_langgraph_tables`).
# El dueño se deduce del `thread_id`, que el backend arma como `<user_id>:<conversation_id>`
# (ver `ai_chat_service._thread_id`).
LANGGRAPH_THREAD_TABLES: tuple[str, ...] = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

# Sin columna de usuario ni thread_id: es la tabla de versiones interna de LangGraph. No
# contiene datos de nadie y queda denegada para todos salvo el dueño.
LANGGRAPH_INTERNAL_TABLES: tuple[str, ...] = ("checkpoint_migrations",)


def _ensure_roles() -> None:
    """Crea `anon` y `authenticated` si faltan. En Supabase ya existen y no se tocan.

    Son los roles a los que PostgREST cambia según haya o no sesión. Sin ellos, un
    `CREATE POLICY ... TO authenticated` falla, y las políticas no podrían escribirse igual
    en los dos entornos.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                CREATE ROLE anon NOLOGIN NOINHERIT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                CREATE ROLE authenticated NOLOGIN NOINHERIT;
            END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated")


def _ensure_auth_uid() -> None:
    """Garantiza que exista `auth.uid()`, sin pisar nunca la de Supabase.

    La definición local es la misma que usa Supabase: lee el claim `sub` del JWT que
    PostgREST deja en la configuración de la sesión. Que sea idéntica es lo que permite que
    la política sea el mismo texto en los dos lados, y que se pueda testear sin Supabase
    haciendo `SET LOCAL request.jwt.claims = '{"sub": "..."}'`.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'auth' AND p.proname = 'uid'
            ) THEN
                EXECUTE $fn$
                    CREATE FUNCTION auth.uid() RETURNS uuid
                    LANGUAGE sql STABLE
                    AS $body$
                        SELECT COALESCE(
                            NULLIF(current_setting('request.jwt.claim.sub', true), ''),
                            (NULLIF(current_setting('request.jwt.claims', true), '')::jsonb
                                ->> 'sub')
                        )::uuid
                    $body$;
                $fn$;
            END IF;
        END $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA auth TO anon, authenticated")


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def _drop_policies(table: str) -> None:
    """Borra las políticas de Plata sobre la tabla. Hace la migración reaplicable."""
    for operation in ("select", "insert", "update", "delete"):
        op.execute(f"DROP POLICY IF EXISTS plata_{table}_{operation} ON public.{table}")


def _owned_policies(table: str, owner_column: str) -> None:
    """Las cuatro políticas sobre las filas propias, con USING y WITH CHECK.

    `USING` decide qué filas ve la operación; `WITH CHECK` decide qué filas puede dejar
    escritas. En UPDATE van las dos: sin `WITH CHECK`, alguien podría tomar una fila suya y
    reasignarla a otro `user_id`, que es justamente la creación de datos a nombre ajeno.
    """
    op.execute(
        f"""
        CREATE POLICY plata_{table}_select ON public.{table}
            FOR SELECT TO authenticated
            USING ({owner_column} = auth.uid())
        """
    )
    op.execute(
        f"""
        CREATE POLICY plata_{table}_insert ON public.{table}
            FOR INSERT TO authenticated
            WITH CHECK ({owner_column} = auth.uid())
        """
    )
    op.execute(
        f"""
        CREATE POLICY plata_{table}_update ON public.{table}
            FOR UPDATE TO authenticated
            USING ({owner_column} = auth.uid())
            WITH CHECK ({owner_column} = auth.uid())
        """
    )
    op.execute(
        f"""
        CREATE POLICY plata_{table}_delete ON public.{table}
            FOR DELETE TO authenticated
            USING ({owner_column} = auth.uid())
        """
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO authenticated"
    )
    # `anon` es "sin sesión": no tiene nada que hacer con datos financieros de nadie.
    op.execute(f"REVOKE ALL ON public.{table} FROM anon")


def _read_only_policies(table: str, owner_column: str) -> None:
    """Solo lectura de lo propio. La escritura queda para el backend.

    Sin política de INSERT/UPDATE/DELETE, RLS deniega esas operaciones por defecto. Se revoca
    además el privilegio para que ni siquiera dependa de la ausencia de una política.
    """
    op.execute(
        f"""
        CREATE POLICY plata_{table}_select ON public.{table}
            FOR SELECT TO authenticated
            USING ({owner_column} = auth.uid())
        """
    )
    op.execute(f"GRANT SELECT ON public.{table} TO authenticated")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON public.{table} FROM authenticated")
    op.execute(f"REVOKE ALL ON public.{table} FROM anon")


def _internal_table(table: str) -> None:
    """RLS activo y ninguna política: denegado para todo rol que no sea el dueño."""
    op.execute(f"REVOKE ALL ON public.{table} FROM anon, authenticated")


def _create_langgraph_function() -> None:
    """Deja en la base una función idempotente que asegura las tablas del checkpointer.

    Existe como función y no como SQL suelto porque esas tablas las crea LangGraph la
    primera vez que se usa el copiloto, que puede ser DESPUÉS de esta migración. El backend
    la llama justo después de `saver.setup()` (ver `app.ai.agent.graph`), así que quedan
    protegidas apenas aparecen, y se puede volver a ejecutar a mano en cualquier momento:

        SELECT public.plata_secure_langgraph_tables();

    El dueño del checkpoint sale del `thread_id`, que el backend arma como
    `<user_id>:<conversation_id>`. No hay columna `user_id` que filtrar, así que la política
    compara el primer tramo del identificador. Es lectura y nada más: el estado
    conversacional lo escribe solo el backend.
    """
    thread_tables = ", ".join(f"'{name}'" for name in LANGGRAPH_THREAD_TABLES)
    internal_tables = ", ".join(f"'{name}'" for name in LANGGRAPH_INTERNAL_TABLES)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.plata_secure_langgraph_tables()
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target text;
        BEGIN
            FOREACH target IN ARRAY ARRAY[{thread_tables}]
            LOOP
                IF to_regclass('public.' || target) IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
                    EXECUTE format(
                        'DROP POLICY IF EXISTS plata_%s_select ON public.%I', target, target);
                    -- El separador va como %L (literal escapado por format) en lugar de
                    -- escribirse a mano: anidar comillas dentro de un literal de plpgsql
                    -- que a su vez vive en un bloque dollar-quoted es justo donde se
                    -- cuelan los errores de sintaxis.
                    EXECUTE format(
                        'CREATE POLICY plata_%s_select ON public.%I '
                        'FOR SELECT TO authenticated '
                        'USING (split_part(thread_id, %L, 1) = auth.uid()::text)',
                        target, target, ':');
                    EXECUTE format('GRANT SELECT ON public.%I TO authenticated', target);
                    EXECUTE format(
                        'REVOKE INSERT, UPDATE, DELETE ON public.%I FROM authenticated',
                        target);
                    EXECUTE format('REVOKE ALL ON public.%I FROM anon', target);
                END IF;
            END LOOP;

            FOREACH target IN ARRAY ARRAY[{internal_tables}]
            LOOP
                IF to_regclass('public.' || target) IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
                    EXECUTE format(
                        'REVOKE ALL ON public.%I FROM anon, authenticated', target);
                END IF;
            END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    _ensure_roles()
    _ensure_auth_uid()

    for table, owner_column in OWNED_TABLES:
        _enable_rls(table)
        _drop_policies(table)
        _owned_policies(table, owner_column)

    for table, owner_column in BACKEND_MANAGED_TABLES:
        _enable_rls(table)
        _drop_policies(table)
        _read_only_policies(table, owner_column)

    for table in INTERNAL_TABLES:
        _enable_rls(table)
        _drop_policies(table)
        _internal_table(table)

    _create_langgraph_function()
    op.execute("SELECT public.plata_secure_langgraph_tables()")


def downgrade() -> None:
    """Saca las políticas y apaga RLS. No borra datos ni toca los roles.

    Los roles `anon` y `authenticated` no se eliminan: en Supabase son del proyecto y
    borrarlos rompería PostgREST. `auth.uid()` tampoco, por lo mismo.
    """
    op.execute("DROP FUNCTION IF EXISTS public.plata_secure_langgraph_tables()")

    for table in LANGGRAPH_THREAD_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('public.{table}') IS NOT NULL THEN
                    DROP POLICY IF EXISTS plata_{table}_select ON public.{table};
                    ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;
                END IF;
            END $$;
            """
        )

    for table in LANGGRAPH_INTERNAL_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('public.{table}') IS NOT NULL THEN
                    ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;
                END IF;
            END $$;
            """
        )

    for table in INTERNAL_TABLES:
        _drop_policies(table)
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")

    for table, _ in BACKEND_MANAGED_TABLES + OWNED_TABLES:
        _drop_policies(table)
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
