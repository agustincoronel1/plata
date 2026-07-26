"""PostgresDraftStore: borradores persistentes con confirmación atómica.

La confirmación se reclama con un UPDATE condicional sobre `status` (una sola sentencia
atómica en PostgreSQL): `... SET status='confirming' WHERE id=:id AND status='pending'`.
Dos requests concurrentes ejecutan el mismo UPDATE; PostgreSQL serializa la fila y solo uno
afecta un renglón (RETURNING lo confirma). El resto recibe 409. La expiración es perezosa:
un `pending` vencido se marca `expired` al tocarlo.

Cada método abre su propia sesión corta (el store es un singleton del proceso, no vive
dentro del ciclo de un request). Nunca persiste secretos: solo el borrador serializado.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.ai.exceptions import (
    DraftAlreadyUsedError,
    DraftExpiredError,
    DraftNotFoundError,
)
from app.core.constants import DEMO_USER_ID
from app.core.database import SessionLocal
from app.models.ai_draft import AIDraft
from app.services.draft_store import DEFAULT_TTL_SECONDS, Draft, DraftStatus

_TASK = "transaction_parse"


class PostgresDraftStore:
    """Store persistente. Cumple la interfaz `DraftStore`."""

    def __init__(
        self, ttl_seconds: int = DEFAULT_TTL_SECONDS, session: Session | None = None
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._session = session

    def with_session(self, session: Session) -> PostgresDraftStore:
        return PostgresDraftStore(
            ttl_seconds=int(self._ttl.total_seconds()),
            session=session,
        )

    @contextmanager
    def _session_scope(self) -> Iterator[tuple[Session, bool]]:
        if self._session is not None:
            yield self._session, False
            return
        with SessionLocal() as session:
            yield session, True

    def create(self, payload: dict[str, Any], source_text: str) -> Draft:
        now = datetime.now(UTC)
        row = AIDraft(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            task=_TASK,
            source_text=source_text,
            payload=payload,
            status=DraftStatus.PENDING.value,
            created_at=now,
            expires_at=now + self._ttl,
        )
        with self._session_scope() as (session, owns_session):
            session.add(row)
            session.flush()
            if owns_session:
                session.commit()
                session.refresh(row)
            return _to_draft(row)

    def get(self, draft_id: UUID) -> Draft:
        with self._session_scope() as (session, owns_session):
            row = self._expire_if_needed(session, draft_id)
            if row.status == DraftStatus.EXPIRED.value:
                raise DraftExpiredError
            if owns_session:
                session.commit()
            return _to_draft(row)

    def claim_for_confirmation(self, draft_id: UUID) -> Draft:
        with self._session_scope() as (session, owns_session):
            self._expire_if_needed(session, draft_id)
            row = session.execute(
                update(AIDraft)
                .where(AIDraft.id == draft_id, AIDraft.status == DraftStatus.PENDING.value)
                .values(status=DraftStatus.CONFIRMING.value, version=AIDraft.version + 1)
                .returning(AIDraft)
            ).scalar_one_or_none()
            if row is None:
                if owns_session:
                    session.commit()
                self._raise_not_claimable(draft_id, session if not owns_session else None)
            if owns_session:
                session.commit()
            return _to_draft(row)

    def release_to_pending(self, draft_id: UUID) -> Draft:
        return self._simple_transition(draft_id, DraftStatus.CONFIRMING, DraftStatus.PENDING)

    def mark_confirmed(self, draft_id: UUID) -> Draft:
        return self._simple_transition(
            draft_id, DraftStatus.CONFIRMING, DraftStatus.CONFIRMED, stamp="confirmed_at"
        )

    def mark_rejected(self, draft_id: UUID) -> Draft:
        with self._session_scope() as (session, owns_session):
            self._expire_if_needed(session, draft_id)
            values: dict[str, Any] = {
                "status": DraftStatus.REJECTED.value,
                "rejected_at": datetime.now(UTC),
                "version": AIDraft.version + 1,
            }
            row = session.execute(
                update(AIDraft)
                .where(AIDraft.id == draft_id, AIDraft.status == DraftStatus.PENDING.value)
                .values(**values)
                .returning(AIDraft)
            ).scalar_one_or_none()
            if row is None:
                if owns_session:
                    session.commit()
                self._raise_not_claimable(draft_id, session if not owns_session else None)
            if owns_session:
                session.commit()
            return _to_draft(row)

    # --- Internos ---

    def _simple_transition(
        self,
        draft_id: UUID,
        expected: DraftStatus,
        new: DraftStatus,
        *,
        stamp: str | None = None,
    ) -> Draft:
        with self._session_scope() as (session, owns_session):
            values: dict[str, Any] = {"status": new.value, "version": AIDraft.version + 1}
            if stamp:
                values[stamp] = datetime.now(UTC)
            row = session.execute(
                update(AIDraft)
                .where(AIDraft.id == draft_id, AIDraft.status == expected.value)
                .values(**values)
                .returning(AIDraft)
            ).scalar_one_or_none()
            if row is None:
                if owns_session:
                    session.commit()
                self._raise_not_claimable(draft_id, session if not owns_session else None)
            if owns_session:
                session.commit()
            return _to_draft(row)

    def _expire_if_needed(self, session: Session, draft_id: UUID) -> AIDraft:
        now = datetime.now(UTC)
        session.execute(
            update(AIDraft)
            .where(
                AIDraft.id == draft_id,
                AIDraft.status == DraftStatus.PENDING.value,
                AIDraft.expires_at <= now,
            )
            .values(status=DraftStatus.EXPIRED.value)
        )
        row = session.get(AIDraft, draft_id, populate_existing=True)
        if row is None:
            raise DraftNotFoundError
        return row

    def _raise_not_claimable(self, draft_id: UUID, session: Session | None = None) -> None:
        if session is not None:
            row = session.get(AIDraft, draft_id)
        else:
            with SessionLocal() as lookup:
                row = lookup.get(AIDraft, draft_id)
        if row is None:
            raise DraftNotFoundError
        if row.status == DraftStatus.EXPIRED.value:
            raise DraftExpiredError
        raise DraftAlreadyUsedError


def _to_draft(row: AIDraft) -> Draft:
    return Draft(
        draft_id=row.id,
        payload=row.payload,
        source_text=row.source_text or "",
        status=DraftStatus(row.status),
        created_at=row.created_at,
        expires_at=row.expires_at,
    )
