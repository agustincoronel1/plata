"""Piezas compartidas por los schemas de entrada y salida.

El dinero se modela con Decimal en todos lados. Pydantic 2, al parsear JSON, mantiene
los números destinados a un campo Decimal como texto y no los pasa por float, así que el
contrato monetario nunca toca un binario flotante. La serialización de salida devuelve
Decimal como string, y el frontend lo trata como texto de presentación.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

# Numeric(14, 2) en la base: hasta 14 dígitos, 2 decimales. Los mismos límites acá evitan
# que un monto válido para Pydantic reviente después contra el constraint de PostgreSQL.
_MONEY_KWARGS = {"max_digits": 14, "decimal_places": 2}

# Puede ser negativo: una persona puede estar en rojo y el saldo debe poder representarlo.
Money = Annotated[Decimal, Field(**_MONEY_KWARGS)]
# Estrictamente mayor que cero: montos de movimientos y compromisos.
PositiveMoney = Annotated[Decimal, Field(gt=0, **_MONEY_KWARGS)]
# Cero o más: montos de configuración que no admiten negativos.
NonNegativeMoney = Annotated[Decimal, Field(ge=0, **_MONEY_KWARGS)]


def _normalize_category(value: str) -> str:
    """Categoría consistente: sin espacios en los bordes y en minúsculas.

    Los datos demo ya usan categorías en minúscula ("supermercado", "vivienda"). Sin una
    normalización única, "Comida", "comida" y " comida " serían tres categorías distintas.
    """
    return value.strip().lower()


# Nombre visible (perfil, compromiso): requerido, recortado, hasta 120 caracteres.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
# Categoría: requerida, normalizada a minúsculas, hasta 60 caracteres.
Category = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=60),
    AfterValidator(_normalize_category),
]


def empty_to_none(value: str | None) -> str | None:
    """Un texto opcional que llega vacío o solo con espacios se guarda como NULL."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class NonEmptyUpdate(BaseModel):
    """Base de los schemas de actualización parcial (PATCH).

    Rechaza un body sin ningún campo: un PATCH que no cambia nada es un error del cliente,
    no una operación silenciosa.
    """

    @model_validator(mode="after")
    def _at_least_one_field(self) -> NonEmptyUpdate:
        if not self.model_fields_set:
            raise ValueError("Enviá al menos un campo para actualizar.")
        return self
