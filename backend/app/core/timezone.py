from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def app_timezone_name() -> str:
    """Zona horaria principal de Plata, compartida por fechas de negocio."""
    return settings.ai_usage_timezone


def app_timezone() -> ZoneInfo:
    return ZoneInfo(app_timezone_name())


def app_today(now: datetime | None = None) -> date:
    """Día calendario de Plata, independiente de la zona horaria del servidor."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(app_timezone()).date()
