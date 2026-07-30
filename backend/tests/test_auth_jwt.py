"""Verificación del JWT de Supabase Auth y endpoint GET /api/v1/auth/me.

Estos tests NO tocan internet ni el proyecto real de Supabase: generan sus propias claves
RSA y EC de test y sustituyen la descarga del JWKS por un doble en memoria. Tampoco tocan
PostgreSQL: /auth/me no consulta la base, así que corren siempre.

Lo que se prueba es el contrato de seguridad completo: sin header, esquema equivocado,
token ilegible, firma de otra clave, vencido, emisor distinto, audiencia distinta, `sub`
ausente y `sub` que no es UUID terminan todos en 401. Solo un token bien firmado, vigente
y del proyecto correcto identifica al usuario.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.core import security
from app.core.config import settings
from app.main import app

ME = "/api/v1/auth/me"

ISSUER = "https://proyecto-de-test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
JWKS_URL = "https://proyecto-de-test.supabase.co/auth/v1/.well-known/jwks.json"

# Claves de test, generadas una sola vez para toda la suite. No son las del proyecto real
# y no salen de este proceso.
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())

RSA_KID = "kid-rsa-test"
OTHER_RSA_KID = "kid-rsa-rotada"
EC_KID = "kid-ec-test"


def _jwk(private_key: Any, kid: str, algorithm: str) -> dict[str, Any]:
    """JWK público (nunca la parte privada), con el mismo formato que publica Supabase."""
    to_jwk = RSAAlgorithm.to_jwk if algorithm == "RS256" else ECAlgorithm.to_jwk
    data = json.loads(to_jwk(private_key.public_key()))
    data.update({"kid": kid, "use": "sig", "alg": algorithm})
    return data


class FakeJWKS:
    """Doble del JWKS remoto: cuenta descargas y permite cambiar el set publicado."""

    def __init__(self, keys: list[dict[str, Any]]) -> None:
        self.keys = keys
        self.fetches = 0

    def __call__(self) -> dict[str, Any]:
        self.fetches += 1
        return {"keys": self.keys}


@pytest.fixture
def jwks(monkeypatch: pytest.MonkeyPatch) -> FakeJWKS:
    """Configura Supabase Auth con valores de test y desconecta la descarga del JWKS.

    Se usa el PyJWKClient real (así se ejercitan de verdad el parseo del JWK, la búsqueda
    por `kid` y el refresco ante una clave desconocida); lo único sustituido es el acceso
    a la red.
    """
    monkeypatch.setattr(settings, "supabase_jwks_url", JWKS_URL)
    monkeypatch.setattr(settings, "supabase_jwt_issuer", ISSUER)
    monkeypatch.setattr(settings, "supabase_jwt_audience", AUDIENCE)

    source = FakeJWKS([_jwk(RSA_KEY, RSA_KID, "RS256"), _jwk(EC_KEY, EC_KID, "ES256")])

    client = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=600)
    monkeypatch.setattr(client, "fetch_data", source)
    monkeypatch.setattr(security, "get_jwks_client", lambda: client)

    return source


@pytest.fixture
def api() -> TestClient:
    """Cliente HTTP sin overrides de base: /auth/me no consulta PostgreSQL."""
    return TestClient(app)


def make_token(
    *,
    key: Any = RSA_KEY,
    kid: str = RSA_KID,
    algorithm: str = "RS256",
    subject: str | None = None,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: timedelta = timedelta(hours=1),
    email: str | None = "persona@ejemplo.test",
    drop: tuple[str, ...] = (),
) -> str:
    """Arma un token firmado. Los parámetros existen para poder romper de a un claim."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject if subject is not None else str(uuid4()),
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "role": "authenticated",
    }
    if email is not None:
        claims["email"] = email
    for claim in drop:
        claims.pop(claim, None)

    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- Rechazos: el header ----------


def test_sin_header_authorization_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    response = api.get(ME)

    assert response.status_code == 401
    assert response.json()["detail"] == security.UNAUTHORIZED_DETAIL
    # El 401 indica cómo autenticarse, sin filtrar por qué falló.
    assert response.headers["www-authenticate"] == "Bearer"


def test_esquema_distinto_de_bearer_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token()

    for header in (f"Basic {token}", f"Token {token}", token):
        response = api.get(ME, headers={"Authorization": header})
        assert response.status_code == 401, header


def test_header_bearer_sin_token_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    assert api.get(ME, headers={"Authorization": "Bearer"}).status_code == 401
    assert api.get(ME, headers={"Authorization": "Bearer  "}).status_code == 401


def test_header_con_partes_de_mas_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    response = api.get(ME, headers={"Authorization": f"Bearer {make_token()} extra"})

    assert response.status_code == 401


