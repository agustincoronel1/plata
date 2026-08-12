"""La marca visible es Vector, y los identificadores persistidos siguen siendo los de antes.

El rebranding tiene dos mitades que tiran para lados opuestos, y este archivo fija las dos:

- Lo que la persona LEE tiene que decir Vector. Un mensaje de error, el título de la API o
  un prompt donde el asistente se presenta con el nombre viejo delatan el rebranding a
  medias.
- Lo que está PERSISTIDO tiene que seguir llamándose como se llama. La función SQL
  `plata_secure_langgraph_tables()`, las políticas RLS y las migraciones aplicadas viven en
  PostgreSQL: renombrarlas sin una migración de compatibilidad rompe producción. El usuario
  final no las ve, así que no hay nada que ganar.

La palabra "plata" en minúscula, cuando significa dinero, es lenguaje natural rioplatense y
se conserva a propósito: convertir "en qué se fue tu plata" en "tu Vector" sería peor que no
haber hecho el rebranding.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings, settings
from app.main import app

BACKEND = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"
MIGRATIONS_DIR = BACKEND / "alembic" / "versions"

BRAND = "Vector"
OLD_BRAND = re.compile(r"\bPlata\b|\bPLATA\b")

# Identificadores persistidos en PostgreSQL. Renombrarlos exige una migración con período
# de compatibilidad; hoy son deuda técnica documentada en el README.
#
# Los nombres de las políticas RLS se arman con f-strings en la migración
# (`plata_{table}_{operation}`), así que lo que se busca es el prefijo, no el nombre final.
PERSISTED_IDENTIFIERS = (
    "plata_secure_langgraph_tables",
    "plata_{table}_{operation}",
    "plata_{table}_select",
)


# ---------- 1, 3, 4. La marca visible es Vector ----------


def test_el_nombre_por_defecto_de_la_api_es_vector() -> None:
    """El valor en código, que es el único determinístico."""
    assert Settings.model_fields["app_name"].default == "Vector API"


def test_el_nombre_configurado_no_conserva_la_marca_vieja() -> None:
    """`APP_NAME` se puede sobreescribir por entorno, y ahí es donde queda vieja.

    Este test encontró exactamente eso: el `.env` local seguía diciendo "Plata API" y tapaba
    el default del código. Lo mismo puede pasar en Render, donde la variable está definida a
    mano y ningún test la mira.
    """
    assert not OLD_BRAND.search(settings.app_name), (
        f"APP_NAME está configurada como {settings.app_name!r}: actualizá la variable de "
        "entorno (local y en Render)."
    )


def test_la_descripcion_de_openapi_es_la_de_vector() -> None:
    assert settings.app_description == "API del copiloto financiero Vector."


def test_el_esquema_openapi_publica_la_marca_nueva() -> None:
    """Es lo que se ve en /docs, o sea la cara pública de la API."""
    schema = app.openapi()

    assert schema["info"]["title"] == "Vector API"
    assert BRAND in schema["info"]["description"]
    assert not OLD_BRAND.search(schema["info"]["title"])
    assert not OLD_BRAND.search(schema["info"]["description"])


def test_el_healthcheck_se_identifica_como_vector() -> None:
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        cuerpo = client.get("/health").json()

    assert cuerpo["service"] == "vector-api"


# ---------- 5. Ningún mensaje público presenta la marca vieja ----------


def _modulos_de_produccion() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_ningun_modulo_de_produccion_nombra_la_marca_vieja() -> None:
    """Barrido: si alguien reintroduce "Plata" como producto, esto falla solo.

    La única excepción permitida es el uso coloquial —"plata" como dinero— que en mayúscula
    solo puede aparecer al empezar una oración. Se lista explícitamente para que agregar una
    excepción nueva sea una decisión consciente y no un descuido.
    """
    excepciones = {
        # "Plata que ya tiene dueño: alquiler, cuota, servicio, suscripción."
        # Va en mayúscula porque abre la oración; significa DINERO, no el producto.
        "models/commitment.py",
    }

    ofensores = [
        f"{p.relative_to(APP_DIR).as_posix()}:{n}"
        for p in _modulos_de_produccion()
        if p.relative_to(APP_DIR).as_posix() not in excepciones
        for n, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if OLD_BRAND.search(linea)
    ]

    assert not ofensores, f"la marca vieja sigue en producción: {ofensores}"


def test_los_prompts_presentan_al_asistente_como_vector() -> None:
    """El copiloto se identifica por la marca cuando habla de sí mismo."""
    brain = (APP_DIR / "ai" / "agent" / "brain.py").read_text(encoding="utf-8")

    assert "copiloto financiero Vector" in brain
    assert "copiloto financiero Plata" not in brain


def test_el_prompt_del_parser_nombra_a_vector() -> None:
    prompt = (APP_DIR / "ai" / "prompts" / "transaction_parser_v1.md").read_text(encoding="utf-8")

    assert BRAND in prompt
    assert not OLD_BRAND.search(prompt)


def test_los_mensajes_de_cuota_y_error_nombran_a_vector() -> None:
    from app.ai.agent.checkpointer import CheckpointerUnavailableError
    from app.ai.exceptions import daily_limit_message

    limite = daily_limit_message(10)
    copiloto = CheckpointerUnavailableError().detail

    for mensaje in (limite, copiloto):
        assert not OLD_BRAND.search(mensaje), mensaje
    assert BRAND in limite
    assert BRAND in copiloto


# ---------- 2. El uso coloquial de "plata" se conserva ----------


def test_se_conserva_plata_como_dinero_en_lenguaje_natural() -> None:
    """Un reemplazo global habría escrito "Vector que ya tiene dueño", que no significa nada."""
    commitment = (APP_DIR / "models" / "commitment.py").read_text(encoding="utf-8")

    assert "Plata que ya tiene dueño" in commitment


def test_el_fast_path_sigue_hablando_de_plata_como_dinero() -> None:
    """Las respuestas del copiloto son rioplatenses: ahí "plata" es dinero y queda."""
    fast_path = (APP_DIR / "services" / "fast_path_service.py").read_text(encoding="utf-8")

    assert "plata" in fast_path.lower()


# ---------- 7, 8. Los identificadores persistidos no cambiaron ----------


@pytest.mark.parametrize("identificador", PERSISTED_IDENTIFIERS)
def test_los_identificadores_persistidos_siguen_existiendo(identificador: str) -> None:
    """Están en migraciones ya aplicadas: renombrarlos rompe la base desplegada."""
    encontrado = any(
        identificador in p.read_text(encoding="utf-8") for p in MIGRATIONS_DIR.glob("*.py")
    )

    assert encontrado, f"{identificador} desapareció de las migraciones"


def test_el_checkpointer_sigue_llamando_a_la_funcion_de_rls() -> None:
    """Si se renombrara la función sin migración, esta llamada quedaría rota en silencio."""
    checkpointer = (APP_DIR / "ai" / "agent" / "checkpointer.py").read_text(encoding="utf-8")

    assert "plata_secure_langgraph_tables" in checkpointer


def test_la_base_de_datos_por_defecto_no_se_renombro() -> None:
    """Cambiar el nombre de la base o del rol dejaría al backend sin poder conectarse."""
    assert "plata" in Settings.model_fields["database_url"].default


# ---------- 11. Las migraciones históricas no se tocaron ----------


def test_ninguna_migracion_menciona_la_marca_nueva() -> None:
    """Las migraciones aplicadas son historia: el rebranding no las reescribe.

    Si aparece "Vector" en una, es que alguien editó una migración ya aplicada, y eso deja
    el archivo distinto de lo que corrió en producción.

    Se excluye `pgvector`: su tipo de columna se llama `Vector` y no tiene nada que ver con
    la marca. Sin esta excepción, la migración del RAG daría un falso positivo para siempre.
    """
    ofensores = []
    for p in MIGRATIONS_DIR.glob("*.py"):
        for linea in p.read_text(encoding="utf-8").splitlines():
            if "pgvector" in linea or re.search(r"\bVector\(", linea):
                continue
            if re.search(rf"\b{BRAND}\b", linea):
                ofensores.append(f"{p.name}: {linea.strip()}")

    assert not ofensores, f"se editaron migraciones aplicadas: {ofensores}"
