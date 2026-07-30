"""Límites diarios de uso de IA, por usuario.

Protección de costos para una demo pública: acota cuánto puede gastar cada cuenta por día.
No es un sistema antifraude, no mira IPs y no intenta detectar abuso.

Las decisiones que no son obvias:

- El día corta a las 00:00 de Argentina, no en UTC, que es cuando la persona espera tener
  el día nuevo.
- La reserva ocurre antes de llamar al modelo. Contar al terminar dejaría pasar las
  llamadas concurrentes: dos peticiones simultáneas leerían el mismo contador y las dos
  creerían tener cuota.
- Si el proveedor nunca se llegó a invocar, la reserva se devuelve: el límite protege de
  un gasto real, no de un intento fallido.

El incremento es una única sentencia:

    INSERT ... VALUES (..., 1)
    ON CONFLICT (user_id, usage_day, kind)
    DO UPDATE SET used = ai_daily_usage.used + 1
    WHERE ai_daily_usage.used < :limite
    RETURNING used

Si el `WHERE` no se cumple, PostgreSQL no actualiza y no devuelve fila: eso es el límite
alcanzado. Sin lectura previa no hay ventana entre consultar y reservar.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.exceptions import AIDailyLimitReachedError, AIProviderUnavailableError
from app.core.config import settings
from app.models.ai_daily_usage import AIDailyUsage


class AIUsageKind(StrEnum):
    """Operaciones de IA con cuota propia. El valor se persiste en la columna `kind`."""

    COPILOT_CHAT = "copilot_chat"
    TRANSACTION_PARSE = "transaction_parse"


def limit_for(kind: AIUsageKind) -> int:
    """Cuota diaria configurada para una operación."""
    if kind is AIUsageKind.COPILOT_CHAT:
        return settings.ai_daily_chat_limit
    return settings.ai_daily_parse_limit


@dataclass(frozen=True)
class AIUsageStatus:
    """Foto del contador de una operación, lista para mostrarle a la persona."""

    kind: AIUsageKind
    limit: int
    used: int
    # Día al que corresponde el contador, en Argentina. De acá sale `resets_at`.
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
    def resets_at(self) -> datetime:
        """Cuándo vuelve a haber cuota: 00:00 del día siguiente, hora de Argentina.

        Va con offset explícito (-03:00) y no en UTC para que el frontend pueda mostrar
        "mañana" sin tener que saber en qué zona se cortó el día.
        """
        tz = ZoneInfo(settings.ai_usage_timezone)
        return datetime.combine(self.day + timedelta(days=1), time.min, tzinfo=tz)

    @property
    def retry_after_seconds(self) -> int:
        """Segundos hasta el reinicio, para la cabecera `Retry-After`. Mínimo 1."""
        delta = self.resets_at - datetime.now(UTC)
        return max(int(delta.total_seconds()), 1)


def usage_day(now: datetime | None = None) -> date:
    """Día calendario en Argentina. Es la clave del contador y define el reinicio diario."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(settings.ai_usage_timezone)).date()


def get_status(
    session: Session, user_id: UUID, kind: AIUsageKind, *, day: date | None = None
) -> AIUsageStatus:
    """Cuánto lleva usado hoy. Solo lectura: no reserva nada."""
    today = day or usage_day()
    row = session.get(AIDailyUsage, (user_id, today, str(kind)))
    return AIUsageStatus(kind=kind, limit=limit_for(kind), used=row.used if row else 0, day=today)


def get_all_status(
    session: Session, user_id: UUID, *, day: date | None = None
) -> dict[AIUsageKind, AIUsageStatus]:
    """El estado de todas las cuotas del usuario, para mostrarlo de una."""
    today = day or usage_day()
    return {kind: get_status(session, user_id, kind, day=today) for kind in AIUsageKind}


def consume(
    session: Session, user_id: UUID, kind: AIUsageKind, *, day: date | None = None
) -> AIUsageStatus:
    """Reserva un uso de forma atómica. Lanza `AIDailyLimitReachedError` si no queda cuota.

    Hace commit inmediatamente: la reserva tiene que sobrevivir aunque la operación de IA
    falle después. Si no se llegó a invocar al proveedor, se devuelve con `refund`, que es
    explícito y deja rastro, en lugar de depender de que algo haga rollback.
    """
    limit = limit_for(kind)
    today = day or usage_day()

    if limit <= 0:
        raise AIDailyLimitReachedError(AIUsageStatus(kind=kind, limit=limit, used=0, day=today))

    values = {"user_id": user_id, "usage_day": today, "kind": str(kind), "used": 1}
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
        raise AIDailyLimitReachedError(get_status(session, user_id, kind, day=today))

    session.commit()
    return AIUsageStatus(kind=kind, limit=limit, used=used, day=today)


def refund(
    session: Session, user_id: UUID, kind: AIUsageKind, *, day: date | None = None
) -> AIUsageStatus:
    """Devuelve un uso reservado que no llegó a gastar nada.

    Nunca baja de cero: si algo lo llamara de más, el contador no se rompe.
    """
    today = day or usage_day()
    used = session.execute(
        update(AIDailyUsage)
        .where(
            AIDailyUsage.user_id == user_id,
            AIDailyUsage.usage_day == today,
            AIDailyUsage.kind == str(kind),
            AIDailyUsage.used > 0,
        )
        .values(used=AIDailyUsage.used - 1, updated_at=datetime.now(UTC))
        .returning(AIDailyUsage.used)
    ).scalar_one_or_none()
    session.commit()

    if used is None:
        return get_status(session, user_id, kind, day=today)
    return AIUsageStatus(kind=kind, limit=limit_for(kind), used=used, day=today)


class DailyQuota:
    """Cuota de una operación dentro de un request. La crea `daily_quota`."""

    def __init__(self, session: Session, user_id: UUID, kind: AIUsageKind) -> None:
        self._session = session
        self._user_id = user_id
        self._kind = kind
        self._day = usage_day()
        self.status: AIUsageStatus | None = None

    def consume(self) -> None:
        """Reserva el uso. Se llama justo antes de invocar al proveedor, nunca antes."""
        self.status = consume(self._session, self._user_id, self._kind, day=self._day)

    def refund(self) -> None:
        if self.status is None:
            return
        self.status = refund(self._session, self._user_id, self._kind, day=self._day)


@contextmanager
def daily_quota(session: Session, user_id: UUID, kind: AIUsageKind) -> Iterator[DailyQuota]:
    """Envuelve una operación de IA con su cuota diaria.

    Si el bloque falla porque el proveedor no estaba disponible —es decir, no se llegó a
    gastar una llamada— la reserva se devuelve. Cualquier otro error mantiene el consumo:
    si el modelo se invocó, el costo ya se produjo, y da igual que la respuesta no sirviera.
    """
    quota = DailyQuota(session, user_id, kind)
    try:
        yield quota
    except AIProviderUnavailableError:
        quota.refund()
        raise
