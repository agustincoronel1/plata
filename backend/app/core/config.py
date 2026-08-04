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

    # Orígenes extra habilitados en CORS, separados por comas. Vacío por defecto: en
    # producción el único origen es `frontend_url`, y cualquier agregado es explícito.
    cors_extra_origins: str = ""

    # Valor de respaldo para desarrollo. Debe coincidir con .env.example, incluido el
    # 127.0.0.1: con `localhost`, en Windows psycopg intenta ::1 primero y duplica el
    # tiempo que tarda /health/db en devolver 503 con la base caída.
    database_url: str = "postgresql+psycopg://plata:change_me@127.0.0.1:5432/plata"

    # --- IA ---
    # El proveedor por defecto es "mock": Plata arranca y funciona sin API key. Un proveedor
    # real mal configurado no se valida acá sino al ejecutar una operación de IA, para que
    # nunca impida arrancar el backend (ver app.ai).
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

    # --- Límite diario de consultas inteligentes ---
    # Protección de costos para una beta pública: acota cuánto puede gastar cada cuenta por
    # día. No es un sistema antifraude. Es UNA sola cuota compartida por todos los canales
    # (copiloto web, interpretación de movimientos y, más adelante, WhatsApp). Se cuentan
    # solo las operaciones que llegan a invocar al proveedor; una validación que falla
    # antes no gasta cuota.
    ai_daily_limit: int = 10
    # A partir de cuántos usos restantes se avisa al usuario que se está quedando sin cuota.
    ai_usage_warning_threshold: int = 3
    # Zona horaria del corte diario: el contador se reinicia a las 00:00 de Argentina, que
    # es cuando la persona espera tener el día nuevo, no a medianoche UTC. Es una sola y
    # está definida acá: no depende de la zona del servidor ni de la del navegador.
    ai_usage_timezone: str = "America/Argentina/Buenos_Aires"

    # --- Autenticación (Supabase Auth) ---
    # Sin valores por defecto reales: el backend arranca igual sin configurarlas, y recién
    # al pedir un endpoint protegido se responde 503 con un mensaje claro. Ninguna de estas
    # variables es un secreto: son la URL del proyecto y los metadatos públicos del token.
    # El backend NUNCA usa la publishable key ni la service_role key.
    supabase_url: str = ""
    # JWKS público del proyecto: de ahí sale la clave con la que se verifica la FIRMA.
    supabase_jwks_url: str = ""
    # Emisor esperado del token (claim `iss`).
    supabase_jwt_issuer: str = ""
    # Audiencia esperada del token (claim `aud`). En Supabase, "authenticated".
    supabase_jwt_audience: str = "authenticated"
    # Vida del JWKS cacheado, en segundos. Ante un `kid` desconocido (rotación de claves)
    # el cliente refresca antes de este plazo; el TTL solo acota el uso de un set viejo.
    supabase_jwks_cache_seconds: int = 600

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Orígenes que el navegador puede usar para llamar a la API.

        Lista cerrada y explícita, nunca `*`: en producción quedan solo `frontend_url` y lo
        que se declare en `cors_extra_origins`.

        En desarrollo se agregan los orígenes locales de Vite. Son varios porque para el
        navegador `localhost` y `127.0.0.1` son orígenes distintos aunque resuelvan a la
        misma máquina, y porque Vite salta al siguiente puerto libre si el 5173 está
        ocupado.
        """
        origins = [self.frontend_url]

        if self.environment == "development":
            origins += [
                f"http://{host}:{port}"
                for host in ("localhost", "127.0.0.1")
                for port in (5173, 5174, 5175)
            ]

        origins += [item.strip() for item in self.cors_extra_origins.split(",") if item.strip()]

        # Sin duplicados y conservando el orden: `frontend_url` primero.
        return list(dict.fromkeys(origins))


settings = Settings()
