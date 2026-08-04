"""Límite diario de consultas inteligentes, por usuario.

Una sola cuota por cuenta y por día, compartida por **todos** los canales: hoy el copiloto
web y la interpretación de movimientos; mañana WhatsApp. El contador no sabe de canales a
propósito — la clave es `(user_id, usage_day)` — así que un canal nuevo consume el mismo
cupo con solo llamar a `daily_quota` con el UUID del usuario autenticado.

Protección de costos para una beta pública. No es un sistema antifraude, no mira IPs y no
intenta detectar abuso.

Las decisiones que no son obvias:

- El día corta a las 00:00 de Argentina (`AI_USAGE_TIMEZONE`), no en UTC, que es cuando la
  persona espera tener el día nuevo.
- La reserva ocurre antes de llamar al modelo. Contar al terminar dejaría pasar las
  llamadas concurrentes: dos peticiones simultáneas leerían el mismo contador y las dos
  creerían tener cuota.
- Si el proveedor nunca se llegó a invocar, la reserva se devuelve: el límite protege de
  un gasto real, no de un intento fallido. Si el proveedor SÍ se invocó, el uso se cuenta
  aunque la respuesta después falle: la llamada ya se pagó.

El incremento es una única sentencia:

    INSERT ... VALUES (..., 1)
    ON CONFLICT (user_id, usage_day, kind)
    DO UPDATE SET used = ai_daily_usage.used + 1
    WHERE ai_daily_usage.used < :limite
    RETURNING used

Si el `WHERE` no se cumple, PostgreSQL no actualiza y no devuelve fila: eso es el límite
alcanzado. Sin lectura previa no hay ventana entre consultar y reservar, y como PostgreSQL
serializa la fila, varias instancias del backend comparten el mismo contador sin ponerse
de acuerdo entre ellas.

Sobre la tercera columna de la clave (`kind`): viene del diseño anterior, que tenía una
cuota por operación. Ahora hay una sola, así que se escribe siempre `USAGE_BUCKET` y la
clave primaria deja, en los hechos, un único registro por usuario y día. Se conserva la
columna en lugar de borrarla para no correr una migración destructiva sobre datos vivos.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.exceptions import AIDailyLimitReachedError, AIProviderUnavailableError
from app.core.config import settings
from app.models.ai_daily_usage import AIDailyUsage

# Valor único de la columna `kind`. No es una dimensión del contador: está fijo para que la
# clave primaria heredada equivalga a "un registro por usuario y por día".
USAGE_BUCKET = "ai_query"


def daily_limit() -> int:
    """Consultas inteligentes por día y por cuenta (`AI_DAILY_LIMIT`, por defecto 10)."""
    return settings.ai_daily_limit


def usage_timezone() -> str:
    """Zona horaria del corte diario. Viaja en las respuestas para que no haya que adivinarla."""
    return settings.ai_usage_timezone


@dataclass(frozen=True)
class AIUsageStatus:
    """Foto del contador de una cuenta, lista para mostrarle a la persona."""

    limit: int
    used: int
    # Día al que corresponde el contador, en la zona configurada. De acá sale `resets_at`.
    day: date

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def warning(self) -> bool:
        """Si conviene avisar que se está por quedar sin cuota."""
        return 0 < self.remaining <= settings.ai_usage_warning_threshold

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    @property
    def timezone(self) -> str:
        return usage_timezone()

    @property
    def resets_at(self) -> datetime:
        """Cuándo vuelve a haber cuota: 00:00 del día siguiente, hora de Argentina.

        Va con offset explícito (-03:00) y no en UTC para que el frontend pueda mostrar
        "mañana" sin tener que saber en qué zona se cortó el día.
        """
        tz = ZoneInfo(usage_timezone())
        return datetime.combine(self.day + timedelta(days=1), time.min, tzinfo=tz)

    @property
    def retry_after_seconds(self) -> int:
        """Segundos hasta el reinicio, para la cabecera `Retry-After`. Mínimo 1."""
        delta = self.resets_at - datetime.now(UTC)
        return max(int(delta.total_seconds()), 1)


def usage_day(now: datetime | None = None) -> date:
    """Día calendario en la zona configurada. Es la clave del contador y define el reinicio."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(usage_timezone())).date()


