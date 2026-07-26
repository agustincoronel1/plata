"""Evaluación de selección de tools y necesidad de aprobación (mock, sin DB).

python -m app.ai.evaluators.tool_selection
"""

from __future__ import annotations

import sys
from datetime import date

from app.ai.agent.router import WRITE_INTENTS, plan_tools
from app.ai.agent.schemas import AgentIntent
from app.ai.agent.tools import TOOLS
from app.ai.evaluators._common import Metric, load_jsonl, print_report

DATASET = "tool_selection.jsonl"
AS_OF = date(2026, 7, 24)
MAX_STEPS = 4


def run() -> int:
    selection = Metric("tool_selection_accuracy")
    approval = Metric("approval_required_accuracy")
    arg_validity = Metric("tool_argument_validity")
    max_steps = Metric("max_steps_ok")
    failures: list[str] = []

    for row in load_jsonl(DATASET):
        intent = AgentIntent(row["intent"])
        calls = plan_tools(
            intent,
            row.get("message", ""),
            row.get("args", {}),
            AS_OF,
            row.get("last_simulation"),
        )
        names = [c["name"] for c in calls]
        ok = names == row["expected_tools"]
        selection.add(ok)
        if not ok:
            failures.append(f"{row['intent']}: {names} != {row['expected_tools']}")

        expected_approval = row["expected_approval"]
        got_approval = intent in WRITE_INTENTS and bool(calls)
        approval.add(got_approval == expected_approval)

        max_steps.add(len(calls) <= MAX_STEPS)

        # Los argumentos planificados validan contra el schema Pydantic de cada tool.
        valid = True
        for call in calls:
            try:
                TOOLS[call["name"]].args_model.model_validate(call["arguments"])
            except Exception:  # noqa: BLE001
                valid = False
        arg_validity.add(valid)

    metrics = [selection, approval, arg_validity, max_steps]
    print_report("tool_selection (mock)", metrics, failures)
    return 0 if selection.rate == 1.0 and arg_validity.rate == 1.0 else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
