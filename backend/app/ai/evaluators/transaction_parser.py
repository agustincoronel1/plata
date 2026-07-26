"""Evaluación offline del parsing de movimientos.

    python -m app.ai.evaluators.transaction_parser [--provider mock]

Usa el proveedor MOCK por defecto (nunca un proveedor real salvo pedido explícito). Lee
`evals/transaction_parsing.jsonl`, corre cada caso por el gateway + reglas de negocio y
calcula métricas reales. Sale con código != 0 si el schema falla en algún caso o si el
runner se rompe.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal

from app.ai.evaluators._common import Metric, load_jsonl, print_report
from app.ai.gateway import AIGateway, build_provider
from app.core.config import Settings
from app.services import ai_transaction_service as svc
from app.services.draft_store import InMemoryDraftStore

DATASET = "transaction_parsing.jsonl"


def _amount_eq(actual: Decimal | None, expected: str | None) -> bool:
    if expected is None:
        return actual is None
    return actual is not None and actual == Decimal(expected)


def run(provider_name: str = "mock") -> int:
    rows = load_jsonl(DATASET)
    gateway = AIGateway(build_provider(Settings(ai_provider=provider_name)))

    intent = Metric("intent_accuracy")
    type_acc = Metric("type_accuracy")
    amount = Metric("amount_exact_match")
    date_acc = Metric("date_exact_match")
    missing = Metric("missing_fields_accuracy")
    schema = Metric("valid_schema_rate")
    failures: list[str] = []

    for row in rows:
        as_of = date.fromisoformat(row["as_of"])
        try:
            resp = svc.parse_transaction(gateway, InMemoryDraftStore(), row["input"], as_of=as_of)
        except Exception as exc:  # noqa: BLE001 - una salida inválida cuenta como fallo
            schema.add(False)
            failures.append(f"{row['id']}: schema inválido ({type(exc).__name__})")
            continue
        schema.add(True)

        tx = resp.transaction
        got_type = tx.type.value if tx and tx.type else None
        got_amount = tx.amount if tx else None
        got_date = tx.occurred_on.isoformat() if tx and tx.occurred_on else None

        checks = {
            intent: resp.intent.value == row["expected_intent"],
            type_acc: got_type == row["expected_type"],
            amount: _amount_eq(got_amount, row["expected_amount"]),
            date_acc: got_date == row["expected_date"],
            missing: set(resp.missing_fields) == set(row["expected_missing_fields"]),
        }
        for metric, ok in checks.items():
            metric.add(ok)
        wrong = [m.name for m, ok in checks.items() if not ok]
        if wrong:
            failures.append(f"{row['id']}: {', '.join(wrong)}")

    metrics = [intent, type_acc, amount, date_acc, missing, schema]
    print_report(f"transaction_parsing ({provider_name}, n={len(rows)})", metrics, failures)

    # El schema debe validar siempre; el resto son métricas de calidad informativas.
    return 0 if schema.rate == 1.0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluación de parsing de movimientos")
    parser.add_argument("--provider", default="mock", help="Proveedor de IA (default: mock)")
    args = parser.parse_args()
    sys.exit(run(args.provider))


if __name__ == "__main__":
    main()