def get_status(session: Session, user_id: UUID, *, day: date | None = None) -> AIUsageStatus:
    """Cuánto lleva usado hoy. Solo lectura: consultar el estado no gasta cuota."""
    today = day or usage_day()
    row = session.get(AIDailyUsage, (user_id, today, USAGE_BUCKET))
    return AIUsageStatus(limit=daily_limit(), used=row.used if row else 0, day=today)


def consume(session: Session, user_id: UUID, *, day: date | None = None) -> AIUsageStatus:
    """Reserva un uso de forma atómica. Lanza `AIDailyLimitReachedError` si no queda cuota.

    Hace commit inmediatamente: la reserva tiene que sobrevivir aunque la operación de IA
    falle después. Si no se llegó a invocar al proveedor, se devuelve con `refund`, que es
    explícito y deja rastro, en lugar de depender de que algo haga rollback.
    """
    limit = daily_limit()
    today = day or usage_day()

    if limit <= 0:
        raise AIDailyLimitReachedError(AIUsageStatus(limit=limit, used=0, day=today))

    values = {"user_id": user_id, "usage_day": today, "kind": USAGE_BUCKET, "used": 1}
    statement = (
        insert(AIDailyUsage)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["user_id", "usage_day", "kind"],
            set_={"used": AIDailyUsage.used + 1, "updated_at": datetime.now(UTC)},
            # Sin este WHERE, el contador seguiría subiendo por encima del límite.
            where=AIDailyUsage.used < limit,
        )
        .returning(AIDailyUsage.used)
    )

    used = session.execute(statement).scalar_one_or_none()
    if used is None:
        # No hubo fila: la única causa posible es que el WHERE no se cumpliera, es decir
        # que la cuenta ya estaba en el límite. La sentencia no falló ni escribió nada, así
        # que no hay nada que revertir: se corta con 429 y listo. El error viaja con el
        # estado real del contador para que la respuesta pueda decir cuándo se renueva.
        raise AIDailyLimitReachedError(get_status(session, user_id, day=today))

    session.commit()
    return AIUsageStatus(limit=limit, used=used, day=today)


def refund(session: Session, user_id: UUID, *, day: date | None = None) -> AIUsageStatus:
    """Devuelve un uso reservado que no llegó a gastar nada.

    Nunca baja de cero: si algo lo llamara de más, el contador no se rompe.
    """
    today = day or usage_day()
    used = session.execute(
        update(AIDailyUsage)
        .where(
            AIDailyUsage.user_id == user_id,
            AIDailyUsage.usage_day == today,
            AIDailyUsage.kind == USAGE_BUCKET,
            AIDailyUsage.used > 0,
        )
        .values(used=AIDailyUsage.used - 1, updated_at=datetime.now(UTC))
        .returning(AIDailyUsage.used)
    ).scalar_one_or_none()
    session.commit()

    if used is None:
        return get_status(session, user_id, day=today)
    return AIUsageStatus(limit=daily_limit(), used=used, day=today)


class DailyQuota:
    """Cuota de una petición de IA. La crea `daily_quota`.

    `consume()` es idempotente dentro del mismo objeto: una misma petición no descuenta dos
    veces aunque el flujo llame al gancho más de una vez.
    """

    def __init__(self, session: Session, user_id: UUID) -> None:
        self._session = session
        self._user_id = user_id
        self._day = usage_day()
        self.status: AIUsageStatus | None = None

    def consume(self) -> None:
        """Reserva el uso. Se llama justo antes de invocar al proveedor, nunca antes."""
        if self.status is not None:
            return
        self.status = consume(self._session, self._user_id, day=self._day)

    def refund(self) -> None:
        if self.status is None:
            return
        self.status = refund(self._session, self._user_id, day=self._day)


@contextmanager
def daily_quota(session: Session, user_id: UUID) -> Iterator[DailyQuota]:
    """Envuelve una operación de IA con la cuota diaria de la cuenta.

    Es el único punto de entrada que debería usar un canal nuevo (WhatsApp incluido): con
    el UUID del usuario autenticado alcanza, y el cupo es el mismo que el de la web.

    Si el bloque falla porque el proveedor no estaba disponible —es decir, no se llegó a
    gastar una llamada— la reserva se devuelve. Cualquier otro error mantiene el consumo:
    si el modelo se invocó, el costo ya se produjo, y da igual que la respuesta no sirviera.
    """
    quota = DailyQuota(session, user_id)
    try:
        yield quota
    except AIProviderUnavailableError:
        quota.refund()
        raise
