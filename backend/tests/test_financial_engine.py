"""Tests unitarios del motor financiero. Funciones puras: no tocan PostgreSQL.

Cubren disponible real, monto diario, proyección de fin de mes, calendario de cuotas y la
simulación de compras. Todo con Decimal y fechas fijas (`as_of`).
"""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.services import financial_engine as fe
from app.services.financial_engine import CommitmentInput, ProfileInput

AS_OF = date(2026, 7, 24)


def profile(
    *,
    balance="620000.00",
    next_amount="1200000.00",
    next_date=date(2026, 8, 1),
    protected="120000.00",
    safety="40000.00",
) -> ProfileInput:
    return ProfileInput(
        current_balance=Decimal(balance),
        next_income_amount=Decimal(next_amount),
        next_income_date=next_date,
        protected_amount=Decimal(protected),
        safety_buffer=Decimal(safety),
    )


def commitment(amount, due, status="pending", recurring=False) -> CommitmentInput:
    return CommitmentInput(
        amount=Decimal(amount), due_date=due, status=status, is_recurring=recurring
    )


# ---------- Disponible real ----------


def test_disponible_real_positivo() -> None:
    coms = [commitment("410000.00", date(2026, 7, 28))]
    s = fe.build_summary(profile(), coms, AS_OF)
    assert s["available_real"] == Decimal("50000.00")
    assert s["spendable_total"] == Decimal("50000.00")
    assert s["deficit_amount"] == Decimal("0")
    assert s["status"] == fe.STATUS_HEALTHY


def test_disponible_real_cero_es_tight() -> None:
    p = profile(balance="300000.00", protected="60000.00", safety="40000.00")
    coms = [commitment("200000.00", date(2026, 7, 28))]
    s = fe.build_summary(p, coms, AS_OF)
    assert s["available_real"] == Decimal("0")
    assert s["spendable_total"] == Decimal("0")
    assert s["status"] == fe.STATUS_TIGHT


def test_disponible_real_negativo_es_deficit() -> None:
    p = profile(balance="100000.00", protected="0", safety="0")
    coms = [commitment("200000.00", date(2026, 7, 28))]
    s = fe.build_summary(p, coms, AS_OF)
    assert s["available_real"] == Decimal("-100000.00")
    assert s["spendable_total"] == Decimal("0")
    assert s["deficit_amount"] == Decimal("100000.00")
    assert s["status"] == fe.STATUS_DEFICIT


def test_current_balance_negativo() -> None:
    p = profile(balance="-50000.00", protected="0", safety="0")
    s = fe.build_summary(p, [], AS_OF)
    assert s["available_real"] == Decimal("-50000.00")
    assert s["deficit_amount"] == Decimal("50000.00")
    assert s["spendable_total"] == Decimal("0")


def test_protected_y_safety_en_cero() -> None:
    p = profile(balance="500000.00", protected="0", safety="0")
    s = fe.build_summary(p, [], AS_OF)
    assert s["available_real"] == Decimal("500000.00")


def test_compromisos_paid_ignorados() -> None:
    coms = [commitment("410000.00", date(2026, 7, 28), status="paid")]
    s = fe.build_summary(profile(), coms, AS_OF)
    assert s["pending_commitments_amount"] == Decimal("0")
    assert s["available_real"] == Decimal("460000.00")


def test_compromisos_cancelled_ignorados() -> None:
    coms = [commitment("410000.00", date(2026, 7, 28), status="cancelled")]
    s = fe.build_summary(profile(), coms, AS_OF)
    assert s["pending_commitments_amount"] == Decimal("0")


def test_compromisos_pending_incluidos() -> None:
    coms = [commitment("100000.00", date(2026, 7, 28))]
    s = fe.build_summary(profile(), coms, AS_OF)
    assert s["pending_commitments_amount"] == Decimal("100000.00")


def test_compromisos_vencidos_pending_incluidos() -> None:
    # due < as_of pero sigue pending: es dinero pendiente, cuenta.
    coms = [commitment("70000.00", date(2026, 7, 10))]
    s = fe.build_summary(profile(), coms, AS_OF)
    assert s["pending_commitments_amount"] == Decimal("70000.00")
    assert s["overdue_commitments_amount"] == Decimal("70000.00")
    assert "Tenés compromisos pendientes vencidos." in s["warnings"]


def test_compromisos_posteriores_al_horizonte_ignorados() -> None:
    # horizonte = next_income_date = 2026-08-01; este vence después.
    coms = [commitment("90000.00", date(2026, 8, 15))]
    s = fe.build_summary(profile(), coms, AS_OF)
    assert s["pending_commitments_amount"] == Decimal("0")


