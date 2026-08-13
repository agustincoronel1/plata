"""Detección determinística de consultas simples, ANTES de entrar al grafo.

Muchas preguntas del copiloto ya tienen su respuesta en PostgreSQL: "cuánto gasté este
mes", "cuál es mi saldo", "cuáles fueron mis últimos gastos". Hacerlas pasar por LangGraph
cuesta latencia, plata y una consulta de la cuota diaria, y agrega una posibilidad de
falla del modelo donde no hacía falta ninguna.

Este módulo decide —sin IA, sin red y sin tocar la base— si un mensaje es una de esas
consultas. Es solo la DETECCIÓN: la ejecución y el texto viven en
`app.services.fast_path_service`, y el enganche en `app.services.ai_chat_service`.

La regla que ordena todo el archivo: **ante la duda, LangGraph**. Un falso negativo
(mandar al agente algo que el fast path podía resolver) cuesta una consulta de cuota. Un
falso positivo (contestar con una plantilla algo que necesitaba análisis) le da a la
persona una respuesta equivocada sobre su plata. Por eso los patrones son cerrados y
enumerados en lugar de generales, y por eso hay una lista de bloqueo que gana siempre.

Los patrones además esquivan a propósito las frases con las que se CREA un movimiento o un
compromiso ("gasté 25 lucas en nafta", "tengo un compromiso que vence pronto"): esas son
escrituras, pasan por aprobación humana y no son asunto del fast path.

No es un NLP: es un atajo para un puñado de frases muy frecuentes en español argentino.
Cualquier cosa que se salga de ahí sigue por el camino de siempre, intacto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.services.categorizer import canonical_expense_category, normalize
from app.services.spending_service import Period, parse_period, strip_period

__all__ = ["FastPathIntent", "FastPathMatch", "Period", "match_fast_path"]


class FastPathIntent(StrEnum):
    """Consultas que el fast path sabe resolver de forma determinística."""

    EXPENSE_TOTAL = "expense_total"
    EXPENSE_BY_CATEGORY = "expense_by_category"
    INCOME_TOTAL = "income_total"
    CURRENT_BALANCE = "current_balance"
    AVAILABLE_AMOUNT = "available_amount"
    RECENT_TRANSACTIONS = "recent_transactions"
    PENDING_COMMITMENTS = "pending_commitments"


@dataclass(frozen=True)
class FastPathMatch:
    """Qué se detectó. Sin datos del usuario: solo la forma de la pregunta."""

    intent: FastPathIntent
    # Solo lo usan los intents de agregación temporal; en el resto queda en None.
    period: Period | None = None
    # Categoría canónica de gastos, solo en EXPENSE_BY_CATEGORY.
    category: str | None = None
    # En compromisos: "cuánto tengo comprometido" (total) vs "cuáles son" (lista).
    wants_total: bool = False
    # En movimientos recientes: "mis últimos gastos" pide solo gastos, "mis últimos
    # movimientos" pide todo. Mezclar un ingreso en una lista de gastos sería un error.
    expenses_only: bool = False


# Largo máximo del mensaje normalizado. Una consulta simple es corta; algo más largo
# casi siempre trae condiciones, comparaciones o varias preguntas juntas.
MAX_MESSAGE_LENGTH = 90

# Si aparece cualquiera de estos, el mensaje va al agente sin más análisis.
#
# Tres familias, todas por el mismo motivo (piden algo que el fast path no sabe hacer):
#   1. Análisis, causas, comparaciones y consejo.
#   2. Períodos y cálculos fuera del alcance de V1 (rangos largos, cuotas, promedios).
#   3. Escrituras y cualquier intento de manipular al agente.
#
# "puedo gastar" y "por dia" están acá a propósito: "¿cuánto puedo gastar hoy?" NO es lo
# mismo que "¿cuánto tengo disponible?". Uno pregunta por un presupuesto diario, que el
# producto calcula con su propia lógica (`daily_safe_to_spend`), y el otro por el
# disponible actual. Confundirlos sería inventarle a la persona una fórmula que no pidió.
#
# Son subcadenas, así que acá van solo tokens largos y distintivos: los cortos y
# ambiguos ("ano" matchearía "mano") viven en `_BLOCKER_WORDS`, que compara por palabra.
_BLOCKERS: tuple[str, ...] = (
    # 1. Análisis y consejo
    "por que",
    "porque",
    "analiz",
    "compar",
    "conviene",
    "deberia",
    "podria",
    "recomend",
    "aconsej",
    "ahorr",
    "tendencia",
    "proyec",
    "estimac",
    "explica",
    "detalle",
    "desglos",
    "promedio",
    "grafico",
    "resumen",
    # 2. Períodos y cálculos fuera de alcance
    "meses",
    "trimestre",
    "semestre",
    "quincena",
    "cuota",
    "simul",
    "invert",
    "presupuesto",
    "puedo gastar",
    "puedo comprar",
    "me alcanza",
    "alcanza para",
    "por dia",
    "diario",
    "al dia",
    "cada dia",
    # 3. Escrituras e inyección
    "registra",
    "anota",
    "agenda",
    "instrucciones",
    "sos un asistente",
)

# Bloqueadores que solo valen como palabra completa. Separados de `_BLOCKERS` porque una
# comparación por subcadena daría falsos positivos ("ano" dentro de "mano" o "verano").
_BLOCKER_WORDS = re.compile(
    r"\b(?:ano|anual|desde|hasta|entre|crea|crear|borra|borrar|elimina|eliminar"
    r"|modifica|modificar|cambia|cambiar|ignora|ignorar)\b"
)

# Conjunciones: unen dos preguntas o dos filtros ("comida y transporte", "hoy y ayer").
# El fast path resuelve una sola cosa por vez, así que cualquiera de estas manda al agente.
_CONJUNCTIONS = re.compile(r"\s(?:y|o|u|e|pero|ademas|tambien)\s")

# Un dígito casi siempre significa un monto, un año o una cantidad, y ninguno de los
# intents de V1 los necesita. Bloquear todo el conjunto es más barato y más seguro que
# distinguir "últimos 5 gastos" de "gasté 25 mil".
_HAS_DIGIT = re.compile(r"\d")


# --- Períodos ---------------------------------------------------------------------
#
# El vocabulario vive en `spending_service`, que es también quien traduce cada período a un
# rango de fechas. Tenerlo en dos lados era el origen de un error silencioso: "el mes
# pasado" no estaba en esta lista, así que caía en el default (el mes en curso) y la
# persona recibía el total del mes equivocado sin ningún aviso.

_TODAY = re.compile(r"\bhoy\b")


# --- Patrones por intención -------------------------------------------------------
#
# Se evalúan en el orden de `_INTENT_PATTERNS` y gana el primero. El orden importa:
# AVAILABLE_AMOUNT va antes que CURRENT_BALANCE porque "cuánto tengo disponible" también
# contiene "cuánto tengo".

_EXPENSE_TOTAL = (
    r"\bcuant[oa]\s+(?:plata\s+|dinero\s+)?gaste\b",
    r"\bcuant[oa]\s+(?:plata\s+|dinero\s+)?(?:llevo|he)\s+gastad[oa]\b",
    r"\bcuant[oa]\s+(?:plata\s+|dinero\s+)?llevo\s+gastando\b",
    r"\bque\s+gaste\b",
    r"\bcuant[oa]s?\s+gastos?\s+tuve\b",
)

_INCOME_TOTAL = (
    r"\bcuant[oa]\s+(?:plata\s+|dinero\s+)?(?:ingrese|cobre|gane|facture)\b",
    r"\bcuant[oa]s?\s+ingresos?\s+tuve\b",
    r"\bcuant[oa]\s+(?:llevo\s+)?(?:ingresado|cobrado|ganado)\b",
    r"\bcuant[oa]\s+me\s+(?:entro|ingreso|pagaron)\b",
    r"\bque\s+(?:ingrese|cobre)\b",
)

_AVAILABLE_AMOUNT = (
    r"\bcuant[oa]\s+(?:plata\s+|dinero\s+)?(?:tengo\s+|me\s+queda\s+)?disponible\b",
    r"\bcuant[oa]\s+puedo\s+usar\b",
    r"\bcuant[oa]\s+tengo\s+libre\b",
    r"\bmi\s+(?:plata|dinero|saldo)\s+disponible\b",
    r"\bdisponible\s+real\b",
    r"\bde\s+cuanto\s+dispongo\b",
)

_CURRENT_BALANCE = (
    r"\bcual\s+es\s+mi\s+saldo\b",
    r"\bque\s+saldo\s+tengo\b",
    r"\bmi\s+saldo\b",
    r"\bsaldo\s+actual\b",
    r"\bcuant[oa]\s+(?:plata|dinero)\s+tengo\b",
    r"\bcuant[oa]\s+tengo\s+ahora\b",
    r"\bcuant[oa]\s+tengo\s+en\s+total\b",
)

_RECENT_TRANSACTIONS = (
    r"\bultim[oa]s\s+(?:gastos|movimientos|consumos|transacciones|compras)\b",
    r"\bque\s+fue\s+lo\s+ultimo\s+que\s+(?:gaste|compre)\b",
    r"\bmostrame\s+(?:mis\s+)?(?:ultimos|ultimas)\b",
    r"\bmis\s+ultim[oa]s\b",
)

# "mis últimos gastos" pide gastos; "mis últimos movimientos", todo. La diferencia importa:
# colar un sueldo en una lista de gastos sería una respuesta equivocada.
_ONLY_EXPENSES = re.compile(r"\b(?:gastos|compras|consumos|gaste|compre)\b")

# Deliberadamente NO alcanza con nombrar un compromiso: "tengo un compromiso que vence
# pronto" y "necesito pagar el alquiler" son altas de compromiso, que el agente resuelve
# pidiendo los datos que faltan y esperando aprobación. Acá se exige la forma de consulta
# (plural, "pendientes", "próximos pagos" o "cuánto tengo comprometido").
_PENDING_COMMITMENTS = (
    r"\bque\s+compromisos\b",
    r"\bcuales\s+(?:son\s+)?(?:mis\s+)?compromisos\b",
    r"\bmis\s+compromisos\b",
    r"\bcompromisos\s+pendientes\b",
    r"\bproximos\s+pagos\b",
    r"\bpagos\s+pendientes\b",
    r"\bcuant[oa]\s+tengo\s+comprometido\b",
    r"\bque\s+vencimientos\s+tengo\b",
)

# El orden es significativo: ver la nota de arriba.
_INTENT_PATTERNS: tuple[tuple[FastPathIntent, tuple[str, ...]], ...] = (
    (FastPathIntent.AVAILABLE_AMOUNT, _AVAILABLE_AMOUNT),
    (FastPathIntent.PENDING_COMMITMENTS, _PENDING_COMMITMENTS),
    (FastPathIntent.RECENT_TRANSACTIONS, _RECENT_TRANSACTIONS),
    (FastPathIntent.INCOME_TOTAL, _INCOME_TOTAL),
    (FastPathIntent.EXPENSE_TOTAL, _EXPENSE_TOTAL),
    (FastPathIntent.CURRENT_BALANCE, _CURRENT_BALANCE),
)

_COMPILED: tuple[tuple[FastPathIntent, tuple[re.Pattern[str], ...]], ...] = tuple(
    (intent, tuple(re.compile(p) for p in patterns)) for intent, patterns in _INTENT_PATTERNS
)

# Intents cuyo resultado depende de un rango de fechas.
_TEMPORAL = (
    FastPathIntent.EXPENSE_TOTAL,
    FastPathIntent.EXPENSE_BY_CATEGORY,
    FastPathIntent.INCOME_TOTAL,
)

# "cuánto tengo comprometido" pide un número; "cuáles son mis próximos pagos", una lista.
_WANTS_TOTAL = re.compile(r"\bcuant[oa]\b")

# La categoría viaja después de "en": "cuánto gasté EN servicios este mes". Se toman hasta
# dos palabras porque el vocabulario no tiene categorías más largas.
_CATEGORY_AFTER_EN = re.compile(r"\ben\s+([a-z]+(?:\s+[a-z]+)?)")


def _period(normalized: str) -> Period:
    """Rango pedido. Sin período explícito, el mes en curso (el default del producto)."""
    return parse_period(normalized) or Period.MONTH


def _without_period(normalized: str) -> str:
    """El mensaje sin las expresiones de período, para poder buscar la categoría.

    Sin esto, "cuánto gasté en el mes" parecería preguntar por una categoría llamada
    "el mes" y terminaría cayendo al agente por una razón inventada.
    """
    return strip_period(normalized)


def _category(text: str) -> str | None:
    """Categoría canónica mencionada tras "en", o None si no es una del vocabulario.

    Se exige una categoría EXACTA de `EXPENSE_CATEGORIES` (ignorando acentos y mayúsculas,
    que es lo que hace `canonical_expense_category`). A propósito no se usan las reglas por
    palabra clave del categorizador: "cuánto gasté en nafta" se parece a transporte, pero
    la persona preguntó por un comercio, no por la categoría, y devolverle el total de
    "transporte" sería contestar otra pregunta. Esa consulta merece la búsqueda híbrida del
    agente, así que se devuelve None y el mensaje sigue de largo.
    """
    for match in _CATEGORY_AFTER_EN.finditer(text):
        phrase = match.group(1)
        # La frase completa primero: el patrón admite dos palabras y hay que probarla
        # antes de quedarse solo con la primera.
        for candidate in (phrase, phrase.split()[0]):
            canonical = canonical_expense_category(candidate)
            if canonical is not None:
                return canonical
    return None


def _is_blocked(normalized: str) -> bool:
    """Motivos por los que ni se intenta clasificar."""
    if not normalized or len(normalized) > MAX_MESSAGE_LENGTH:
        return True
    if _HAS_DIGIT.search(normalized):
        return True
    if _CONJUNCTIONS.search(normalized) or _BLOCKER_WORDS.search(normalized):
        return True
    return any(blocker in normalized for blocker in _BLOCKERS)


def match_fast_path(message: str) -> FastPathMatch | None:
    """Clasifica el mensaje. `None` significa "esto lo resuelve el agente".

    Determinística y pura: no llama al modelo, no toca la base y no depende de la hora.
    Solo mira el texto.
    """
    normalized = normalize(message)
    if _is_blocked(normalized):
        return None

    for intent, patterns in _COMPILED:
        if any(pattern.search(normalized) for pattern in patterns):
            return _build(intent, normalized)
    return None


def _build(intent: FastPathIntent, normalized: str) -> FastPathMatch | None:
    """Completa el match con período y categoría, o lo descarta si algo no cierra."""
    if intent is FastPathIntent.PENDING_COMMITMENTS:
        return FastPathMatch(intent=intent, wants_total=bool(_WANTS_TOTAL.search(normalized)))

    if intent is FastPathIntent.RECENT_TRANSACTIONS:
        return FastPathMatch(intent=intent, expenses_only=bool(_ONLY_EXPENSES.search(normalized)))

    if intent is FastPathIntent.AVAILABLE_AMOUNT:
        # "cuánto tengo disponible hoy" roza el presupuesto diario, que tiene su propia
        # lógica en el motor. No se adivina: va al agente.
        return None if _TODAY.search(normalized) else FastPathMatch(intent=intent)

    if intent is FastPathIntent.EXPENSE_TOTAL:
        # Un "en <algo>" convierte el total en un total por categoría. Si ese algo no es
        # una categoría del vocabulario, la pregunta es más específica de lo que el fast
        # path sabe contestar.
        rest = _without_period(normalized)
        if _CATEGORY_AFTER_EN.search(rest):
            category = _category(rest)
            if category is None:
                return None
            return FastPathMatch(
                intent=FastPathIntent.EXPENSE_BY_CATEGORY,
                period=_period(normalized),
                category=category,
            )

    if intent in _TEMPORAL:
        return FastPathMatch(intent=intent, period=_period(normalized))

    return FastPathMatch(intent=intent)
