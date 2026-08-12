"""Contrato de seguridad de la superficie HTTP, verificado por barrido.

Los tests de aislamiento comprueban endpoints concretos, uno por uno. El problema de eso es
que un endpoint NUEVO no aparece en ninguna lista: se agrega, nadie se acuerda de sumarlo a
los tests, y queda público sin que falle nada.

Acá se recorre la superficie que la aplicación publica de verdad (el esquema OpenAPI, que es
lo que FastAPI arma a partir de las rutas registradas) y se exige que cada operación cumpla
el contrato. Si alguien agrega un endpoint sin sesión, este test falla solo.

Se usa el esquema y no `app.routes` a propósito: la forma interna de guardar las rutas
cambió entre versiones de FastAPI (ahora vienen anidadas en routers incluidos), y el esquema
es la vista estable y además es exactamente lo que ve alguien de afuera.

Lo público está enumerado y es corto: todo lo demás es privado por defecto.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

# Rutas deliberadamente públicas, con el motivo de cada una.
#
# - "/" y "/health": los mira Render para saber si el servicio está vivo, y el frontend para
#   detectar el arranque en frío. No tocan PostgreSQL ni devuelven datos de nadie.
# - "/health/db": comprueba que la base responda. Devuelve "connected" o 503, nada más.
PUBLIC_PATHS = {"/", "/health", "/health/db"}

# Placeholders para armar una URL concreta a partir de una con parámetros.
PATH_PARAM_VALUES = {
    "transaction_id": "99999999-9999-4999-8999-999999999999",
    "commitment_id": "99999999-9999-4999-8999-999999999999",
    "draft_id": "99999999-9999-4999-8999-999999999999",
    "conversation_id": "99999999-9999-4999-8999-999999999999",
}

# Nombres con los que un endpoint estaría tomando la identidad del cliente en lugar del
# token. Ninguno puede aparecer como parámetro de query, path o header.
FORBIDDEN_PARAM_NAMES = {"user_id", "userid", "uid", "owner_id", "account_id"}


def _operations() -> list[tuple[str, str, list[dict]]]:
    """(método, path, parámetros) de cada operación publicada."""
    schema = app.openapi()
    operations: list[tuple[str, str, list[dict]]] = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method.upper() in {"OPTIONS", "HEAD"}:
                continue
            operations.append((method.upper(), path, operation.get("parameters", [])))
    return operations


def _concrete_path(path: str) -> str:
    for name, value in PATH_PARAM_VALUES.items():
        path = path.replace(f"{{{name}}}", value)
    return path


def test_el_barrido_encuentra_operaciones() -> None:
    """Si la introspección devolviera vacío, todo lo de abajo pasaría por no hacer nada."""
    assert len(_operations()) > 15


def test_toda_ruta_privada_exige_sesion() -> None:
    """Sin token, todo lo que no esté en `PUBLIC_PATHS` responde 401.

    Es la red que atrapa un endpoint nuevo que se olvidaron de proteger: no hace falta
    acordarse de agregarlo a ninguna lista, aparece solo.
    """
    # Sin overrides: acá se quiere la dependencia real de autenticación.
    app.dependency_overrides.clear()
    # El rate limiting apagado para que un 429 no se confunda con un 401 en el barrido.
    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False

    try:
        client = TestClient(app)
        for method, path, _ in _operations():
            if path in PUBLIC_PATHS:
                continue

            response = client.request(method, _concrete_path(path), json={})

            assert response.status_code == 401, (
                f"{method} {path} respondió {response.status_code} sin token: "
                "o le falta la dependencia de sesión, o es público y hay que declararlo "
                "en PUBLIC_PATHS con su motivo."
            )
    finally:
        settings.rate_limit_enabled = original


def test_las_rutas_publicas_declaradas_existen_y_son_publicas() -> None:
    """La lista de excepciones no puede mentir: ni sobrar ni tapar algo que pide sesión."""
    app.dependency_overrides.clear()
    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False

    try:
        client = TestClient(app)
        publicadas = {path for _, path, _ in _operations()}
        assert PUBLIC_PATHS <= publicadas, "PUBLIC_PATHS nombra rutas que ya no existen"

        for path in PUBLIC_PATHS:
            # 503 es válido en /health/db con la base caída; lo que no puede pasar es 401.
            assert client.get(path).status_code != 401
    finally:
        settings.rate_limit_enabled = original


def test_ninguna_ruta_acepta_el_usuario_como_parametro() -> None:
    """La identidad sale del token y de ningún otro lado.

    Un endpoint que declarara `user_id` en la query, el path o un header lo estaría tomando
    del cliente, que es exactamente lo que no puede pasar.
    """
    ofensores = [
        f"{method} {path}: {parameter['name']} (in={parameter['in']})"
        for method, path, parameters in _operations()
        for parameter in parameters
        if parameter["name"].lower().replace("-", "_") in FORBIDDEN_PARAM_NAMES
    ]

    assert not ofensores, f"endpoints que aceptan el usuario desde el cliente: {ofensores}"