def test_no_resta_transacciones_de_nuevo() -> None:
    # El motor no recibe transacciones: available_real depende solo del saldo,
    # los compromisos y las reservas. current_balance ya es el saldo actual.
    p = profile(protected="0", safety="0")
    s = fe.build_summary(p, [], AS_OF)
    assert s["available_real"] == p.current_balance
    assert "transactions" not in fe.build_summary.__code__.co_varnames


# ---------- Monto diario ----------


def test_monto_diario_fecha_futura_round_down() -> None:
    # spendable 100000, faltan 8 días -> 12500 exacto.
    p = profile(balance="100000.00", protected="0", safety="0")
    s = fe.build_summary(p, [], AS_OF)
    assert s["days_until_income"] == 8
    assert s["daily_safe_to_spend"] == Decimal("12500.00")


def test_monto_diario_round_down_conservador() -> None:
    # spendable 100.00, faltan 3 días -> 33.33 (trunca, no redondea a 33.34).
    p = profile(balance="100.00", protected="0", safety="0", next_date=date(2026, 7, 27))
    s = fe.build_summary(p, [], AS_OF)
    assert s["days_until_income"] == 3
    assert s["daily_safe_to_spend"] == Decimal("33.33")


def test_monto_diario_ingreso_hoy() -> None:
    p = profile(next_date=AS_OF, protected="0", safety="0", balance="80000.00")
    s = fe.build_summary(p, [], AS_OF)
    assert s["days_until_income"] == 1
    assert s["daily_safe_to_spend"] == Decimal("80000.00")


def test_monto_diario_fecha_vencida() -> None:
    p = profile(next_date=date(2026, 7, 1))
    s = fe.build_summary(p, [], AS_OF)
    assert s["days_until_income"] == 0
    assert s["daily_safe_to_spend"] is None
    assert s["status"] == fe.STATUS_INCOMPLETE
    assert "La fecha de tu próximo ingreso ya pasó." in s["warnings"]


def test_monto_diario_fecha_null() -> None:
    p = profile(next_date=None)
    s = fe.build_summary(p, [], AS_OF)
    assert s["days_until_income"] is None
    assert s["daily_safe_to_spend"] is None
    assert s["status"] == fe.STATUS_INCOMPLETE
    assert "No configuraste la fecha de tu próximo ingreso." in s["warnings"]


def test_monto_diario_sin_division_por_cero() -> None:
    # Fecha vencida y null nunca dividen: devuelven None sin error.
    for nd in (None, date(2026, 7, 1)):
        s = fe.build_summary(profile(next_date=nd), [], AS_OF)
        assert s["daily_safe_to_spend"] is None


# ---------- Proyección de fin de mes ----------


def test_forecast_ingreso_dentro_del_mes() -> None:
    p = profile(next_date=date(2026, 7, 30), next_amount="500000.00", protected="0", safety="0")
    f = fe.build_month_end_forecast(p, [], AS_OF)
    assert f["month_end"] == date(2026, 7, 31)
    assert f["income_before_month_end"] == Decimal("500000.00")


def test_forecast_ingreso_fuera_del_mes() -> None:
    p = profile(next_date=date(2026, 8, 1), next_amount="500000.00")
    f = fe.build_month_end_forecast(p, [], AS_OF)
    assert f["income_before_month_end"] == Decimal("0")


def test_forecast_compromiso_vencido_pendiente_incluido() -> None:
    coms = [commitment("40000.00", date(2026, 7, 10))]
    f = fe.build_month_end_forecast(profile(), coms, AS_OF)
    assert f["commitments_before_month_end"] == Decimal("40000.00")


def test_forecast_compromiso_posterior_al_mes_ignorado() -> None:
    coms = [commitment("40000.00", date(2026, 8, 3))]
    f = fe.build_month_end_forecast(profile(), coms, AS_OF)
    assert f["commitments_before_month_end"] == Decimal("0")


def test_forecast_no_resta_reservas_del_saldo() -> None:
    # protected/safety NO se restan del saldo proyectado, solo del margen.
    p = profile(next_date=date(2026, 8, 1), protected="120000.00", safety="40000.00")
    coms = [commitment("100000.00", date(2026, 7, 28))]
    f = fe.build_month_end_forecast(p, coms, AS_OF)
    assert f["projected_month_end_balance"] == Decimal("520000.00")  # 620000 - 100000
    assert f["projected_month_end_margin"] == Decimal("360000.00")  # 520000 - 160000


