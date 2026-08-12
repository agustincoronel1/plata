"""Rate limiting por IP y por cuenta, con los contadores en PostgreSQL.

QUÉ ES Y QUÉ NO ES
------------------
Esto acota **cuántas peticiones por minuto** entran, para que la beta pública no se pueda
tirar abajo ni encarecer con un script. Es una cosa distinta de la cuota diaria de IA
(`ai_usage_service`), que acota **cuánto gasta una cuenta por día**. Conviven a propósito:

- La cuota de IA se descuenta solo cuando se va a invocar al modelo. Una acción manual
  —crear un movimiento, pagar un compromiso, ver el dashboard— nunca la toca.
- El rate limit cuenta peticiones, sin importar si hubo IA. Sus límites están puestos muy
  por encima del uso humano razonable, así que una persona usando Plata no los alcanza.

Las dos responden 429, y el cuerpo lleva un `code` distinto para que el frontend sepa cuál
fue sin leer el texto.

POR QUÉ EN POSTGRESQL Y NO EN MEMORIA
-------------------------------------
En Render el proceso se reinicia (y con plan gratuito se duerme), y puede haber más de una
instancia. Un contador en memoria se reiniciaría con el proceso y cada instancia contaría
por su lado: el límite existiría en el papel y no en los hechos. PostgreSQL ya está en la
arquitectura, es compartido por todas las instancias y sobrevive al reinicio, así que no
hace falta sumar Redis.

El incremento es una única sentencia, la misma técnica que ya usa la cuota diaria:

    INSERT ... VALUES (..., 1)
    ON CONFLICT (scope, subject_hash, window_start)
    DO UPDATE SET count = rate_limit_counters.count + 1
    WHERE rate_limit_counters.count < :limite
    RETURNING count

Sin lectura previa no hay ventana entre consultar y reservar, y PostgreSQL serializa la
fila, así que varias instancias comparten el contador sin coordinarse.

PRIVACIDAD DE LA IP
-------------------
No se guarda ninguna IP en claro. El sujeto se hashea con HMAC-SHA256 antes de tocar la
base, así que la tabla contiene identificadores opacos: alcanzan para contar y no sirven
para saber quién es nadie. Las filas vencidas se borran solas (`expires_at`).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.rate_limit_counter import RateLimitCounter

logger = logging.getLogger(__name__)

# Código estable del 429 de rate limit. El frontend lo usa para distinguirlo del 429 de
# cuota diaria de IA, que trae `daily_ai_limit_reached`.
RATE_LIMIT_CODE = "rate_limit_exceeded"

RATE_LIMIT_MESSAGE = (
    "Estás haciendo demasiadas peticiones. Esperá unos segundos y volvé a intentar."
)

# Cada cuántas peticiones se aprovecha para borrar ventanas vencidas. No hay proceso
# programado: la limpieza va a caballo de una petición cualquiera, muy de vez en cuando,
# para que la tabla no crezca sin fin. Es best-effort; si falla, no afecta a la petición.
_CLEANUP_EVERY = 500
_requests_since_cleanup = 0


class RateLimitExceededError(Exception):
    """Se superó un límite. La capa de API la traduce a un 429 (ver app.main).

    Lleva `retry_after_seconds` para la cabecera `Retry-After` y `scope` solo para el log:
    decirle al cliente qué límite exacto tocó le serviría para calibrar un ataque.
    """

    status_code = 429

    def __init__(self, *, scope: str, retry_after_seconds: int) -> None:
        self.scope = scope
        self.retry_after_seconds = max(retry_after_seconds, 1)
        self.detail = RATE_LIMIT_MESSAGE
        super().__init__(self.detail)


@dataclass(frozen=True)
class RateLimitRule:
    """Un límite: tantas peticiones por ventana, para un scope dado."""

    scope: str
    limit: int
    window_seconds: int


def _hash_subject(subject: str) -> str:
    """HMAC-SHA256 del sujeto. Es lo único que llega a la base.

    Con `RATE_LIMIT_IP_HASH_SECRET` definida el hash no se puede recorrer por fuerza bruta.
    Sin ella el hash sigue siendo irreversible, pero el espacio de direcciones IP es chico y
    alguien con acceso a la base podría probarlas todas, así que en producción se avisa.
    """
    secret = settings.rate_limit_ip_hash_secret
    if not secret and settings.environment != "development":
        logger.warning(
            "RATE_LIMIT_IP_HASH_SECRET sin definir: los sujetos del rate limit se hashean "
            "sin clave y podrían recorrerse por fuerza bruta."
        )
    return hmac.new(secret.encode("utf-8"), subject.encode("utf-8"), hashlib.sha256).hexdigest()


def client_ip(request: Request) -> str:
    """IP del cliente, teniendo en cuenta el proxy de Render.

    `X-Forwarded-For` solo se mira si está habilitado explícitamente: en un despliegue sin
    proxy, cualquiera podría mandar el header y estrenar una IP distinta en cada petición
    para no alcanzar nunca el límite.

    Se cuenta DESDE LA DERECHA, que es la parte que el cliente no controla: cada proxy anexa
    su origen al final, así que los tramos de la izquierda pueden venir inventados por quien
    llama.
    """
    if settings.rate_limit_trust_forwarded_for:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            depth = max(settings.rate_limit_forwarded_depth, 1)
            if len(parts) >= depth:
                return parts[-depth]
            if parts:
                return parts[0]

    client = request.client
    return client.host if client else "unknown"


def _window_start(now: datetime, window_seconds: int) -> datetime:
    """Inicio de la ventana fija a la que pertenece `now`.

    Ventana fija y no deslizante: es una sola fila por sujeto y tramo, sin historial de
    peticiones. El costo conocido es que se puede gastar el límite entero al final de una
    ventana y otra vez al principio de la siguiente; para acotar abuso en una beta alcanza.
    """
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)


def _maybe_cleanup(session: Session, now: datetime) -> None:
    """Borra ventanas vencidas cada tanto. Best-effort: nunca rompe la petición."""
    global _requests_since_cleanup

    _requests_since_cleanup += 1
    if _requests_since_cleanup < _CLEANUP_EVERY:
        return
    _requests_since_cleanup = 0

    try:
        session.execute(delete(RateLimitCounter).where(RateLimitCounter.expires_at <= now))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.warning("No se pudo limpiar rate_limit_counters", exc_info=True)


def hit(session: Session, rule: RateLimitRule, subject: str) -> None:
    """Cuenta una petición del sujeto contra la regla. Lanza si se pasó del límite.

    El sujeto llega en claro (una IP, un UUID) y se hashea acá: ningún llamador tiene que
    acordarse de hacerlo.

    Si el límite configurado es 0 o menos, la regla queda apagada. Es la forma de desactivar
    un límite puntual por entorno sin tocar código.
    """
    if not settings.rate_limit_enabled or rule.limit <= 0:
        return

    now = datetime.now(UTC)
    window_start = _window_start(now, rule.window_seconds)
    expires_at = window_start + timedelta(seconds=rule.window_seconds * 2)

    statement = (
        insert(RateLimitCounter)
        .values(
            scope=rule.scope,
            subject_hash=_hash_subject(f"{rule.scope}:{subject}"),
            window_start=window_start,
            count=1,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=["scope", "subject_hash", "window_start"],
            set_={"count": RateLimitCounter.count + 1, "updated_at": now},
            # Sin este WHERE el contador seguiría subiendo por encima del límite y la fila
            # nunca dejaría de actualizarse.
            where=RateLimitCounter.count < rule.limit,
        )
        .returning(RateLimitCounter.count)
    )

    try:
        used = session.execute(statement).scalar_one_or_none()
        # El contador se confirma en el momento: una petición rechazada más adelante igual
        # ocurrió, y si no se commitea, un rollback posterior la borraría del conteo.
        session.commit()
    except SQLAlchemyError:
        # Falla ABIERTO a propósito: si no se puede contar, se deja pasar.
        #
        # El límite protege de un exceso de peticiones, no de una base caída. Cortar acá
        # convertiría cualquier problema de PostgreSQL en un 500 en TODOS los endpoints,
        # incluido `/auth/me`, que antes de esto ni siquiera tocaba la base. Con la base
        # caída no queda nada que proteger: la aplicación ya no funciona.
        #
        # El riesgo que se acepta es que alguien capaz de tirar la base esquive el límite,
        # y en ese escenario el límite es el menor de los problemas.
        session.rollback()
        logger.warning(
            "No se pudo aplicar el rate limit del scope %s: se deja pasar la petición",
            rule.scope,
            exc_info=True,
        )
        return

    _maybe_cleanup(session, now)

    if used is None:
        window_end = window_start + timedelta(seconds=rule.window_seconds)
        retry_after = int((window_end - now).total_seconds())
        logger.info("Rate limit alcanzado en el scope %s", rule.scope)
        raise RateLimitExceededError(scope=rule.scope, retry_after_seconds=retry_after)
