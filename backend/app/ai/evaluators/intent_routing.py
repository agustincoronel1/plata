"""Evaluación de la clasificación de intención del copiloto (mock, sin coste, sin DB).

python -m app.ai.evaluators.intent_routing
"""

from __future__ import annotations

import sys

from app.ai.agent.brain import MockAgentBrain
from app.ai.evaluators._common import Metric, load_jsonl, print_report

DATASETS = ["intent_routing.jsonl", "intent_routing_multiturn.jsonl"]


def run() -> int:
    brain = MockAgentBrain()
    intent = Metric("intent_accuracy")
    failures: list[str] = []

    for name in DATASETS:
        for row in load_jsonl(name):
            history = [{"role": "assistant", "content": "cuota"}] if row.get("history") else []
            result = brain.classify(row["message"], history)
            ok = result["intent"].value == row["expected_intent"]
            intent.add(ok)
            if not ok:
                failures.append(
                    f"{row['message'][:40]}: {result['intent'].value} != {row['expected_intent']}"
                )

    print_report("intent_routing (mock)", [intent], failures)
    return 0 if intent.rate == 1.0 else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
