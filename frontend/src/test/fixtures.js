/** Fixtures compartidas por los tests del dashboard. Reflejan el contrato del backend. */

export const PROFILE = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Agustín Demo',
  currency: 'ARS',
  current_balance: '620000.00',
  next_income_amount: '1200000.00',
  next_income_date: '2026-08-01',
  protected_amount: '120000.00',
  safety_buffer: '40000.00',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

// Resumen del motor financiero coherente con PROFILE y un compromiso de 250000.
export const SUMMARY = {
  as_of: '2026-07-24',
  horizon_date: '2026-08-01',
  current_balance: '620000.00',
  pending_commitments_amount: '250000.00',
  overdue_commitments_amount: '0.00',
  protected_amount: '120000.00',
  safety_buffer: '40000.00',
  available_real: '210000.00',
  spendable_total: '210000.00',
  deficit_amount: '0.00',
  days_until_income: 8,
  daily_safe_to_spend: '26250.00',
  next_income_amount: '1200000.00',
  next_income_date: '2026-08-01',
  status: 'healthy',
  warnings: [],
  // Actividad del mes: ingresos, gastos, ahorro y gasto por categoría (top 5 + "otros").
  month_income_total: '1200000.00',
  month_expenses_total: '70000.00',
  month_savings: '1130000.00',
  previous_month_expenses_total: '100000.00',
  category_summary: [
    { category: 'transporte', amount: '35000.00', percentage: '50.0' },
    { category: 'comida', amount: '21000.00', percentage: '30.0' },
    { category: 'otros', amount: '14000.00', percentage: '20.0' },
  ],
  forecast: {
    month_end: '2026-07-31',
    income_before_month_end: '0.00',
    commitments_before_month_end: '250000.00',
    projected_month_end_balance: '370000.00',
    projected_month_end_margin: '210000.00',
    note: 'Esta proyección utiliza únicamente ingresos y compromisos cargados. No estima gastos variables futuros.',
  },
}

export function makeTransaction(overrides = {}) {
  return {
    id: 'tx-1',
    user_id: PROFILE.id,
    commitment_id: null,
    type: 'expense',
    amount: '20000.00',
    currency: 'ARS',
    category: 'comida',
    description: null,
    occurred_on: '2026-07-20',
    payment_method: null,
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-20T00:00:00Z',
    ...overrides,
  }
}

export function makeCommitment(overrides = {}) {
  return {
    id: 'cm-1',
    user_id: PROFILE.id,
    name: 'Alquiler',
    amount: '250000.00',
    due_date: '2026-08-05',
    category: 'vivienda',
    status: 'pending',
    is_recurring: true,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

// Simulación persistida con un resultado del motor (montos string, fechas ISO).
export function makeSimulation(overrides = {}) {
  return {
    id: 'sim-1',
    user_id: PROFILE.id,
    purchase_name: 'Notebook prueba',
    total_amount: '900000.00',
    installments: 9,
    installment_amount: '100000.00',
    first_installment_date: '2026-08-15',
    created_at: '2026-07-24T12:00:00Z',
    result: {
      total_purchase_amount: '900000.00',
      installment_count: 9,
      regular_installment_amount: '100000.00',
      first_installment_date: '2026-08-15',
      last_installment_date: '2027-04-15',
      schedule: [
        { number: 1, due_date: '2026-08-15', amount: '100000.00' },
        { number: 2, due_date: '2026-09-15', amount: '100000.00' },
      ],
      months: [
        {
          month: '2026-08',
          opening_balance_without_purchase: '620000.00',
          income_amount: '1200000.00',
          commitment_amount: '250000.00',
          closing_balance_without_purchase: '1570000.00',
          purchase_installment_amount: '100000.00',
          closing_balance_with_purchase: '1470000.00',
          margin_without_purchase: '1410000.00',
          margin_with_purchase: '1310000.00',
          breaks_reserve: false,
        },
        {
          month: '2026-09',
          opening_balance_without_purchase: '1570000.00',
          income_amount: '1200000.00',
          commitment_amount: '250000.00',
          closing_balance_without_purchase: '2520000.00',
          purchase_installment_amount: '100000.00',
          closing_balance_with_purchase: '2320000.00',
          margin_without_purchase: '2360000.00',
          margin_with_purchase: '2160000.00',
          breaks_reserve: false,
        },
      ],
      risk_months: [],
      risk_months_count: 0,
      minimum_margin_with_purchase: '1310000.00',
      final_balance_without_purchase: '2520000.00',
      final_balance_with_purchase: '2320000.00',
      conclusion: 'fits_within_reserves',
      assumptions: ['Se asume que tu próximo ingreso se repite todos los meses el mismo día.'],
      start_next_month: {
        first_installment_date: '2026-09-15',
        risk_months_count: 0,
        minimum_margin: '1310000.00',
        final_balance: '2320000.00',
        improves_margin: false,
      },
    },
    ...overrides,
  }
}

// ---------- IA ----------

export function makeParseDraft(overrides = {}) {
  return {
    draft_id: '99999999-9999-4999-8999-999999999999',
    intent: 'create_transaction',
    transaction: {
      type: 'expense',
      amount: '25000.00',
      category: 'transporte',
      description: 'Carga de combustible',
      occurred_on: '2026-07-23',
      payment_method: 'Tarjeta de débito',
      currency: 'ARS',
      source_text: 'Gasté 25 lucas ayer en nafta con débito',
    },
    confidence: '0.96',
    missing_fields: [],
    ambiguities: [],
    explanation: "Interpreté '25 lucas' como $25.000 y 'ayer' como el día anterior.",
    requires_confirmation: true,
    is_confirmable: true,
    prompt_version: 'v1',
    provider: 'mock',
    model: 'mock-transaction-parser-v1',
    latency_ms: 3,
    ...overrides,
  }
}

// Respuesta del copiloto ya presentada: texto corto en es-AR y "cómo lo resolví" humano.
export function makeChatResponse(overrides = {}) {
  return {
    conversation_id: '77777777-7777-4777-8777-777777777777',
    message_id: '66666666-6666-4666-8666-666666666666',
    answer:
      'Podés gastar hasta $210.000 hoy.\n\nEse monto ya contempla tus próximos pagos, el dinero protegido y tu colchón de seguridad.\n\nHasta que cobres, tu límite recomendado es de $26.250 por día.',
    structured_answer: {
      verdict: 'yes',
      headline: 'Podés gastar hasta $210.000 hoy.',
      explanation:
        'Ese monto ya contempla tus próximos pagos, el dinero protegido y tu colchón de seguridad.',
      recommendation: 'Hasta que cobres, tu límite recomendado es de $26.250 por día.',
      details: [],
      how_i_solved_it:
        'Tomé tu saldo de $620.000 y resté $250.000 de compromisos, $120.000 protegidos y $40.000 de colchón. El resultado es un disponible seguro de $210.000.',
    },
    intent: 'dashboard_summary',
    tools_used: [{ name: 'get_financial_summary', arguments: {}, ok: true, duration_ms: 2 }],
    evidence: [],
    assumptions: [],
    requires_approval: false,
    pending_action: null,
    trace_id: 'abc123',
    ...overrides,
  }
}