def test_forecast_margen_negativo() -> None:
    p = profile(
        balance="100000.00", next_date=date(2026, 8, 1), protected="120000.00", safety="40000.00"
    )
    coms = [commitment("30000.00", date(2026, 7, 28))]
    f = fe.build_month_end_forecast(p, coms, AS_OF)
    assert f["projected_month_end_balance"] == Decimal("70000.00")
    assert f["projected_month_end_margin"] == Decimal("-90000.00")


# ---------- Calendario de cuotas ----------


def test_cuota_unica() -> None:
    schedule, regular = fe.build_installment_schedule(Decimal("50000.00"), 1, date(2026, 8, 10))
    assert len(schedule) == 1
    assert schedule[0] == {
        "number": 1,
        "due_date": date(2026, 8, 10),
        "amount": Decimal("50000.00"),
    }
    assert regular == Decimal("50000.00")


def test_cuota_division_exacta() -> None:
    schedule, regular = fe.build_installment_schedule(Decimal("900000.00"), 9, date(2026, 8, 15))
    assert regular == Decimal("100000.00")
    assert all(item["amount"] == Decimal("100000.00") for item in schedule)
    assert sum(item["amount"] for item in schedule) == Decimal("900000.00")


def test_cuota_con_residuo_ajusta_la_ultima() -> None:
    schedule, regular = fe.build_installment_schedule(Decimal("100.00"), 3, date(2026, 8, 15))
    assert regular == Decimal("33.33")
    assert [i["amount"] for i in schedule] == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(i["amount"] for i in schedule) == Decimal("100.00")


@pytest.mark.parametrize(
    ("total", "n"),
    [("100.00", 3), ("999.99", 7), ("1234567.89", 13), ("50.05", 24), ("1.00", 24)],
)
def test_cuota_suma_exacta(total: str, n: int) -> None:
    schedule, _ = fe.build_installment_schedule(Decimal(total), n, date(2026, 8, 15))
    assert sum(i["amount"] for i in schedule) == Decimal(total)
    assert len(schedule) == n


def test_cuota_31_enero_a_febrero() -> None:
    schedule, _ = fe.build_installment_schedule(Decimal("300.00"), 3, date(2026, 1, 31))
    assert [i["due_date"] for i in schedule] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
    ]


def test_cuota_anio_bisiesto() -> None:
    schedule, _ = fe.build_installment_schedule(Decimal("300.00"), 3, date(2028, 1, 31))
    # 2028 es bisiesto: febrero llega hasta el 29.
    assert schedule[1]["due_date"] == date(2028, 2, 29)


def test_cuota_31_a_mes_de_30_dias() -> None:
    schedule, _ = fe.build_installment_schedule(Decimal("200.00"), 2, date(2026, 3, 31))
    assert [i["due_date"] for i in schedule] == [date(2026, 3, 31), date(2026, 4, 30)]


def test_cuota_cambio_de_anio() -> None:
    schedule, _ = fe.build_installment_schedule(Decimal("300.00"), 3, date(2026, 11, 15))
    assert [i["due_date"] for i in schedule] == [
        date(2026, 11, 15),
        date(2026, 12, 15),
        date(2027, 1, 15),
    ]


def test_cuota_maximo_24() -> None:
    schedule, _ = fe.build_installment_schedule(Decimal("2400.00"), 24, date(2026, 8, 15))
    assert len(schedule) == 24
    assert schedule[-1]["due_date"] == date(2028, 7, 15)


# ---------- Simulación ----------


def _simulate(p: ProfileInput, coms, total, n, first):
    return fe.simulate_purchase(p, coms, Decimal(total), n, first, AS_OF)


def test_simulacion_sin_riesgo() -> None:
    p = profile(balance="2000000.00", next_amount="1200000.00", next_date=date(2026, 8, 1))
    result = _simulate(p, [], "300000.00", 3, date(2026, 8, 15))
    assert result["conclusion"] == fe.CONCLUSION_FITS
    assert result["risk_months"] == []
    assert result["installment_count"] == 3


def test_simulacion_con_meses_en_riesgo() -> None:
    p = profile(balance="200000.00", next_amount="0", next_date=None, protected="0", safety="0")
    result = _simulate(p, [], "600000.00", 3, date(2026, 8, 15))
    # Sin ingresos, una compra grande rompe reservas.
    assert result["risk_months_count"] >= 1
    # next_income ausente -> insufficient_data domina sobre breaks.
    assert result["conclusion"] == fe.CONCLUSION_INSUFFICIENT


