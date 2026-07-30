"""CORS: qué orígenes del navegador pueden llamar a la API.

Estos tests existen por una regresión concreta. Mientras el frontend mandaba solo `Accept`,
las peticiones eran "simples" y el navegador no hacía preflight, así que un origen mal
configurado no se notaba. Al agregar `Authorization` con el JWT de Supabase, cada llamada
empieza con un `OPTIONS`, y ahí un origen de más devolvía `400 Disallowed CORS origin`: la
aplicación mostraba "No pudimos conectar con el servidor" con el backend perfectamente vivo.

No usan PostgreSQL: el middleware responde el preflight antes de llegar a la ruta.
"""

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app

PROFILE = "/api/v1/profile"

# Los orígenes locales de Vite. `localhost` y `127.0.0.1` son orígenes distintos para el
# navegador, y Vite salta al puerto siguiente cuando el 5173 está ocupado.
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]


def preflight(client: TestClient, origin: str, *, method: str = "GET", path: str = PROFILE):
    """Simula el preflight que manda el navegador antes de una petición con Authorization."""
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_preflight_con_authorization_desde_localhost_5173_es_aceptado() -> None:
    """El caso que fallaba: OPTIONS con Authorization desde el frontend de desarrollo."""
    client = TestClient(app)
    response = preflight(client, "http://localhost:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "content-type" in allowed


def test_preflight_aceptado_desde_todos_los_origenes_locales_de_vite() -> None:
    client = TestClient(app)

    for origin in DEV_ORIGINS:
        response = preflight(client, origin)
        assert response.status_code == 200, origin
        assert response.headers["access-control-allow-origin"] == origin


def test_preflight_aceptado_en_todos_los_endpoints_del_dashboard() -> None:
    """Las cinco rutas que carga el dashboard al abrir, con sus métodos reales."""
    rutas = [
        (PROFILE, "GET"),
        (PROFILE, "PUT"),
        ("/api/v1/transactions", "POST"),
        ("/api/v1/transactions/1", "PATCH"),
        ("/api/v1/transactions/1", "DELETE"),
        ("/api/v1/commitments", "GET"),
        ("/api/v1/dashboard/summary", "GET"),
        ("/api/v1/simulations", "GET"),
        ("/api/v1/auth/me", "GET"),
    ]

    client = TestClient(app)

    for path, method in rutas:
        response = preflight(client, "http://localhost:5173", method=method, path=path)
        assert response.status_code == 200, f"{method} {path}"


def test_el_preflight_habilita_los_metodos_que_usa_el_frontend() -> None:
    client = TestClient(app)
    response = preflight(client, "http://localhost:5173")

    cabecera = response.headers["access-control-allow-methods"]
    permitidos = {metodo.strip().upper() for metodo in cabecera.split(",")}
    assert {"OPTIONS", "GET", "POST", "PUT", "PATCH", "DELETE"} <= permitidos


def test_un_origen_ajeno_sigue_rechazado() -> None:
    """Ampliar los orígenes de desarrollo no abre la API a cualquiera."""
    client = TestClient(app)
    response = preflight(client, "https://sitio-ajeno.example")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_no_se_combina_comodin_con_credenciales() -> None:
    """La sesión viaja en Authorization, no en cookies: no hacen falta credenciales."""
    client = TestClient(app)
    response = preflight(client, "http://localhost:5173")

    assert response.headers["access-control-allow-origin"] != "*"
    assert "access-control-allow-credentials" not in response.headers
    assert "*" not in settings.cors_allowed_origins


def test_una_respuesta_401_tambien_llega_con_cabeceras_cors() -> None:
    """Sin cabeceras CORS, el navegador convierte el 401 en un error de red opaco.

    Es lo que hace que un problema de sesión se vea como "no pudimos conectar".
    """
    client = TestClient(app)
    response = client.get(
        "/api/v1/auth/me",
        headers={"Origin": "http://localhost:5173", "Authorization": "Bearer no.es.valido"},
    )

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_health_sigue_accesible_desde_el_frontend() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------- La lista de orígenes ----------


def test_en_desarrollo_se_incluyen_los_origenes_locales() -> None:
    origins = Settings(
        environment="development", frontend_url="http://localhost:5173"
    ).cors_allowed_origins

    for origin in DEV_ORIGINS:
        assert origin in origins
    assert "*" not in origins


def test_en_produccion_solo_queda_el_frontend_configurado() -> None:
    """Los orígenes locales son una comodidad de desarrollo, no del despliegue real."""
    origins = Settings(
        environment="production", frontend_url="https://plata.example"
    ).cors_allowed_origins

    assert origins == ["https://plata.example"]


def test_se_pueden_declarar_origenes_extra_explicitos() -> None:
    origins = Settings(
        environment="production",
        frontend_url="https://plata.example",
        cors_extra_origins="https://preview.plata.example, https://otro.example",
    ).cors_allowed_origins

    assert origins == [
        "https://plata.example",
        "https://preview.plata.example",
        "https://otro.example",
    ]


def test_la_lista_no_repite_origenes() -> None:
    origins = Settings(
        environment="development",
        frontend_url="http://localhost:5173",
        cors_extra_origins="http://localhost:5173",
    ).cors_allowed_origins

    assert len(origins) == len(set(origins))
