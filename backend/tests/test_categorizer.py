"""Reglas de categorización de gastos: determinísticas, sin IA y sin base de datos."""

import pytest

from app.models.enums import TransactionType
from app.schemas.transaction import TransactionCreate
from app.services.categorizer import (
    EXPENSE_CATEGORIES,
    OTHER_CATEGORY,
    classify_expense,
    is_expense_category,
    resolve_expense_category,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nafta", "transporte"),
        ("Carga de combustible", "transporte"),
        ("Cargué en la YPF", "transporte"),
        ("Uber al centro", "transporte"),
        ("Compra en el supermercado", "comida"),
        ("Pedido por PedidosYa", "comida"),
        ("Verdulería del barrio", "comida"),
        ("Alquiler de agosto", "vivienda"),
        ("Pagué la luz", "servicios"),
        ("Pagué el gas", "servicios"),
        ("Farmacia", "salud"),
        ("Netflix", "suscripciones"),
        ("Zapatillas nuevas", "compras"),
        ("Compré en Mercado Libre", "compras"),
        ("Entradas de cine", "ocio"),
        ("Curso de inglés", "educación"),
        ("Libro de historia", "educación"),
    ],
)
def test_clasifica_por_palabra_clave(text: str, expected: str) -> None:
    assert classify_expense(text) == expected


@pytest.mark.parametrize("text", ["", None, "algo raro", "xyz 123", "no coincide con nada"])
def test_sin_coincidencia_cae_en_otros(text: str | None) -> None:
    assert classify_expense(text) == OTHER_CATEGORY


def test_normaliza_mayusculas_y_acentos() -> None:
    assert classify_expense("ESTACIÓN DE SERVICIO") == "transporte"
    assert classify_expense("  Educación: matrícula  ") == "educación"


def test_no_matchea_palabras_que_solo_contienen_la_clave() -> None:
    # "bar" no debe matchear "barrio" ni "gas" matchear "gasté": el emparejamiento es por
    # palabra completa (o su plural), no por subcadena.
    assert classify_expense("compré en el barrio") == OTHER_CATEGORY
    assert classify_expense("gasté plata") == OTHER_CATEGORY


def test_todas_las_categorias_de_las_reglas_son_validas() -> None:
    assert all(is_expense_category(name) for name in EXPENSE_CATEGORIES)
    assert not is_expense_category("supermercado")


class TestResolucion:
    """Prioridad: categoría explícita válida > reglas > otros."""

    def test_la_categoria_explicita_gana(self) -> None:
        assert resolve_expense_category("compras", "nafta") == "compras"

    def test_acepta_la_explicita_con_otro_formato(self) -> None:
        assert resolve_expense_category("  EDUCACION ", "nafta") == "educación"

    def test_una_categoria_libre_vieja_se_mapea_por_reglas(self) -> None:
        # El historial y los modelos pueden traer texto libre: entra como pista.
        assert resolve_expense_category("supermercado", None) == "comida"
        assert resolve_expense_category("gastronomía", "Café") == "comida"

    def test_sin_categoria_usa_la_descripcion(self) -> None:
        assert resolve_expense_category(None, "Carga de nafta") == "transporte"

    def test_sin_nada_reconocible_cae_en_otros(self) -> None:
        assert resolve_expense_category(None, "algo") == OTHER_CATEGORY


class TestSchema:
    """La categoría queda resuelta en el schema: nunca se guarda un gasto sin categoría."""

    def test_gasto_sin_categoria_se_clasifica(self) -> None:
        tx = TransactionCreate(
            type="expense", amount="15000", description="Nafta", occurred_on="2026-07-20"
        )
        assert tx.category == "transporte"

    def test_gasto_sin_pistas_queda_en_otros(self) -> None:
        tx = TransactionCreate(type="expense", amount="15000", occurred_on="2026-07-20")
        assert tx.category == OTHER_CATEGORY

    def test_gasto_con_categoria_explicita_la_respeta(self) -> None:
        tx = TransactionCreate(
            type="expense",
            amount="15000",
            category="ocio",
            description="Nafta",
            occurred_on="2026-07-20",
        )
        assert tx.category == "ocio"

    def test_el_ingreso_conserva_su_categoria_libre(self) -> None:
        tx = TransactionCreate(
            type="income", amount="1000", category="  Sueldo ", occurred_on="2026-07-20"
        )
        assert tx.type is TransactionType.INCOME
        assert tx.category == "sueldo"
