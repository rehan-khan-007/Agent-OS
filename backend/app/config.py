from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "Agent-OS"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://agentos:agentos@localhost:5432/agentos"
    redis_url: str = "redis://localhost:6379/0"

    openrouter_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()