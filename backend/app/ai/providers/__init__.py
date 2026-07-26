"""Proveedores de IA detrás de una interfaz común (mock y real)."""

from app.ai.providers.base import AIProvider, AIProviderResult
from app.ai.providers.mock import MockAIProvider

__all__ = ["AIProvider", "AIProviderResult", "MockAIProvider"]
