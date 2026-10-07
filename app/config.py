from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables or .env file.
    Using pydantic-settings guarantees type safety and easy configuration across environments.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "Bulk Certificate Generator API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Base URL for public links (used in QR codes pointing to verification endpoint)
    BASE_URL: str = "http://localhost:8000"

    # Database connection URL (defaults to local SQLite file)
    DATABASE_URL: str = "sqlite:///./certificates.db"

    # Directory where generated PDF certificates are stored
    STORAGE_DIR: Path = Path("storage")

    # Font configuration
    FONT_PATH: Path = Path("app/templates/fonts/DejaVuSans.ttf")
    FONT_NAME: str = "DejaVuSans"


settings = Settings()
