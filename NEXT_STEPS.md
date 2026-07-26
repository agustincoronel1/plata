# NEXT_STEPS — Día 4

Estado: Día 4 implementado con tests offline. No se hizo `git add` ni commit.

## Verificación recomendada

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check

cd ..\frontend
npm run lint
npm run test
npm run build
```

Evaluadores disponibles:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ai.evaluators.transaction_parser
.\.venv\Scripts\python.exe -m app.ai.evaluators.intent_routing
.\.venv\Scripts\python.exe -m app.ai.evaluators.tool_selection
.\.venv\Scripts\python.exe -m app.ai.evaluators.prompt_injection
.\.venv\Scripts\python.exe -m app.ai.evaluators.hybrid_retrieval
.\.venv\Scripts\python.exe -m app.ai.evaluators.grounded_answers
```

## Implementado

- Confirmación human-in-the-loop atómica con `PostgresDraftStore.with_session(...)`: claim del draft, escritura financiera, `confirmed` y commit viajan juntos.
- Operaciones internas `create_transaction_no_commit` y `create_commitment_no_commit`, manteniendo wrappers públicos con commit.
- `OpenAIAgentBrain` sin `NotImplementedError`: structured outputs, loop real `function_call → function_call_output`, allowlist strict, validación Pydantic, `store=False`, timeout/retries configurables y errores seguros.
- LangGraph con `PostgresSaver` en ejecución normal y `MemorySaver` para tests/desarrollo explícito.
- Compromisos desde chat para patrones simples y multi-turn: conserva campos parciales en el checkpoint y pregunta solo lo faltante.
- RAG híbrido aplica `tx_type`, selecciona evidencia relevante antes de agregar, no mezcla gastos e ingresos, y suma de forma determinística por SQL sobre IDs aceptados.
- Frontend: reject real del draft, edición de `description` y `payment_method`, bloqueo de confirmación incompleta y fallback manual conservando texto.

## Pendiente real

- Prueba manual con `AI_PROVIDER=openai`, `AI_API_KEY` local y un modelo real: `RUN_REAL_AI_TESTS=1 .\.venv\Scripts\python.exe -m app.scripts.real_ai_smoke`. Los tests no hacen llamadas reales.
- Resolver la advertencia de compatibilidad `langgraph` / `langgraph-checkpoint-postgres` actualizando versiones cuando el proyecto lo permita.
- Autenticación y multiusuario real: hoy todo sigue aislado por `DEMO_USER_ID`.
- Índice ANN para pgvector si el volumen lo justifica.
- Ampliar extracción de compromisos para más frases naturales sin inventar campos.
- Deploy y configuración productiva de secretos.

## Fuera de alcance

Voz, imágenes, OCR, PDFs, extractos bancarios, multiagente y etapa 2.
