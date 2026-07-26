"""Tests del InMemoryDraftStore: ciclo de vida, TTL y confirmación atómica concurrente."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.exceptions import (
    DraftAlreadyUsedError,
    DraftExpiredError,
    DraftNotFoundError,
)
from app.services.draft_store import DraftStatus, InMemoryDraftStore

PAYLOAD = {"transaction": {"amount": "1000"}}


def _store(**kwargs) -> InMemoryDraftStore:
    return InMemoryDraftStore(**kwargs)


def test_create_y_get() -> None:
    store = _store()
    draft = store.create(PAYLOAD, "gasté 1000")
    assert draft.status is DraftStatus.PENDING
    assert store.get(draft.draft_id).payload == PAYLOAD


def test_get_inexistente_es_not_found() -> None:
    import uuid

    with pytest.raises(DraftNotFoundError):
        _store().get(uuid.uuid4())


def test_claim_confirm_flujo_feliz() -> None:
    store = _store()
    draft = store.create(PAYLOAD, "x")
    store.claim_for_confirmation(draft.draft_id)
    confirmed = store.mark_confirmed(draft.draft_id)
    assert confirmed.status is DraftStatus.CONFIRMED


def test_mark_confirmed_requiere_confirming() -> None:
    store = _store()
    draft = store.create(PAYLOAD, "x")
    # Sin reclamar antes: no se puede confirmar directo desde pending.
    with pytest.raises(DraftAlreadyUsedError):
        store.mark_confirmed(draft.draft_id)


def test_release_devuelve_a_pending() -> None:
    store = _store()
    draft = store.create(PAYLOAD, "x")
    store.claim_for_confirmation(draft.draft_id)
    released = store.release_to_pending(draft.draft_id)
    assert released.status is DraftStatus.PENDING
    # Y se puede volver a reclamar.
    store.claim_for_confirmation(draft.draft_id)


def test_reject_desde_pending() -> None:
    store = _store()
    draft = store.create(PAYLOAD, "x")
    assert store.mark_rejected(draft.draft_id).status is DraftStatus.REJECTED
    with pytest.raises(DraftAlreadyUsedError):
        store.claim_for_confirmation(draft.draft_id)


def test_ttl_expira() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    clock = {"t": now}
    store = _store(ttl_seconds=900, clock=lambda: clock["t"])
    draft = store.create(PAYLOAD, "x")
    clock["t"] = now + timedelta(seconds=901)
    with pytest.raises(DraftExpiredError):
        store.get(draft.draft_id)
    with pytest.raises(DraftExpiredError):
        store.claim_for_confirmation(draft.draft_id)


def test_confirmacion_concurrente_gana_uno_solo() -> None:
    store = _store()
    draft = store.create(PAYLOAD, "x")
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        try:
            store.claim_for_confirmation(draft.draft_id)
            results.append("claimed")
        except DraftAlreadyUsedError:
            results.append("rejected")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("claimed") == 1
    assert results.count("rejected") == 7
