"""Ningún código de producción conoce un usuario fijo.

Antes existía `app/core/constants.py` con `DEMO_USER_ID`, y aunque los endpoints ya
resolvían la identidad desde el JWT, la constante seguía viviendo en el paquete `core`: a
un paso de volver a colarse como valor por defecto de una función o como respaldo
silencioso de un flujo de IA.

Este test es la guardia que impide que vuelva. La regla es fácil de leer y de cumplir:

- El UUID demo existe solo dentro de `app/scripts/`, que son herramientas offline (el seed
  de demostración y la validación manual con el proveedor real). Nada de eso corre al
  atender una petición.
- El resto de `app/` —API, servicios, agente, tools, modelos, schemas, core— no menciona
  ese UUID ni por nombre ni por valor.

Si algún día hace falta un usuario de prueba en producción, este test va a fallar, que es
exactamente lo que tiene que pasar.
"""

from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Herramientas offline. No las importa ningún módulo que atienda peticiones.
ALLOWED_SUBTREES = (APP_DIR / "scripts",)

# El nombre y el valor: renombrar la constante no debería alcanzar para esquivar la regla.
FORBIDDEN = ("DEMO_USER_ID", "11111111-1111-4111-8111-111111111111")


def _production_modules() -> list[Path]:
    return [
        path
        for path in APP_DIR.rglob("*.py")
        if not any(path.is_relative_to(allowed) for allowed in ALLOWED_SUBTREES)
    ]


def test_hay_modulos_de_produccion_para_revisar() -> None:
    """Si el barrido no encuentra nada, el resto de los tests pasarían por vacío."""
    assert len(_production_modules()) > 20


def test_ningun_modulo_de_produccion_menciona_el_usuario_demo() -> None:
    ofensores = [
        f"{path.relative_to(APP_DIR)}: {token}"
        for path in _production_modules()
        for token in FORBIDDEN
        if token in path.read_text(encoding="utf-8")
    ]

    assert not ofensores, "referencias al usuario demo fuera de app/scripts: " + ", ".join(
        ofensores
    )


def test_el_modulo_de_constantes_con_el_usuario_demo_ya_no_existe() -> None:
    assert not (APP_DIR / "core" / "constants.py").exists()


def test_el_uuid_demo_solo_lo_define_el_seed() -> None:
    """Un único lugar donde se escribe el UUID: el script que crea ese perfil."""
    definiciones = [
        path.relative_to(APP_DIR).as_posix()
        for path in APP_DIR.rglob("*.py")
        if "DEMO_USER_ID = UUID(" in path.read_text(encoding="utf-8")
    ]

    assert definiciones == ["scripts/seed_demo.py"]
