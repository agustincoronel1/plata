"""Utilidades compartidas por los evaluadores offline.

Cada evaluador lee un dataset JSONL versionado, corre casos con el proveedor MOCK por
defecto (sin coste, determinístico), calcula métricas reales y sale con código distinto de
cero si algo se rompe. No se inventan porcentajes: todo sale de ejecutar los datasets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import settings

# Los evaluadores son offline por contrato: miden reglas determinísticas, no la calidad de un
# proveedor pago. Con AI_PROVIDER=openai en backend/.env, indexar o clasificar
# dispararía llamadas reales y facturadas al correr un eval. Forzar mock acá —el módulo que
# todos importan— mantiene la promesa del docstring pase lo que pase en el entorno local.
# La validación con el proveedor real vive solo en app/scripts/real_ai_smoke.py.
settings.ai_provider = "mock"
settings.ai_model = "mock-transaction-parser-v1"
settings.ai_api_key = ""
settings.ai_embedding_provider = "mock"

EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"

# Dueño de los datos que siembran y consultan los evaluadores. Es propio del banco de
# pruebas y no coincide con ningún usuario real ni con el perfil demo del seed: los evals
# corren en una transacción que se revierte y no deben depender de nada sembrado afuera.
# Se pasa siempre de forma explícita; ninguna función del backend lo toma por defecto.
EVAL_USER_ID = UUID("44444444-4444-4444-8444-444444444444")


def load_jsonl(name: str) -> list[dict[str, Any]]:
    path = EVALS_DIR / name
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class Metric:
    """Un contador acierto/total con formato de porcentaje."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.hits = 0
        self.total = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.hits += 1 if ok else 0

    @property
    def rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.name}: {self.hits}/{self.total} ({self.rate:.0%})"


def print_report(title: str, metrics: list[Metric], failures: list[str]) -> None:
    print(f"\n=== {title} ===")
    for metric in metrics:
        print(f"  {metric}")
    if failures:
        print(f"\n  Casos fallidos ({len(failures)}):")
        for line in failures:
            print(f"    - {line}")
