"""Tests sobre los metadatos de SQLAlchemy.

No tocan PostgreSQL: inspeccionan la definición de las tablas. Las verificaciones
contra la base real se hacen con el contenedor levantado.
"""

import pytest
from sqlalchemy import CheckConstraint, Date, DateTime, Float, Numeric, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.models import (
    Commitment,
    CommitmentStatus,
    PurchaseSimulation,
    Transaction,
    TransactionType,
    UserProfile,
)

TABLES = Base.metadata.tables

MONEY_COLUMNS = {
    "user_profiles": [
        "current_balance",
        "next_income_amount",
        "protected_amount",
        "safety_buffer",
    ],
    "transactions": ["amount"],
    "commitments": ["amount"],
    "purchase_simulations": ["total_amount", "installment_amount"],
}


def test_existen_las_tablas_esperadas() -> None:
    # Las cuatro tablas financieras + las tablas de IA/RAG + los contadores de uso.
    assert set(TABLES) == {
        "user_profiles",
        "transactions",
        "commitments",
        "purchase_simulations",
        "ai_drafts",
        "transaction_search_documents",
        "ai_daily_usage",
    }


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [(table, column) for table, columns in MONEY_COLUMNS.items() for column in columns],
)
def test_los_montos_usan_numeric_14_2(table_name: str, column_name: str) -> None:
    column_type = TABLES[table_name].columns[column_name].type

    assert isinstance(column_type, Numeric)
    assert column_type.precision == 14
    assert column_type.scale == 2


def test_ninguna_columna_usa_float() -> None:
    for table in TABLES.values():
        for column in table.columns:
            assert not isinstance(column.type, Float), f"{table.name}.{column.name} usa Float"


@pytest.mark.parametrize("table_name", ["transactions", "commitments", "purchase_simulations"])
def test_foreign_key_a_user_profiles_con_cascade(table_name: str) -> None:
    foreign_keys = list(TABLES[table_name].foreign_keys)

    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert fk.column is TABLES["user_profiles"].columns["id"]
    assert fk.parent.name == "user_id"
    assert fk.ondelete == "CASCADE"


def _check_constraint_names(table_name: str) -> set[str]:
    return {
        constraint.name
        for constraint in TABLES[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }


@pytest.mark.parametrize(
    ("table_name", "expected"),
    [
        (
            "user_profiles",
            {
                "ck_user_profiles_next_income_amount_non_negative",
                "ck_user_profiles_protected_amount_non_negative",
                "ck_user_profiles_safety_buffer_non_negative",
                "ck_user_profiles_currency_uppercase",
            },
        ),
        ("transactions", {"ck_transactions_amount_positive"}),
        ("commitments", {"ck_commitments_amount_positive"}),
        (
            "purchase_simulations",
            {
                "ck_purchase_simulations_total_amount_positive",
                "ck_purchase_simulations_installments_positive",
                "ck_purchase_simulations_installment_amount_positive",
            },
        ),
    ],
)
def test_check_constraints_principales(table_name: str, expected: set[str]) -> None:
    assert expected <= _check_constraint_names(table_name)


def test_current_balance_admite_negativos() -> None:
    """Una persona puede estar en rojo: ningún constraint debe impedirlo."""
    constraints = TABLES["user_profiles"].constraints

    for constraint in constraints:
        if isinstance(constraint, CheckConstraint):
            assert "current_balance" not in str(constraint.sqltext)

    assert TABLES["user_profiles"].columns["current_balance"].nullable is False


def test_enums_tienen_los_valores_correctos() -> None:
    assert [member.value for member in TransactionType] == ["income", "expense"]
    assert [member.value for member in CommitmentStatus] == ["pending", "paid", "cancelled"]

    assert set(TABLES["transactions"].columns["type"].type.enums) == {"income", "expense"}
    assert set(TABLES["commitments"].columns["status"].type.enums) == {
        "pending",
        "paid",
        "cancelled",
    }


def test_enums_no_son_nativos_de_postgres() -> None:
    """native_enum=False: se guardan como VARCHAR + CHECK, sin tipo ENUM en PG."""
    for table_name, column_name in [("transactions", "type"), ("commitments", "status")]:
        enum_type = TABLES[table_name].columns[column_name].type
        assert enum_type.native_enum is False
        assert enum_type.create_constraint is True


def test_result_es_jsonb() -> None:
    column = TABLES["purchase_simulations"].columns["result"]

    assert isinstance(column.type, JSONB)
    assert column.nullable is False


def test_identificadores_son_uuid() -> None:
    """Toda tabla con clave sustituta la usa como Uuid.

    `ai_daily_usage` queda afuera a propósito: no tiene `id` porque su clave primaria es
    natural y compuesta (usuario, día, tipo), que es justo lo que habilita el incremento
    atómico del contador con un solo INSERT ... ON CONFLICT.
    """
    for name, table in TABLES.items():
        if name == "ai_daily_usage":
            assert [c.name for c in table.primary_key] == ["user_id", "usage_day", "kind"]
            assert isinstance(table.columns["user_id"].type, Uuid)
            continue
        assert isinstance(table.columns["id"].type, Uuid)


def test_fechas_financieras_usan_date_y_timestamps_llevan_timezone() -> None:
    assert isinstance(TABLES["transactions"].columns["occurred_on"].type, Date)
    assert isinstance(TABLES["commitments"].columns["due_date"].type, Date)
    assert isinstance(TABLES["purchase_simulations"].columns["first_installment_date"].type, Date)

    for table_name in TABLES:
        created_at = TABLES[table_name].columns["created_at"].type
        assert isinstance(created_at, DateTime)
        assert created_at.timezone is True


def test_purchase_simulation_no_tiene_updated_at() -> None:
    """Una simulación es inmutable."""
    assert "updated_at" not in TABLES["purchase_simulations"].columns
    for table_name in ["user_profiles", "transactions", "commitments"]:
        assert "updated_at" in TABLES[table_name].columns


def test_indices_esperados() -> None:
    def index_names(table_name: str) -> set[str]:
        return {index.name for index in TABLES[table_name].indexes}

    assert "ix_transactions_user_id_occurred_on" in index_names("transactions")
    assert "ix_commitments_user_id_due_date_status" in index_names("commitments")
    assert "ix_purchase_simulations_user_id_created_at" in index_names("purchase_simulations")


def test_currency_se_normaliza_a_mayusculas() -> None:
    assert UserProfile(name="Agustín", currency="ars").currency == "ARS"


def test_defaults_de_negocio() -> None:
    assert TABLES["user_profiles"].columns["currency"].default.arg == "ARS"
    assert TABLES["commitments"].columns["status"].default.arg is CommitmentStatus.PENDING
    assert TABLES["commitments"].columns["is_recurring"].default.arg is False


def test_relaciones_declaradas() -> None:
    for relationship_name, target in [
        ("transactions", Transaction),
        ("commitments", Commitment),
        ("purchase_simulations", PurchaseSimulation),
    ]:
        relationship = UserProfile.__mapper__.relationships[relationship_name]
        assert relationship.mapper.class_ is target
        assert relationship.cascade.delete_orphan
        assert relationship.passive_deletes is True