def test_simulacion_breaks_reserves_con_ingreso() -> None:
    p = profile(
        balance="200000.00",
        next_amount="150000.00",
        next_date=date(2026, 8, 1),
        protected="0",
        safety="0",
    )
    result = _simulate(p, [], "900000.00", 3, date(2026, 8, 15))
    assert result["conclusion"] == fe.CONCLUSION_BREAKS
    assert result["risk_months_count"] >= 1


def test_simulacion_ingreso_recurrente_se_repite() -> None:
    p = profile(
        balance="100000.00",
        next_amount="500000.00",
        next_date=date(2026, 8, 1),
        protected="0",
        safety="0",
    )
    result = _simulate(p, [], "300000.00", 3, date(2026, 8, 15))
    meses_con_ingreso = [m for m in result["months"] if m["income_amount"] > Decimal("0")]
    # Agosto, septiembre, octubre reciben el ingreso recurrente.
    assert len(meses_con_ingreso) >= 3


def test_simulacion_compromiso_recurrente_cada_mes() -> None:
    p = profile(balance="2000000.00", next_amount="1200000.00", next_date=date(2026, 8, 1))
    coms = [commitment("50000.00", date(2026, 7, 26), recurring=True)]
    result = _simulate(p, coms, "300000.00", 3, date(2026, 8, 15))
    # El compromiso recurrente aparece en todos los meses simulados.
    assert all(m["commitment_amount"] >= Decimal("50000.00") for m in result["months"])


def test_simulacion_compromiso_no_recurrente_una_sola_vez() -> None:
    p = profile(balance="2000000.00")
    coms = [commitment("50000.00", date(2026, 8, 20), recurring=False)]
    result = _simulate(p, coms, "300000.00", 3, date(2026, 8, 15))
    meses_con_compromiso = [
        m for m in result["months"] if m["commitment_amount"] == Decimal("50000.00")
    ]
    assert len(meses_con_compromiso) == 1


def test_simulacion_ignora_paid_y_cancelled() -> None:
    p = profile(balance="2000000.00")
    coms = [
        commitment("999999.00", date(2026, 8, 20), status="paid", recurring=True),
        commitment("888888.00", date(2026, 8, 20), status="cancelled"),
    ]
    result = _simulate(p, coms, "300000.00", 3, date(2026, 8, 15))
    assert all(m["commitment_amount"] == Decimal("0") for m in result["months"])


def test_simulacion_comparacion_ahora_vs_mes_siguiente() -> None:
    p = profile(
        balance="500000.00",
        next_amount="400000.00",
        next_date=date(2026, 8, 1),
        protected="0",
        safety="0",
    )
    result = _simulate(p, [], "600000.00", 4, date(2026, 8, 15))
    alt = result["start_next_month"]
    assert alt["first_installment_date"] == date(2026, 9, 15)
    assert set(alt.keys()) == {
        "first_installment_date",
        "risk_months_count",
        "minimum_margin",
        "final_balance",
        "improves_margin",
    }
    assert isinstance(alt["improves_margin"], bool)


def test_simulacion_serializable_a_json() -> None:
    p = profile()
    result = _simulate(
        p,
        [commitment("50000.00", date(2026, 7, 26), recurring=True)],
        "300000.00",
        3,
        date(2026, 8, 15),
    )
    jsonable = fe.to_jsonable(result)
    # No debe lanzar: no quedan Decimal ni date crudos.
    dumped = json.dumps(jsonable)
    assert isinstance(dumped, str)
    assert isinstance(jsonable["total_purchase_amount"], str)
    assert jsonable["first_installment_date"] == "2026-08-15"
    assert isinstance(jsonable["schedule"][0]["due_date"], str)


def test_simulacion_no_modifica_entradas() -> None:
    p = profile()
    coms = [commitment("50000.00", date(2026, 7, 26))]
    saldo_antes = p.current_balance
    fe.simulate_purchase(p, coms, Decimal("300000.00"), 3, date(2026, 8, 15), AS_OF)
    # ProfileInput es frozen y el motor no muta nada.
    assert p.current_balance == saldo_antes
    assert coms[0].amount == Decimal("50000.00")


def test_simulacion_suma_de_cuotas_coincide_con_total() -> None:
    p = profile()
    result = _simulate(p, [], "100.00", 3, date(2026, 8, 15))
    assert sum(i["amount"] for i in result["schedule"]) == Decimal("100.00")
