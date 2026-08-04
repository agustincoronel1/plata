"""Categorización de gastos por reglas: determinística, sin IA y sin dependencias.

Un gasto siempre termina en una de las categorías de `EXPENSE_CATEGORIES`. La resolución
tiene un orden fijo y explícito:

1. **Categoría explícita válida**: lo que la persona eligió gana siempre.
2. **Reglas por palabra clave**: se busca en el texto disponible (la categoría escrita a
   mano, el concepto y la descripción) la primera coincidencia de `KEYWORDS`.
3. **`otros`**: sin coincidencia no se inventa nada.

El punto 2 es también lo que hace compatible el historial: una categoría vieja de texto
libre ("supermercado", "gastronomía") entra como texto y se mapea al vocabulario fijo.

El emparejamiento es intencionalmente simple, sin regex: el texto se normaliza (minúsculas,
sin acentos) y una palabra clave de un solo término matchea contra una palabra completa —o
su plural en "s"—, mientras que una de varias palabras ("mercado libre") se busca como
subcadena. Así "bar" no matchea "barrio" ni "gas" matchea "gasté".

Los ingresos NO pasan por acá: conservan su categoría de texto libre.
"""

from __future__ import annotations

import unicodedata

OTHER_CATEGORY = "otros"

# Vocabulario cerrado de gastos. No hay subcategorías ni categorías personalizadas: la
# lista se extiende editando esta constante (y `KEYWORDS`), no desde la base de datos.
EXPENSE_CATEGORIES: tuple[str, ...] = (
    "comida",
    "transporte",
    "vivienda",
    "servicios",
    "salud",
    "suscripciones",
    "compras",
    "ocio",
    "educación",
    OTHER_CATEGORY,
)

# Reglas: categoría -> palabras clave. El orden importa (gana la primera categoría que
# matchea), así que las más específicas van antes que las más genéricas.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "transporte": (
        "nafta",
        "combustible",
        "gasoil",
        "ypf",
        "shell",
        "axion",
        "puma",
        "estacion de servicio",
        "colectivo",
        "subte",
        "tren",
        "taxi",
        "remis",
        "uber",
        "cabify",
        "didi",
        "sube",
        "peaje",
        "estacionamiento",
        "cochera",
    ),
    "suscripciones": (
        "netflix",
        "spotify",
        "youtube",
        "disney",
        "hbo",
        "prime video",
        "suscripcion",
        "abono mensual",
    ),
    "vivienda": (
        "alquiler",
        "expensas",
        "inmobiliaria",
        "hipoteca",
    ),
    "servicios": (
        "luz",
        "gas",
        "agua",
        "internet",
        "wifi",
        "telefono",
        "celular",
        "cable",
        "edenor",
        "edesur",
        "metrogas",
        "aysa",
    ),
    "salud": (
        "farmacia",
        "medico",
        "medica",
        "dentista",
        "odontologo",
        "clinica",
        "hospital",
        "obra social",
        "prepaga",
        "kinesiologia",
    ),
    "educación": (
        "curso",
        "cursada",
        "facultad",
        "universidad",
        "colegio",
        "escuela",
        "libro",
        "matricula",
        "capacitacion",
    ),
    "comida": (
        "supermercado",
        "super",
        "comida",
        "almuerzo",
        "cena",
        "desayuno",
        "restaurante",
        "resto",
        "delivery",
        "pedidosya",
        "rappi",
        "almacen",
        "carniceria",
        "verduleria",
        "panaderia",
        "fiambreria",
        "dietetica",
        "kiosco",
        "cafe",
        "cafeteria",
        "coto",
        "carrefour",
        "jumbo",
    ),
    "ocio": (
        "cine",
        "bar",
        "boliche",
        "salida",
        "juego",
        "teatro",
        "recital",
        "streaming de juegos",
        "gimnasio",
        "vacaciones",
    ),
    "compras": (
        "ropa",
        "zapatillas",
        "calzado",
        "indumentaria",
        "mercado libre",
        "electronica",
        "electrodomestico",
        "regalo",
        "mueble",
        "shopping",
    ),
}


def normalize(text: str | None) -> str:
    """Minúsculas, sin acentos y con espacios colapsados. La base de la comparación."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    without_accents = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(without_accents.split())


# Índice normalizado -> valor canónico. Permite aceptar "Educación", "educacion" o
# " EDUCACION " y guardar siempre el mismo valor.
_CANONICAL: dict[str, str] = {normalize(name): name for name in EXPENSE_CATEGORIES}


def is_expense_category(value: str | None) -> bool:
    """True si `value` es una de las categorías permitidas (ignora acentos y mayúsculas)."""
    return normalize(value) in _CANONICAL


def canonical_expense_category(value: str | None) -> str | None:
    """Devuelve la categoría canónica si `value` es una permitida; si no, None."""
    return _CANONICAL.get(normalize(value))


def _matches(haystack: str, words: set[str], keyword: str) -> bool:
    """Una palabra clave compuesta se busca como subcadena; una simple, como palabra."""
    if " " in keyword:
        return keyword in haystack
    return keyword in words or f"{keyword}s" in words


def classify_expense(*texts: str | None) -> str:
    """Categoría del gasto según las reglas. `otros` si nada coincide."""
    haystack = " ".join(normalize(text) for text in texts if text).strip()
    if not haystack:
        return OTHER_CATEGORY

    words = set(haystack.split())
    for category, keywords in KEYWORDS.items():
        if any(_matches(haystack, words, keyword) for keyword in keywords):
            return category
    return OTHER_CATEGORY


def resolve_expense_category(explicit: str | None, *texts: str | None) -> str:
    """Categoría final de un gasto: la explícita válida, la de las reglas o `otros`.

    Una categoría explícita que NO está en la lista (texto libre viejo o escrito a mano)
    no se descarta: entra como una pista más para las reglas.
    """
    canonical = canonical_expense_category(explicit)
    if canonical is not None:
        return canonical
    return classify_expense(explicit, *texts)
