from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación, leída desde el entorno o desde backend/.env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Plata API"
    app_version: str = "0.1.0"
    environment: str = "development"
    frontend_url: str = "http://localhost:5173"
    api_v1_prefix: str = "/api/v1"

    # Valor de respaldo para desarrollo. Debe coincidir con .env.example, incluido el
    # 127.0.0.1: con `localhost`, en Windows psycopg intenta ::1 primero y duplica el
    # tiempo que tarda /health/db en devolver 503 con la base caída.
    database_url: str = "postgresql+psycopg://plata:change_me@127.0.0.1:5432/plata"

    # --- IA (Día 4) ---
    # El proveedor por defecto es "mock": Plata arranca y funciona sin API key. La
    # validación de un proveedor real mal configurado NO ocurre acá (no debe impedir que
    # el backend arranque), sino recién al ejecutar una operación de IA (ver app.ai).
    ai_provider: str = "mock"
    ai_model: str = "mock-transaction-parser-v1"
    # Sin valor por defecto real: una clave vacía es válida mientras el proveedor sea mock.
    ai_api_key: str = ""
    ai_timeout_seconds: int = 20
    ai_max_retries: int = 1
    # Por defecto NO se loguea el contenido (texto del usuario, montos, descripciones).
    ai_log_content: bool = False
    # Vida de un borrador temporal (15 minutos).
    ai_draft_ttl_seconds: int = 900
    # Store de borradores: "postgres" (persistente, ejecución normal) o "memory" (dev/tests
    # sin base). Los tests inyectan su propio store, así que este valor no los afecta.
    ai_draft_store: str = "postgres"
    # Checkpoints del copiloto LangGraph: "postgres" en ejecución normal, "memory" para
    # tests o desarrollo explícito sin persistencia entre procesos.
    ai_checkpoint_store: str = "postgres"
    ai_agent_max_iterations: int = 5
    ai_rag_vector_max_distance: float = 0.75
    ai_rag_max_evidence: int = 5
    # Proveedor de embeddings para el RAG: "mock" (determinístico, sin coste) u "openai".
    ai_embedding_provider: str = "mock"
    ai_embedding_model: str = "text-embedding-3-small"
    ai_embedding_dimension: int = 1536


settings = Settings()
