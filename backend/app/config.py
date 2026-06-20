from pydantic_settings import BaseSettings
import secrets


class Settings(BaseSettings):
    SECRET_KEY: str = secrets.token_hex(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 60
    TEMP_TOKEN_EXPIRE_MINUTES: int = 5

    DATABASE_URL: str = "sqlite:////data/app.db"

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"

    LIBATION_CLI: str = "/usr/bin/libationcli"
    LIBATION_CONFIG: str = "/config"
    AUDIOBOOKS_DIR: str = "/audiobooks"

    BRIDGE_URL: str = "http://localhost:8001"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
