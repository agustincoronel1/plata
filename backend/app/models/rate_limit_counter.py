from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RateLimitCounter(Base):
    """Contador de una ventana de rate limiting.

    Vive en PostgreSQL y no en memoria por dos motivos concretos de la beta: en Render el
    proceso se reinicia (y con plan gratuito se duerme), y puede haber más de una instancia.
    Un contador en memoria se perdería en el reinicio y cada instancia contaría por su lado,
    que es exactamente lo que vuelve inútil un límite. Acá el contador es uno solo para todo
    el despliegue, sin coordinación entre procesos y sin agregar Redis a la infraestructura.

    Es la misma técnica que ya usa `ai_daily_usage`: la clave primaria compuesta habilita el
    incremento atómico con un único `INSERT ... ON CONFLICT DO UPDATE ... WHERE count <
    limite`. Sin leer primero no hay ventana entre consultar y reservar, así que dos
    peticiones simultáneas no pueden atravesar el límite.

    Ventana fija (fixed window): `window_start` es el inicio del tramo al que pertenece la
    petición. Es más simple que una ventana deslizante y para acotar abuso en una beta
    alcanza; el costo conocido es que se puede consumir el límite entero al final de una
    ventana y otra vez al principio de la siguiente.

    Sobre `subject_hash`: NUNCA guarda una IP en claro. Quien llama hashea el sujeto (IP o
    UUID de usuario) con HMAC-SHA256 antes de llegar acá, así que la tabla solo contiene
    identificadores opacos e irreversibles. Contar no necesita saber a quién se cuenta, y
    una beta pública no tiene por qué acumular un registro de direcciones IP.

    Sin foreign key al perfil: el límite tiene que poder aplicarse a peticiones sin sesión y
    a cuentas que todavía no completaron el onboarding.
    """

    __tablename__ = "rate_limit_counters"
    # Para la limpieza de ventanas vencidas: sin este índice, borrarlas obliga a recorrer
    # la tabla entera.
    __table_args__ = (Index("ix_rate_limit_counters_expires_at", "expires_at"),)

    # Qué se está limitando: "ip:ai", "user:ai", "ip:api"… Lo define `app.core.rate_limit`.
    scope: Mapped[str] = mapped_column(String(60), primary_key=True)
    # HMAC-SHA256 del sujeto. Opaco a propósito: no se puede volver a la IP original.
    subject_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Cuándo deja de servir esta fila. Es lo único que necesita la limpieza para borrar
    # ventanas viejas sin tener que saber cuánto duraba cada scope.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
