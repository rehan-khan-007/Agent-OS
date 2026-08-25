from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "Agent-OS"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://agentos:agentos@localhost:5432/agentos"
    redis_url: str = "redis://localhost:6379/0"

    openrouter_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    tavily_api_key: str = ""

    # Cloudflare R2 (S3-compatible object storage for document uploads).
    # Empty by default — the worker falls back to treating the queue
    # payload's file_path as a plain local path if R2 isn't configured,
    # so local dev without R2 credentials still works.
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
