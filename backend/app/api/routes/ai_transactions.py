"""Endpoints de registro asistido por IA: /api/v1/ai/transactions.

`parse` y `confirm` son DOS operaciones separadas y explícitas:

- `parse` interpreta texto y devuelve un borrador (200). No crea nada ni toca el saldo.
- `confirm` requiere confirmación humana y recién ahí crea el movimiento (201), reusando
  `transaction_service`. El saldo se actualiza una sola vez.
- `reject` descarta el borrador (204). No toca la base.

Los tres exigen sesión. Sin un JWT válido se responde 401 **antes** de llamar al modelo:
no se gasta una llamada a OpenAI, no se crea ningún borrador y no se toca la base. El
dueño del borrador es siempre `current_user.id`; nunca llega por body, query, path ni
header, y el modelo tampoco puede proponerlo.

`parse` es la única de las tres que llama al modelo, así que es la única que descuenta de
la cuota diaria de consultas inteligentes (la misma que usa el copiloto). Confirmar y
rechazar solo tocan PostgreSQL: no cuestan plata y no se limitan, porque dejar a alguien
sin poder confirmar un borrador que ya pagó sería absurdo.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.ai.gateway import AIGateway, get_ai_gateway
from app.api.rate_limits import ai_ip_limit, ai_user_limit
from app.api.usage_headers import apply_usage_headers, with_usage
from app.core.database import get_db
from app.core.security import CurrentUser
from app.schemas.ai_transaction import (
    TransactionConfirmationResponse,
    TransactionConfirmRequest,
    TransactionParseRequest,
    TransactionParseResponse,
)
from app.services import ai_transaction_service, ai_usage_service
from app.services.draft_store import DraftStore, get_draft_store

router = APIRouter(
    prefix="/ai/transactions",
    tags=["ai-transactions"],
    dependencies=[Depends(ai_ip_limit)],
)


@router.post(
    "/parse",
    response_model=TransactionParseResponse,
    summary="Interpretar un movimiento desde texto (no lo guarda)",
    description=(
        "Interpreta una frase en español rioplatense y devuelve un BORRADOR editable. No "
        "modifica el saldo ni crea el movimiento: para eso está `confirm`. La IA propone, "
        "una persona confirma. Requiere sesión: sin token no se llama al modelo. Tiene "
        "cuota diaria: al agotarla responde 429."
    ),
    # Límite por cuenta además de la cuota diaria: la cuota acota el gasto del día, esto
    # acota la ráfaga.
    dependencies=[Depends(ai_user_limit)],
)
def parse_transaction(
    payload: TransactionParseRequest,
    current_user: CurrentUser,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    gateway: Annotated[AIGateway, Depends(get_ai_gateway)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
) -> TransactionParseResponse:
    # La cuota se reserva acá, después de que FastAPI validó el cuerpo (un 422 no
    # gasta) y justo antes de la única llamada al modelo de este endpoint.
    with ai_usage_service.daily_quota(db, current_user.id) as quota:
        quota.consume()
        parsed = ai_transaction_service.parse_transaction(
            gateway, store, payload.text, user_id=current_user.id
        )

    apply_usage_headers(response, quota.status)
    return with_usage(parsed, quota.status)


@router.post(
    "/{draft_id}/confirm",
    response_model=TransactionConfirmationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirmar un borrador propio y registrar el movimiento",
    description=(
        "Con confirmación explícita (`confirmed=true`) y correcciones opcionales, crea el "
        "movimiento real reusando el servicio de transacciones y actualiza el saldo una "
        "sola vez. 404 si el borrador no existe **o es de otra cuenta**, 410 si expiró, "
        "409 si ya se usó, 422 si los datos no forman un movimiento válido."
    ),
)
def confirm_transaction(
    draft_id: UUID,
    payload: TransactionConfirmRequest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    store: Annotated[DraftStore, Depends(get_draft_store)],
) -> TransactionConfirmationResponse:
    return ai_transaction_service.confirm_transaction(
        db, store, draft_id, payload, user_id=current_user.id
    )


@router.post(
    "/{draft_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rechazar un borrador propio",
    description=(
        "Descarta el borrador. No crea movimiento ni modifica el saldo. 404 si no existe "
        "o es de otra cuenta."
    ),
)
def reject_transaction(
    draft_id: UUID,
    current_user: CurrentUser,
    store: Annotated[DraftStore, Depends(get_draft_store)],
) -> None:
    ai_transaction_service.reject_transaction(store, draft_id, user_id=current_user.id)
