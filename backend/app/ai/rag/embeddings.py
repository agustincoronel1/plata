"""Proveedores de embeddings. Mock determinístico por defecto; real desacoplado y perezoso.

El mock produce un vector reproducible por bolsa-de-palabras hasheada + normalización L2:
textos con palabras en común quedan cerca por coseno. Sirve para dev/tests/evals sin coste
ni red. El proveedor real (OpenAI) se importa de forma perezosa y falla solo al usarse sin
API key.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Any, Protocol

MOCK_EMBEDDING_MODEL = "mock-embedding-v1"
EMBEDDING_VERSION = "v1"


def _tokens(text: str) -> list[str]:
    lowered = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    return re.findall(r"[a-z0-9]+", stripped)


class EmbeddingProvider(Protocol):
    model: str

    def embed(self, text: str) -> list[float]: ...


class MockEmbeddingProvider:
    """Embedding determinístico por bolsa-de-palabras hasheada (sin coste)."""

    model = MOCK_EMBEDDING_MODEL

    def __init__(self, dimension: int) -> None:
        self._dim = dimension

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _tokens(text):
            digest = hashlib.md5(token.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


class OpenAIEmbeddingProvider:  # pragma: no cover - requiere red y API key
    def __init__(self, settings: Any) -> None:
        self.model = settings.ai_embedding_model
        self._settings = settings

    def embed(self, text: str) -> list[float]:
        from app.ai.exceptions import AIProviderUnavailableError

        if not self._settings.ai_api_key:
            raise AIProviderUnavailableError("Embeddings reales no configurados (falta API key).")
        from openai import OpenAI

        client = OpenAI(api_key=self._settings.ai_api_key)
        resp = client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding


def build_embedding_provider(settings: Any) -> EmbeddingProvider:
    if settings.ai_embedding_provider == "openai":
        return OpenAIEmbeddingProvider(settings)
    return MockEmbeddingProvider(settings.ai_embedding_dimension)
