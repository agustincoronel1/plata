"""Registro de prompts: carga el contenido desde un archivo y calcula su checksum.

El prompt NO vive como un string enorme dentro del código: vive en un `.md` versionado
(`<identifier>_<version>.md`). El registro lo lee, calcula el SHA-256 del contenido (para
trazabilidad e identificación reproducible de la versión) y lo cachea.

`render(as_of)` sustituye el marcador `{{AS_OF}}` por la fecha de referencia, para que el
modelo resuelva fechas relativas ("ayer") de forma determinista. El checksum se calcula
sobre el archivo original (con el marcador), así que no depende de `as_of`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

AS_OF_MARKER = "{{AS_OF}}"

# Identificador y versión del prompt de parsing de movimientos.
TRANSACTION_PARSER = "transaction_parser"
TRANSACTION_PARSER_VERSION = "v1"


@dataclass(frozen=True)
class Prompt:
    """Un prompt cargado: qué tarea, qué versión, su contenido y su checksum."""

    identifier: str
    version: str
    template: str
    checksum: str

    def render(self, as_of: date) -> str:
        """Devuelve el prompt con la fecha de referencia inyectada."""
        return self.template.replace(AS_OF_MARKER, as_of.isoformat())


@cache
def get_prompt(identifier: str, version: str) -> Prompt:
    """Carga (cacheado) el prompt `<identifier>_<version>.md` con su checksum SHA-256."""
    path = _PROMPTS_DIR / f"{identifier}_{version}.md"
    template = path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return Prompt(
        identifier=identifier,
        version=version,
        template=template,
        checksum=checksum,
    )
