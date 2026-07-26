"""Evaluación de respuestas grounded del copiloto (requiere PostgreSQL, mock).

El verificador determinístico ya corre DENTRO del grafo: si una respuesta afirmara un monto
sin respaldo, se reemplaza por un mensaje seguro. Acá medimos que las respuestas que deben
estar fundamentadas efectivamente lo estén (no cayeron al fallback) y que las búsquedas
traigan evidencia.

    python -m app.ai.evaluators.grounded_answers
"""

from __future__ import annotations

import sys
import uuid
from datetime import date

from app.ai.agent.brain import MockAgentBrain
from app.ai.evaluators._common import Metric, load_jsonl, print_report
from app.ai.evaluators._dbharness import postgres_available, seeded_session
from app.ai.gateway import AIGateway, build_provider
from app.core.config import Settings
from app.services import ai_chat_service
from app.services.draft_store import InMemoryDraftStore

DATASET = "grounded_answers.jsonl"
AS_OF = date(2026, 7, 24)
_FALLBACKS = ("No pude verificar", "No pude resolver eso con datos confiables")


def _is_fallback(answer: str) -> bool:
    return any(marker in answer for marker in _FALLBACKS)


def run() -> int:
    if not postgres_available():
        print("grounded_answers: PostgreSQL no disponible, omito (exit 0).")
        return 0

    supported = Metric("supported_amount_rate")
    coverage = Metric("evidence_coverage")
    unsupported = Metric("unsupported_claim_rate")
    failures: list[str] = []

    store = InMemoryDraftStore()
    gateway = AIGateway(build_provider(Settings(ai_provider="mock")))
    brain = MockAgentBrain()

    with seeded_session() as session:
        for row in load_jsonl(DATASET):
            resp = ai_chat_service.chat(
                session,
                row["message"],
                uuid.uuid4(),
                as_of=AS_OF,
                draft_store=store,
                gateway=gateway,
                brain=brain,
            )
            grounded = not _is_fallback(resp.answer)
            if row["must_be_grounded"]:
                supported.add(grounded)
                unsupported.add(not grounded)
                if not grounded:
                    failures.append(f"'{row['message'][:30]}' cayó al fallback")
            if row["expects_evidence"]:
                coverage.add(len(resp.evidence) >= 1)
                if not resp.evidence:
                    failures.append(f"'{row['message'][:30]}' sin evidencia")

    metrics = [supported, coverage, unsupported]
    print_report("grounded_answers (mock)", metrics, failures)
    return 0 if supported.rate == 1.0 and coverage.rate == 1.0 else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