# ---------- Rechazos: el token ----------


def test_token_malformado_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    for token in ("no-es-un-jwt", "a.b", "a.b.c", ""):
        response = api.get(ME, headers=bearer(token))
        assert response.status_code == 401, token


def test_firma_de_otra_clave_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    """El `kid` es de una clave publicada, pero la firma se hizo con otra distinta."""
    token = make_token(key=OTHER_RSA_KEY, kid=RSA_KID)

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_kid_desconocido_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(key=OTHER_RSA_KEY, kid="kid-que-no-existe")

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_token_expirado_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(expires_in=timedelta(minutes=-5))

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_token_sin_exp_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(drop=("exp",))

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_issuer_incorrecto_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(issuer="https://otro-proyecto.supabase.co/auth/v1")

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_audience_incorrecta_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(audience="service_role")

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_sub_ausente_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(drop=("sub",))

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_sub_que_no_es_uuid_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    for subject in ("usuario-1", "12345", "11111111-1111-4111-8111"):
        response = api.get(ME, headers=bearer(make_token(subject=subject)))
        assert response.status_code == 401, subject


def test_algoritmo_none_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    """Un token sin firma nunca se acepta, aunque los claims sean perfectos."""
    claims = {
        "sub": str(uuid4()),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(claims, key=None, algorithm="none", headers={"kid": RSA_KID})

    assert api.get(ME, headers=bearer(token)).status_code == 401


def test_token_hmac_con_secreto_adivinado_responde_401(api: TestClient, jwks: FakeJWKS) -> None:
    """Solo se aceptan algoritmos asimétricos: ningún secreto compartido sirve para firmar."""
    claims = {
        "sub": str(uuid4()),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(claims, "publishable-key-o-cualquier-otro-valor", algorithm="HS256")

    assert api.get(ME, headers=bearer(token)).status_code == 401


# ---------- Token válido ----------


def test_token_valido_devuelve_uuid_y_email(api: TestClient, jwks: FakeJWKS) -> None:
    user_id = str(uuid4())
    token = make_token(subject=user_id, email="persona@ejemplo.test")

    response = api.get(ME, headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"id": user_id, "email": "persona@ejemplo.test"}


def test_token_valido_firmado_con_ec_tambien_se_acepta(api: TestClient, jwks: FakeJWKS) -> None:
    """Supabase firma con ES256 en los proyectos nuevos."""
    user_id = str(uuid4())
    token = make_token(key=EC_KEY, kid=EC_KID, algorithm="ES256", subject=user_id)

    response = api.get(ME, headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["id"] == user_id


def test_token_sin_email_devuelve_email_nulo(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token(email=None)

    response = api.get(ME, headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["email"] is None


def test_respuesta_no_incluye_el_token_ni_claims_de_mas(api: TestClient, jwks: FakeJWKS) -> None:
    token = make_token()

    body = api.get(ME, headers=bearer(token)).json()

    assert set(body) == {"id", "email"}
    assert token not in json.dumps(body)


# ---------- JWKS: cacheo y rotación ----------


def test_el_jwks_se_descarga_una_sola_vez_para_varias_peticiones(
    api: TestClient, jwks: FakeJWKS
) -> None:
    for _ in range(3):
        assert api.get(ME, headers=bearer(make_token())).status_code == 200

    assert jwks.fetches == 1


def test_una_clave_nueva_se_resuelve_refrescando_el_jwks(api: TestClient, jwks: FakeJWKS) -> None:
    """Rotación: llega un token con un `kid` que el set cacheado no tiene."""
    assert api.get(ME, headers=bearer(make_token())).status_code == 200
    assert jwks.fetches == 1

    jwks.keys = [*jwks.keys, _jwk(OTHER_RSA_KEY, OTHER_RSA_KID, "RS256")]
    rotado = make_token(key=OTHER_RSA_KEY, kid=OTHER_RSA_KID)

    assert api.get(ME, headers=bearer(rotado)).status_code == 200
    assert jwks.fetches == 2


def test_el_cliente_jwks_es_unico_por_proceso() -> None:
    security.reset_jwks_client()
    try:
        assert security.get_jwks_client() is security.get_jwks_client()
    finally:
        security.reset_jwks_client()


# ---------- Configuración ausente ----------


def test_sin_jwks_configurado_no_se_hace_pasar_por_token_invalido(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un backend sin configurar responde 503, no 401: el problema no es del cliente."""
    monkeypatch.setattr(settings, "supabase_jwks_url", "")
    monkeypatch.setattr(settings, "supabase_jwt_issuer", "")

    response = api.get(ME, headers=bearer("un.token.cualquiera"))

    assert response.status_code == 503
    assert response.json()["detail"] == security.NOT_CONFIGURED_DETAIL
