from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIDailyUsage(Base):
    """Contador diario de consultas inteligentes, por usuario y por día.

    Es una protección de costos para una beta pública, no un sistema antifraude. Cuenta
    solo las operaciones que llegan a invocar al proveedor: una validación que falla antes
    no gasta cuota porque no gasta plata.

    La clave primaria es compuesta y eso es lo que hace posible el incremento atómico: un
    único `INSERT ... ON CONFLICT DO UPDATE ... WHERE used < limite` resuelve "reservar un
    uso si queda alguno" en una sola sentencia, sin leer primero. Dos peticiones
    concurrentes no pueden atravesar el límite —PostgreSQL serializa la fila y la segunda
    ve el valor ya incrementado— y varias instancias del backend comparten el contador sin
    coordinarse entre ellas.

    `kind` es herencia del diseño anterior, que tenía una cuota por operación. Hoy la cuota
    es una sola, compartida por todos los canales, y la columna guarda siempre
    `ai_usage_service.USAGE_BUCKET`: en los hechos, la clave lógica es (usuario, día) y no
    puede haber dos registros del mismo usuario para la misma fecha. Se conserva la columna
    en vez de borrarla para no correr una migración destructiva sobre datos vivos.

    `usage_day` NO es una fecha UTC: es el día calendario en la zona configurada (ver
    `ai_usage_service`), que es cuando el usuario espera que se le reinicie el contador.

    No hay foreign key al perfil a propósito: el contador tiene que existir aunque la
    persona todavía no haya completado el onboarding, y tiene que sobrevivir a que borre
    sus datos financieros.
    """

    __tablename__ = "ai_daily_usage"

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    usage_day: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), primary_key=True)

    used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
