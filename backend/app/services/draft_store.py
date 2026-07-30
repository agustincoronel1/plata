"""Almacén de borradores (human-in-the-loop) con confirmación atómica.

Un `parse` de IA no guarda nada: crea un **borrador** que un humano debe confirmar. El
store guarda esos borradores con TTL y una máquina de estados:

    pending → confirming → confirmed
    pending → rejected
    pending → expired

`claim_for_confirmation` es la operación **atómica** que evita la doble confirmación: solo
un `pending` puede pasar a `confirming`, y solo el primer request lo logra; el segundo
recibe 409. Si PostgreSQL falla al crear el movimiento, `release_to_pending` devuelve el
borrador a `pending` para reintentar. Un `confirmed`, `rejected` o `expired` no se reutiliza.

Hay dos implementaciones detrás de la misma interfaz (`DraftStore`):

- `InMemoryDraftStore`: para tests y desarrollo. Un único lock protege todo el estado.
- `PostgresDraftStore` (app.services.draft_store_pg): persistente, reclama con un UPDATE
  condicional atómico. Es el store de ejecución normal.

Nunca guarda API keys ni respuestas crudas del modelo: solo el borrador estructurado ya
serializado y el texto de origen.

Todo borrador pertenece a un usuario. `user_id` es keyword-only y obligatorio en cada
operación: sin valor por defecto no se puede tocar el borrador de otra persona por olvido.
El filtro por dueño va SIEMPRE en la misma consulta que el id, y un borrador ajeno se
comporta igual que uno inexistente (`DraftNotFoundError` → 404): el código de estado no
sirve para averiguar si existe.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.ai.exceptions import (
    DraftAlreadyUsedError,
    DraftExpiredError,
    DraftNotFoundError,
)

DEFAULT_TTL_SECONDS = 900  # 15 minutos


class DraftStatus(StrEnum):
    PENDING = "pending"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Draft:
    """Un borrador temporal. `payload` es el borrador estructurado ya serializado."""

    draft_id: UUID
    # Dueño del borrador. Sale del JWT verificado, nunca del cuerpo ni del modelo.
    user_id: UUID
    payload: dict[str, Any]
    source_text: str
    status: DraftStatus
    created_at: datetime
    expires_at: datetime


class DraftStore(ABC):
    """Interfaz común a los stores en memoria y en PostgreSQL."""

    @abstractmethod
    def create(self, payload: dict[str, Any], source_text: str, *, user_id: UUID) -> Draft:
        """Crea un borrador propiedad de `user_id`."""

    @abstractmethod
    def get(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        """Borrador propio. `DraftNotFoundError` si no existe o es de otra persona."""

    @abstractmethod
    def claim_for_confirmation(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        """pending → confirming, atómico. 409 si ya está siendo usado o consumido."""

    @abstractmethod
    def release_to_pending(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        """confirming → pending (cuando la persistencia falla y hay que reintentar)."""

    @abstractmethod
    def mark_confirmed(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        """confirming → confirmed (después de crear el movimiento)."""

    @abstractmethod
    def mark_rejected(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        """pending → rejected. Rechazar no toca la base."""


class InMemoryDraftStore(DraftStore):
    """Store thread-safe para el proceso actual. Un único lock protege todo el estado."""

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._drafts: dict[UUID, Draft] = {}

    def create(self, payload: dict[str, Any], source_text: str, *, user_id: UUID) -> Draft:
        now = self._clock()
        draft = Draft(
            draft_id=uuid4(),
            user_id=user_id,
            payload=payload,
            source_text=source_text,
            status=DraftStatus.PENDING,
            created_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._drafts[draft.draft_id] = draft
        return draft

    def get(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        with self._lock:
            draft = self._require_locked(draft_id, user_id)
            draft = self._expire_if_needed_locked(draft)
            if draft.status == DraftStatus.EXPIRED:
                raise DraftExpiredError
            return draft

    def claim_for_confirmation(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        return self._transition(
            draft_id, user_id, DraftStatus.CONFIRMING, require=DraftStatus.PENDING
        )

    def release_to_pending(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        return self._transition(
            draft_id, user_id, DraftStatus.PENDING, require=DraftStatus.CONFIRMING
        )

    def mark_confirmed(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        return self._transition(
            draft_id, user_id, DraftStatus.CONFIRMED, require=DraftStatus.CONFIRMING
        )

    def mark_rejected(self, draft_id: UUID, *, user_id: UUID) -> Draft:
        return self._transition(
            draft_id, user_id, DraftStatus.REJECTED, require=DraftStatus.PENDING
        )

    # --- Internos (siempre bajo lock) ---

    def _transition(
        self, draft_id: UUID, user_id: UUID, new_status: DraftStatus, *, require: DraftStatus
    ) -> Draft:
        with self._lock:
            draft = self._require_locked(draft_id, user_id)
            draft = self._expire_if_needed_locked(draft)
            self._assert_status(draft, require)
            updated = replace(draft, status=new_status)
            self._drafts[draft_id] = updated
            return updated

    def _require_locked(self, draft_id: UUID, user_id: UUID) -> Draft:
        """El borrador de otra persona es indistinguible de uno inexistente."""
        draft = self._drafts.get(draft_id)
        if draft is None or draft.user_id != user_id:
            raise DraftNotFoundError
        return draft

    def _expire_if_needed_locked(self, draft: Draft) -> Draft:
        if draft.status == DraftStatus.PENDING and self._clock() >= draft.expires_at:
            expired = replace(draft, status=DraftStatus.EXPIRED)
            self._drafts[draft.draft_id] = expired
            return expired
        return draft

    @staticmethod
    def _assert_status(draft: Draft, required: DraftStatus) -> None:
        if draft.status == required:
            return
        if draft.status == DraftStatus.EXPIRED:
            raise DraftExpiredError
        raise DraftAlreadyUsedError


# Instancia global del proceso. Los endpoints la obtienen por dependencia, así los tests
# pueden inyectar un store fresco por test y evitar contaminación entre casos.
_default_store: DraftStore | None = None


def get_draft_store() -> DraftStore:
    """Dependencia de FastAPI: el store configurado (PostgreSQL en ejecución normal).

    Si `AI_DRAFT_STORE=memory`, usa el store en memoria (útil sin base). En cualquier caso
    los tests lo sobreescriben con `app.dependency_overrides` para aislarse entre casos.
    """
    global _default_store
    if _default_store is None:
        from app.core.config import settings

        if settings.ai_draft_store == "postgres":
            from app.services.draft_store_pg import PostgresDraftStore

            _default_store = PostgresDraftStore(ttl_seconds=settings.ai_draft_ttl_seconds)
        else:
            _default_store = InMemoryDraftStore(ttl_seconds=settings.ai_draft_ttl_seconds)
    return _default_store
