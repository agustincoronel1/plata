"""Qué día es "hoy" para Vector, sin depender de la zona horaria del servidor.

Render corre en UTC. Sin esto, un gasto cargado a las 22:00 de Argentina quedaría fechado
al día siguiente. La zona de negocio sale de `settings.app_timezone` y es independiente de
`ai_usage_timezone`, que solo define el corte de la cuota diaria de IA: son dos decisiones
distintas aunque hoy apunten a la misma zona.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def today_in(tz_name: str, now: datetime | None = None) -> date:
    """Día calendario en `tz_name`. Un `now` sin tzinfo se interpreta como UTC."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz_name)).date()


def app_timezone_name() -> str:
    """Zona horaria de negocio: la que fecha el dinero."""
    return settings.app_timezone


def app_timezone() -> ZoneInfo:
    return ZoneInfo(app_timezone_name())


def app_today(now: datetime | None = None) -> date:
    """Día calendario de Vector para fechas de negocio (movimientos, pagos)."""
    return today_in(app_timezone_name(), now)
